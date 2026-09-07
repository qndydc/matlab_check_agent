"""
Description: 共享模型设置、密钥保护、并发持久化与任务配置热加载的回归测试。
References: ModelSettingsStore、两个 Web 应用、load_settings、LLM 工厂。
Referenced By: pytest 自动发现；测试仅使用临时 .env 和假密钥。
"""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from matlab_refactor_agent.infrastructure.config import _ENV_FIELDS, load_settings
from matlab_refactor_agent.infrastructure.llm import factory
from matlab_refactor_agent.interfaces.api.settings import (
    EDITABLE_FIELDS, ModelSettingsStore, SettingsUpdate, install_model_settings_routes,
)


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch) -> Path:
    for name in _ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TEST_MODEL_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "# Keep this comment\n"
        "UNRELATED='untouched # value'\n"
        "MATLAB_REFACTOR_LLM_API_KEY_ENV=TEST_MODEL_KEY\n"
        "TEST_MODEL_KEY=original-fake-key\n"
        "MATLAB_REFACTOR_LLM_MODEL=old-model\n"
        f"MATLAB_REFACTOR_ARTIFACT_DIR={tmp_path.as_posix()}/jobs\n"
        f"MATLAB_REFACTOR_WEB_DB={tmp_path.as_posix()}/web.db\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATLAB_REFACTOR_ENV_FILE", str(path))
    return path


@pytest.fixture
def client(config_file):
    api = FastAPI()
    install_model_settings_routes(api, config_file)
    with TestClient(api) as session:
        yield session


def test_settings_round_trip_preserves_unrelated_fields_and_hides_key(client, config_file):
    original = client.get("/api/settings/llm")
    assert original.headers["cache-control"] == "no-store"
    assert original.json()["api_key_configured"] is True
    assert "original-fake-key" not in original.text
    assert set(original.json()["values"]) == EDITABLE_FIELDS.keys()
    response = client.put("/api/settings/llm", json={
        "revision": original.json()["revision"],
        "values": {"model": "new-model", "base_url": "http://localhost:9900/v1",
                   "temperature": 0.2, "timeout_seconds": 90, "thinking_mode": None,
                   "semantic_max_output_tokens": 8000, "migration_max_output_tokens": 12000,
                   "semantic_token_budget": 16000, "model_context_window_tokens": 64000,
                   "context_safety_margin_tokens": 2048, "sdk_max_retries": 1,
                   "response_retries": 3, "semantic_max_functions_per_unit": 4,
                   "semantic_confidence_threshold": 0.75, "semantic_max_attempts": 3,
                   "max_workers": 6, "max_agents": 5, "semantic_max_agents": 2,
                   "migration_max_agents": 3, "migration_chunk_max_agents": 2},
        "api_key": "",
    })
    assert response.status_code == 200
    assert response.json()["values"]["model"] == "new-model"
    assert response.json()["values"]["thinking_mode"] is None
    values = dotenv_values(config_file)
    assert values["TEST_MODEL_KEY"] == "original-fake-key"
    assert values["UNRELATED"] == "untouched # value"
    assert config_file.read_text(encoding="utf-8").startswith("# Keep this comment\n")
    assert "original-fake-key" not in response.text
    assert "api_key" not in load_settings(config_file).llm.model_dump()
    assert "original-fake-key" not in repr(load_settings(config_file))
    assert "TEST_MODEL_KEY" not in os.environ
    settings = load_settings(config_file)
    assert settings.orchestrator.max_workers == 6
    assert settings.llm.max_agents == 5
    assert settings.llm.semantic_max_agents == 2
    assert settings.llm.migration_max_agents == 3
    assert settings.llm.migration_chunk_max_agents == 2


def test_key_replacement_literal_escaping_and_explicit_clear(client, config_file):
    key = "fake-${UNRELATED}-'quoted'-\\folder-#-\"double\""
    response = client.put("/api/settings/llm", json={"api_key": key})
    assert response.status_code == 200
    assert key not in response.text
    assert load_settings(config_file).llm.api_key.get_secret_value() == key
    assert client.put("/api/settings/llm", json={"api_key": "  "}).status_code == 200
    assert load_settings(config_file).llm.api_key.get_secret_value() == key
    response = client.put("/api/settings/llm", json={"clear_api_key": True})
    assert response.status_code == 200
    assert response.json()["api_key_configured"] is False
    assert response.json()["api_key_source"] == "missing"
    assert load_settings(config_file).llm.api_key.get_secret_value() == ""


@pytest.mark.parametrize("payload", [
    {"values": {"base_url": "file:///tmp/data"}},
    {"values": {"base_url": "https://user:secret-input@example.com"}},
    {"values": {"base_url": "https://example.com?api_key=secret-input"}},
    {"values": {"model": "model\nINJECTED=1"}},
    {"values": {"model": " "}},
    {"values": {"timeout_seconds": -1}},
    {"values": {"temperature": 3}},
    {"values": {"thinking_mode": "unknown"}},
    {"values": {"model_context_window_tokens": 4096}},
    {"values": {"artifact_dir": "other"}},
    {"values": {"api_key_env": "OTHER"}},
    {"values": {"api_key": "secret-input"}},
    {"api_key": "secret-input\nINJECTED=1"},
    {"api_key": "secret-input", "clear_api_key": True},
    {"api_key": {"invalid": "secret-input"}},
    {"unknown": "secret-input"},
])
def test_invalid_updates_are_atomic_and_do_not_echo_secrets(client, config_file, payload):
    before = config_file.read_bytes()
    response = client.put("/api/settings/llm", json=payload)
    assert response.status_code == 422
    assert "secret-input" not in response.text
    assert config_file.read_bytes() == before


def test_process_overrides_are_visible_and_read_only(client, config_file, monkeypatch):
    monkeypatch.setenv("MATLAB_REFACTOR_LLM_MODEL", "external-model")
    monkeypatch.setenv("TEST_MODEL_KEY", "external-fake-key")
    before = config_file.read_bytes()
    view = client.get("/api/settings/llm").json()
    assert view["values"]["model"] == "external-model"
    assert view["overridden_fields"] == ["model"]
    assert view["api_key_source"] == "environment"
    assert "external-fake-key" not in str(view)
    for payload in ({"values": {"model": "ignored"}}, {"api_key": "new"}, {"clear_api_key": True}):
        assert client.put("/api/settings/llm", json=payload).status_code == 409
    assert config_file.read_bytes() == before
    assert client.put("/api/settings/llm", json={"values": {"temperature": 0.5}}).status_code == 200


def test_stale_revision_prevents_overwriting_other_page(client, config_file):
    revision = client.get("/api/settings/llm").json()["revision"]
    ModelSettingsStore(config_file).update(SettingsUpdate(values={"model": "other-page"}))
    response = client.put("/api/settings/llm", json={"revision": revision, "api_key": "stale-key"})
    assert response.status_code == 409
    assert load_settings(config_file).llm.model == "other-page"
    assert load_settings(config_file).llm.api_key.get_secret_value() == "original-fake-key"


def test_concurrent_stores_preserve_independent_updates(config_file):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: ModelSettingsStore(config_file).update(
            SettingsUpdate(values=item)), [{"model": "concurrent"}, {"temperature": 0.8}]))
    assert len(results) == 2
    assert load_settings(config_file).llm.model == "concurrent"
    assert load_settings(config_file).llm.temperature == 0.8
    assert not list(config_file.parent.glob("..env.*.tmp"))


def test_export_duplicates_and_multiline_keys_are_replaced_as_whole_bindings(client, config_file):
    with config_file.open("a", encoding="utf-8") as stream:
        stream.write("export MATLAB_REFACTOR_LLM_MODEL=duplicate\nTEST_MODEL_KEY='first\nsecond'\n")
    assert client.put("/api/settings/llm", json={"values": {"model": "123"}, "api_key": "replacement"}).status_code == 200
    text = config_file.read_text(encoding="utf-8")
    assert text.count("MATLAB_REFACTOR_LLM_MODEL=") == 1
    assert text.count("TEST_MODEL_KEY=") == 1
    assert "second" not in text
    assert load_settings(config_file).llm.model == "123"
    assert load_settings(config_file).llm.api_key.get_secret_value() == "replacement"


def test_other_web_origins_cannot_change_settings(client, config_file):
    before = config_file.read_bytes()
    assert client.put("/api/settings/llm", headers={"Origin": "https://untrusted.example"},
                      json={"values": {"model": "bad"}}).status_code == 403
    assert config_file.read_bytes() == before
    assert client.put("/api/settings/llm", headers={"Origin": "http://127.0.0.1:5174"},
                      json={"values": {"model": "allowed"}}).status_code == 200
    assert client.put("/api/settings/llm", headers={"Origin": "http://127.0.0.1:5184", "Host": "127.0.0.1:5184"},
                      json={"values": {"model": "same-origin-proxy"}}).status_code == 200


def test_llm_factory_uses_per_task_key_snapshot(config_file, monkeypatch):
    old = load_settings(config_file)
    ModelSettingsStore(config_file).update(SettingsUpdate(api_key=SecretStr("next-fake-key")))
    new = load_settings(config_file)
    constructor = Mock()
    monkeypatch.setattr(factory, "OpenAICompatibleLLMClient", constructor)
    factory.create_llm_client(old.llm)
    assert constructor.call_args.kwargs["api_key"] == "original-fake-key"
    factory.create_llm_client(new.llm)
    assert constructor.call_args.kwargs["api_key"] == "next-fake-key"


def test_both_web_apps_share_settings_and_new_service_snapshots(config_file, monkeypatch):
    semantic = import_module("matlab_refactor_agent.apps.semantic.backend.app")
    migration = import_module("matlab_refactor_agent.apps.migration.backend.app")
    from matlab_refactor_agent.apps.migration.backend import jobs

    monkeypatch.setattr(semantic, "SemanticService", lambda settings: settings)
    monkeypatch.setattr(jobs, "MigrationService", lambda settings: settings)
    semantic_manager = semantic.MvpJobManager(env_file=config_file)
    migration_manager = jobs.MigrationJobManager(env_file=config_file)
    try:
        first = TestClient(semantic.create_app(semantic_manager, env_file=config_file))
        second = TestClient(migration.create_app(manager=migration_manager, env_file=config_file))
        old = migration_manager._service_factory()
        assert first.put("/api/settings/llm", json={"api_key": "hot-key", "values": {"model": "hot-model"}}).status_code == 200
        assert second.get("/api/settings/llm").json()["values"]["model"] == "hot-model"
        assert second.get("/api/settings/model").json()["model"] == "hot-model"
        for manager in (semantic_manager, migration_manager):
            fresh = manager._service_factory()
            assert fresh.llm.model == "hot-model"
            assert fresh.llm.api_key.get_secret_value() == "hot-key"
        assert old.llm.model == "old-model"
        assert old.llm.api_key.get_secret_value() == "original-fake-key"
        assert old.orchestrator.artifact_dir == fresh.orchestrator.artifact_dir
    finally:
        semantic_manager._executor.shutdown(wait=True)
        migration_manager.close()


@pytest.mark.parametrize("module", [
    "matlab_refactor_agent.apps.semantic.backend.app",
    "matlab_refactor_agent.apps.migration.backend.app",
    "matlab_refactor_agent.interfaces.api.app",
])
def test_api_entrypoints_import_independently_in_fresh_processes(config_file, module):
    result = subprocess.run(
        [sys.executable, "-c", f"from importlib import import_module; assert import_module('{module}').app"],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_first_save_creates_env_and_disk_failure_keeps_previous_settings(client, config_file, monkeypatch):
    other_path = config_file.parent / "first-run" / ".env"
    store = ModelSettingsStore(other_path)
    store.update(SettingsUpdate(values={"model": "first-model"}))
    assert load_settings(other_path).llm.model == "first-model"

    before = config_file.read_bytes()
    def fail_replace(*_args, **_kwargs):
        raise PermissionError("simulated disk failure")
    monkeypatch.setattr(Path, "replace", fail_replace)
    response = client.put("/api/settings/llm", json={"api_key": "new-fake-key"})
    assert response.status_code == 400
    assert "new-fake-key" not in response.text
    assert config_file.read_bytes() == before
    assert not list(config_file.parent.glob("..env.*.tmp"))


def test_malformed_env_is_not_silently_rewritten(client, config_file):
    with config_file.open("a", encoding="utf-8") as stream:
        stream.write("INVALID='unterminated\n")
    before = config_file.read_bytes()
    assert client.put("/api/settings/llm", json={"api_key": "fake-key"}).status_code == 422
    assert config_file.read_bytes() == before


def test_independent_processes_merge_changes_under_file_lock(config_file):
    commands = [
        "SettingsUpdate(values={'model': 'cross-process-model'})",
        "SettingsUpdate(values={'temperature': 0.35})",
    ]
    processes = [subprocess.Popen([
        sys.executable, "-c",
        "from pathlib import Path; from matlab_refactor_agent.interfaces.api.settings "
        "import ModelSettingsStore, SettingsUpdate; import os; "
        "ModelSettingsStore(Path(os.environ['MATLAB_REFACTOR_ENV_FILE'])).update(" + command + ")",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
    try:
        for process in processes:
            _, error = process.communicate(timeout=20)
            assert process.returncode == 0, error
        assert load_settings(config_file).llm.model == "cross-process-model"
        assert load_settings(config_file).llm.temperature == 0.35
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
