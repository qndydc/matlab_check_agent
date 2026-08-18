"""
Description: 在隔离目录复制 MATLAB 项目并确定性应用已批准的重命名和移动计划。
References: hashlib、shutil、domain.changes、domain.planning。
Referenced By: LangGraphWorkflow 和隔离执行测试。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

from matlab_refactor_agent.domain.changes import AppliedFileChange, ChangeSet
from matlab_refactor_agent.domain.exceptions import ChangeSetExecutionError
from matlab_refactor_agent.domain.planning import RefactorOperation, RefactorPlan

CHANGESET_MARKER = ".matlab-refactor-changeset.json"


class ChangeSetExecutor:
    """作用：只读源项目并原子发布新代码库；输入：已批准计划；输出：可审计 ChangeSet。"""

    def __init__(self, output_dir: Path, exclude_patterns: list[str]) -> None:
        self._output_dir = output_dir.expanduser().resolve()
        self._exclude_patterns = sorted({".git", "__pycache__", *exclude_patterns})

    def execute(
        self,
        *,
        job_id: str,
        source_root: Path,
        plan: RefactorPlan,
        attempt: int = 0,
    ) -> ChangeSet:
        if not job_id.isalnum():
            raise ChangeSetExecutionError("Job ID 不安全")
        source = source_root.expanduser().resolve()
        output_name = job_id if attempt == 0 else f"{job_id}-repair-{attempt}"
        output = (self._output_dir / output_name).resolve()
        self._validate_roots(source, output)
        source_hash_before, _ = self.snapshot(source)
        plan_hash = self._model_hash(plan)
        if output.exists():
            return self._load_existing(
                output, plan_hash, source_hash_before, attempt
            )

        self._output_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{job_id}-", dir=self._output_dir)
        ).resolve()
        shutil.rmtree(staging)
        try:
            shutil.copytree(
                source,
                staging,
                ignore=shutil.ignore_patterns(*self._exclude_patterns),
            )
            replacements = self._replacement_map(plan.operations)
            changed_files = self._rewrite_matlab_files(staging, replacements)
            file_changes = self._apply_moves(staging, plan.operations)
            changed_files = sorted(
                self._final_path(path, plan.operations)
                for path in changed_files
            )
            source_hash_after, _ = self.snapshot(source)
            if source_hash_after != source_hash_before:
                raise ChangeSetExecutionError(
                    "执行期间源项目发生变化，已中止发布"
                )
            output_hash, output_files = self.snapshot(staging)
            change_set = ChangeSet(
                job_id=job_id,
                attempt=attempt,
                source_root=str(source),
                output_root=str(output),
                plan_hash=plan_hash,
                source_tree_hash_before=source_hash_before,
                source_tree_hash_after=source_hash_after,
                output_tree_hash=output_hash,
                copied_file_count=len(output_files),
                changed_matlab_files=changed_files,
                file_changes=file_changes,
            )
            (staging / CHANGESET_MARKER).write_text(
                change_set.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            staging.replace(output)
            return change_set
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def _validate_roots(self, source: Path, output: Path) -> None:
        if not source.is_dir():
            raise ChangeSetExecutionError(f"源项目目录不存在: {source}")
        if self._output_dir == source or self._output_dir.is_relative_to(source):
            raise ChangeSetExecutionError("output_dir 必须位于源项目目录之外")
        if output == source or output.is_relative_to(source):
            raise ChangeSetExecutionError("隔离输出不能位于源项目目录内")

    def _load_existing(
        self,
        output: Path,
        plan_hash: str,
        source_hash: str,
        attempt: int,
    ) -> ChangeSet:
        marker = output / CHANGESET_MARKER
        if not marker.is_file():
            raise ChangeSetExecutionError(f"输出目录已存在且不可重放: {output}")
        try:
            change_set = ChangeSet.model_validate_json(
                marker.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ChangeSetExecutionError("现有 ChangeSet 标记无效") from exc
        output_hash, _ = self.snapshot(
            output, excluded={CHANGESET_MARKER}
        )
        if (
            change_set.plan_hash != plan_hash
            or change_set.attempt != attempt
            or change_set.source_tree_hash_before != source_hash
            or change_set.output_tree_hash != output_hash
        ):
            raise ChangeSetExecutionError("现有输出与当前计划或源项目不一致")
        return change_set

    @staticmethod
    def snapshot(
        root: Path, excluded: set[str] | None = None
    ) -> tuple[str, dict[str, str]]:
        excluded = excluded or set()
        files: dict[str, str] = {}
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            is_junction = getattr(path, "is_junction", lambda: False)()
            if path.is_symlink() or is_junction:
                raise ChangeSetExecutionError(f"不支持链接路径: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in excluded:
                continue
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        digest = hashlib.sha256()
        for relative, file_hash in files.items():
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_hash.encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest(), files

    @staticmethod
    def _model_hash(plan: RefactorPlan) -> str:
        payload = plan.model_dump_json(exclude_none=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _rewrite_matlab_files(
        self, root: Path, replacements: dict[str, str]
    ) -> list[str]:
        if not replacements:
            return []
        pattern = re.compile(
            r"\b(?:"
            + "|".join(
                re.escape(name)
                for name in sorted(replacements, key=len, reverse=True)
            )
            + r")\b"
        )
        changed: list[str] = []
        for path in sorted(root.rglob("*.m")):
            text = path.read_text(encoding="utf-8")
            rewritten_lines: list[str] = []
            in_block_comment = False
            for line in text.splitlines(keepends=True):
                stripped = line.strip()
                if in_block_comment:
                    rewritten_lines.append(line)
                    if stripped.startswith("%}"):
                        in_block_comment = False
                    continue
                if stripped.startswith("%{"):
                    in_block_comment = True
                    rewritten_lines.append(line)
                    continue
                rewritten_lines.append(
                    self._rewrite_line(line, pattern, replacements)
                )
            rewritten = "".join(rewritten_lines)
            if rewritten == text:
                continue
            path.write_text(rewritten, encoding="utf-8", newline="")
            changed.append(path.relative_to(root).as_posix())
        return changed

    @staticmethod
    def _rewrite_line(
        line: str, pattern: re.Pattern[str], replacements: dict[str, str]
    ) -> str:
        output: list[str] = []
        code_start = 0
        index = 0
        while index < len(line):
            char = line[index]
            if char == "%":
                output.append(
                    pattern.sub(lambda match: replacements[match.group()], line[code_start:index])
                )
                output.append(line[index:])
                return "".join(output)
            if char == '"' or (
                char == "'" and ChangeSetExecutor._starts_string(line, index)
            ):
                output.append(
                    pattern.sub(lambda match: replacements[match.group()], line[code_start:index])
                )
                end = ChangeSetExecutor._string_end(line, index, char)
                output.append(line[index:end])
                index = end
                code_start = end
                continue
            index += 1
        output.append(
            pattern.sub(lambda match: replacements[match.group()], line[code_start:])
        )
        return "".join(output)

    @staticmethod
    def _starts_string(line: str, index: int) -> bool:
        previous = line[:index].rstrip()
        return not previous or previous[-1] in "([{,=:+-*/\\^~<>&|;"

    @staticmethod
    def _string_end(line: str, start: int, quote: str) -> int:
        index = start + 1
        while index < len(line):
            if line[index] != quote:
                index += 1
                continue
            if index + 1 < len(line) and line[index + 1] == quote:
                index += 2
                continue
            return index + 1
        return len(line)

    def _apply_moves(
        self, root: Path, operations: list[RefactorOperation]
    ) -> list[AppliedFileChange]:
        grouped: dict[str, list[RefactorOperation]] = defaultdict(list)
        for operation in operations:
            grouped[operation.source_path].append(operation)
        changes: list[AppliedFileChange] = []
        moves: dict[str, str] = {}
        for source, items in sorted(grouped.items()):
            targets = {item.target_path for item in items}
            if len(targets) != 1:
                raise ChangeSetExecutionError(f"源文件存在多个目标: {source}")
            target = next(iter(targets))
            self._safe_relative(source)
            self._safe_relative(target)
            changes.append(
                AppliedFileChange(
                    source_path=source,
                    target_path=target,
                    symbol_ids=sorted(item.symbol_id for item in items),
                    replacements={
                        self._symbol_name(item.symbol_id): item.proposed_name
                        for item in items
                        if self._symbol_name(item.symbol_id) != item.proposed_name
                    },
                )
            )
            if source != target:
                moves[source] = target
        sources = set(moves)
        for source, target in moves.items():
            source_path = root / PurePosixPath(source)
            target_path = root / PurePosixPath(target)
            if not source_path.is_file():
                raise ChangeSetExecutionError(f"计划源文件不存在: {source}")
            if target_path.exists() and target not in sources:
                raise ChangeSetExecutionError(f"目标文件已存在: {target}")
        if not moves:
            return changes
        move_temp = Path(tempfile.mkdtemp(prefix=".moves-", dir=root.parent))
        try:
            staged: dict[str, Path] = {}
            for index, source in enumerate(sorted(moves)):
                temporary = move_temp / f"{index:08d}.m"
                (root / PurePosixPath(source)).replace(temporary)
                staged[source] = temporary
            for source, target in sorted(moves.items()):
                destination = root / PurePosixPath(target)
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged[source].replace(destination)
            for directory in sorted(
                (item for item in root.rglob("*") if item.is_dir()),
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                if not any(directory.iterdir()):
                    directory.rmdir()
        finally:
            if move_temp.exists():
                shutil.rmtree(move_temp)
        return changes

    def _replacement_map(
        self, operations: list[RefactorOperation]
    ) -> dict[str, str]:
        replacements: dict[str, str] = {}
        for operation in operations:
            old = self._symbol_name(operation.symbol_id)
            new = operation.proposed_name
            existing = replacements.get(old)
            if existing is not None and existing != new:
                raise ChangeSetExecutionError(f"符号重命名冲突: {old}")
            if old != new:
                replacements[old] = new
        return replacements

    @staticmethod
    def _symbol_name(symbol_id: str) -> str:
        return symbol_id.rsplit(".", 1)[-1]

    @staticmethod
    def _safe_relative(value: str) -> None:
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or path.is_absolute()
            or ".." in path.parts
            or any(":" in part for part in path.parts)
        ):
            raise ChangeSetExecutionError(f"计划路径不安全: {value}")

    @staticmethod
    def _final_path(
        path: str, operations: list[RefactorOperation]
    ) -> str:
        moves = {
            item.source_path: item.target_path
            for item in operations
            if item.source_path != item.target_path
        }
        return moves.get(path, path)
