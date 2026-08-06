# MATLAB Refactor Agent

MATLAB Refactor Agent 用于扫描大型 MATLAB 代码库、提取函数元数据并分析调用关系。当前版本采用 Orchestrator–Worker 架构完成阶段一 CLI，不会修改被分析项目。

## 当前能力

- 递归扫描 `.m` 文件，并支持配置排除目录/路径。
- 使用 `maxx`（Tree-sitter）识别 MATLAB 文件类型和验证解析结果。
- 提取函数名、包限定名、输入、输出、源码行号和调用名称。
- 将脚本纳入调用图，支持 `+package` 函数解析。
- 构建有向调用图，检测循环依赖、孤立对象、入口点和核心函数。
- 将无法唯一解析的调用单独报告，避免错误建立依赖边。
- 支持 Rich 终端函数调用树，标记循环、共享依赖和未解析调用。
- 支持版本化 Graph JSON，为后续 React/vis-network 交互图提供稳定节点/边契约。
- 支持 Mermaid 调用图导出，便于在文档和代码托管平台直接预览。
- 通过 TaskQueue、WorkerPool、SQLite 状态和 artifact 引用运行扫描分析流水线。
- Worker-1 生成稳定文件清单，Worker-2 并发解析文件分片并聚合，Worker-3 分析依赖图。
- Worker 间不传递整个代码库，只传递 Job/Task 元数据和 artifact 路径。

本版本只执行只读分析，尚不包含 LLM 规划、文件重构、MATLAB Engine 验证或 Web UI。

## 环境要求

- Python 3.10 及以上
- Windows、Linux 或 macOS
- 分析阶段不要求安装 MATLAB

`maxx` 当前采用 GPL-3.0 许可证。若后续分发本项目，请先确认整体许可策略。

## 安装

建议在虚拟环境中安装：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 快速开始

扫描项目：

```powershell
matlab-refactor scan D:\path\to\matlab-project
```

分析依赖：

```powershell
matlab-refactor analyze D:\path\to\matlab-project
```

命令完成后会输出 Job ID。查询 Orchestrator 和 Worker 状态：

```powershell
matlab-refactor status <job-id>
```

在终端显示函数调用/依赖树：

```powershell
matlab-refactor analyze D:\path\to\matlab-project --tree
```

导出 Web-ready 图数据和 Mermaid 图：

```powershell
matlab-refactor analyze D:\path\to\matlab-project `
  --graph-json dependency-graph.json `
  --mermaid dependency-graph.mmd
```

三个可视化选项可以组合使用：

```powershell
matlab-refactor analyze D:\path\to\matlab-project `
  --tree `
  --graph-json dependency-graph.json `
  --mermaid dependency-graph.mmd
```

将分析结果写入 JSON：

```powershell
matlab-refactor analyze D:\path\to\matlab-project --json analysis.json
```

输出 JSON 到标准输出，便于 CI 使用：

```powershell
matlab-refactor analyze D:\path\to\matlab-project --json
```

也可以直接运行 Python 模块：

```powershell
python -m matlab_refactor_agent analyze D:\path\to\matlab-project
```

### 独立运行 Worker

每个 Worker 模块都提供 `if __name__ == "__main__"` 演示入口：

```powershell
# Worker-1：发现文件并输出 file-manifest 引用
python -m matlab_refactor_agent.workers.scanner_agent D:\path\to\matlab-project

# Worker-2：按两个文件一片执行解析和聚合
python -m matlab_refactor_agent.workers.parser_agent D:\path\to\matlab-project --chunk-size 2

# Worker-3：读取 Worker-2 输出的 scan-result.json
python -m matlab_refactor_agent.workers.analyzer_agent D:\path\to\scan-result.json

# Worker-4～7：展示尚未实现时的标准安全响应
python -m matlab_refactor_agent.workers.planner_agent
python -m matlab_refactor_agent.workers.executor_agent
python -m matlab_refactor_agent.workers.validator_agent
python -m matlab_refactor_agent.workers.reporter_agent
```

可通过 `--artifact-dir` 指定演示产物目录；默认写入 `var/worker-demos/`。

## 配置

复制 `config.example.yaml` 并按需修改：

```yaml
project:
  exclude_patterns: [".git", "slprj", "build"]
  entry_points: []

logging:
  level: INFO
  format: console

orchestrator:
  state_db: var/refactor-agent.db
  artifact_dir: var/jobs
  max_workers: 4
  parser_chunk_size: 100
```

`parser_chunk_size` 控制每个 Worker-2 解析任务包含的 MATLAB 文件数；独立分片最多按 `max_workers` 并发执行。

通过全局参数加载配置：

```powershell
matlab-refactor --config config.yaml analyze D:\path\to\matlab-project
```

`entry_points` 留空时，默认将调用图中没有入边的对象视为候选入口。手动配置后，仅报告能够在项目内唯一解析的指定入口。

## 输出语义

- `functions`：函数和作为入口节点的脚本。
- `dependencies`：完成作用域消歧后的项目内部有向调用边。
- `cycles`：项目内部已解析调用边形成的循环。
- `orphans`：没有项目内部入边或出边的对象；调用 MATLAB 内置函数不会让它脱离孤立状态。
- `core_functions`：对项目内部调用图计算 PageRank 后排名前 20% 的非孤立对象。
- `entry_points`：手工配置的入口，或自动检测出的零入度对象。
- `unresolved_calls`：MATLAB 内置函数、第三方函数、动态调用或无法唯一消歧的调用。

当前调用提取覆盖常见的 `name(...)` 和 `package.name(...)` 形式。命令式调用、`feval`、动态函数句柄、复杂类方法分派将在后续版本增强。

### Graph JSON 契约

`--graph-json` 输出面向后续 Web API 和图组件的数据，不等同于包含全部分析细节的 `--json`：

- `schema_version`：当前为 `1.0`，用于前端兼容判断。
- `nodes`：稳定 ID、显示标签、文件位置、类型，以及入口/核心/孤立标记。
- `edges`：稳定边 ID、`source`、`target` 和循环边标记。
- `entry_points`、`cycles`、`unresolved_calls`：图交互需要的辅助信息。

后续 vis-network 前端只需将 `source/target` 映射为 `from/to`，不需要重新解析 MATLAB 调用名称。

## 开发与测试

所有新增函数和类必须包含简洁注释头，至少描述以下内容：

- 作用：该定义负责什么。
- 输入：参数或构造数据。
- 输出：返回值、生成对象或副作用。
- 数据流：数据从哪里进入、经过什么处理、流向哪里。

所有 `.py` 文件还必须在文件首部包含标准模块文档头，并置于
`from __future__` 之前：

```python
"""
Description: 当前模块负责什么。
References: 当前模块直接引用的内部模块或关键外部库。
Referenced By: 主要引用当前模块的模块、入口或测试。
"""
```

没有直接引用时应明确写“无”，不能省略字段。新增、移动模块后需要同步更新
`References` 和 `Referenced By`。

```powershell
python -m pytest
python -m pytest --cov=matlab_refactor_agent --cov-report=term-missing
```

项目结构和后续阶段参见 [项目文件规划.md](项目文件规划.md)。架构目标参见 [设计文档.md](设计文档.md)。
多 Worker 的职责、状态流和大代码库分片原则参见 [Orchestrator–Worker 架构](docs/orchestrator-worker.md)。
