# 项目结构

本文说明仓库的代码分层、目录职责和主要调用入口。项目包含两个 Web 应用，但二者共用确定性分析、配置、产物存储和领域模型。

## 总体分层

```text
用户入口（CLI / Semantic Web / Migration Web）
                    │
                    ▼
Application Services：组织一次完整用例
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
固定 Pipeline                 Agent Loop
Analysis / Semantics          MATLAB → Python Migration
        │                       │
        └───────────┬───────────┘
                    ▼
Domain Contracts + Workers + Infrastructure
```

依赖方向从入口指向内部能力。`domain/` 不依赖 Web 或具体模型客户端；确定性分析不依赖 LLM；前端不复制后端业务逻辑。

## 仓库目录

```text
matlab_check_agent/
├── apps/                                  # 两个 React/Vite 前端
│   ├── semantic/frontend/                 # 代码树、调用图、源码和三级语义
│   ├── migration/frontend/                # WCC 队列、策略、Observation 和代码预览
│   └── shared/                            # 两端共用的模型/.env 设置面板
├── src/matlab_refactor_agent/             # Python 包
│   ├── analysis/                          # 确定性结构分析公共入口
│   ├── semantics/                         # 三级语义固定流水线
│   ├── migration/                         # 迁移计划、上下文、运行时和断点
│   ├── agents/                            # LLM 语义注释器和 Python 转换器
│   ├── orchestration/                     # LangGraph、调度和状态机
│   ├── workers/                           # 扫描、解析、图、组装和验证工具
│   ├── executors/                         # MATLAB/Python 可注入执行适配器
│   ├── domain/                            # Pydantic 模型、契约、枚举和异常
│   ├── infrastructure/                    # 配置、LLM 客户端、Artifact 和日志
│   ├── application/                       # SemanticService / MigrationService
│   ├── interfaces/                        # CLI 与旧 API 兼容入口
│   └── apps/                              # 两个 FastAPI 后端
├── tests/
│   ├── unit/                              # 算法、领域边界和 API 单元测试
│   ├── integration/                       # CLI、Web、持久化和断点集成测试
│   └── fixtures/matlab_projects/basic/    # 小型可重复 MATLAB 测试工程
├── scripts/
│   ├── start_mvp.py                       # 一键启动 Semantic 前后端
│   └── start_migration.py                 # 一键启动 Migration 前后端
├── docs/                                  # 使用、部署、结构和算法文档
├── Dockerfile                             # Semantic 前后端生产镜像
├── Dockerfile.migration                   # Migration 前后端生产镜像
├── compose.yaml                           # 同时运行两个应用
└── pyproject.toml                         # Python 包、CLI 和测试配置
```

`var/`、前端 `dist/`、`node_modules/`、数据库和缓存均为生成内容，不应提交。

## Python 包详解

### `analysis/`：结构分析入口

`AnalysisPipeline` 串联扫描、解析、依赖分析和结构树生成，只处理可复现的结构事实，不调用模型。

```text
analysis/pipeline.py
  ├── workers/scanning.py
  ├── workers/matlab_parser.py
  ├── workers/dependency_analysis.py
  └── workers/code_tree.py
```

输出包括 `ScanResult`、`AnalysisResult`、调用边、SCC 和结构代码树。算法细节见 [结构图拆分](structure-analysis.md)。

### `semantics/`：三级语义 Pipeline

这是节点和停止条件固定的流水线，不是自主 Agent：

```text
preparation.py   # 按 SCC、邻居和 token 预算生成工作单元
annotator.py     # 调用模型生成函数级结构化注释
quality.py       # 验证覆盖、证据、置信度和边界
aggregator.py    # 函数 → 文件 → 项目聚合
checkpoint.py   # 保存进度并校验源码变化
pipeline.py     # 对外组合入口
```

底层图编排位于 `orchestration/semantic_graph.py`。详见 [三级语义生成](semantic-pipeline.md)。

### `migration/`：MATLAB → Python 会话

```text
planning.py       # 从调用图生成 WCC，并保留 SCC 原子边界
context.py        # 分别构造 ReasonContext 与精简 ActContext
reason_policy.py  # R0/R1/R2 有界上下文和确定性降级
chunking.py       # 按 SCC DAG 和预算拆成 ActChunk
runtime.py        # Build Context / Reason / Act / Assemble / Observe
checkpoint.py     # Job 创建、恢复、源码指纹和产物引用
agent.py          # 只组合 Session、Runtime 与 MigrationGraph
loop.py           # Agent Loop 公共入口
```

状态路由位于 `orchestration/migration_graph.py`，WCC/ActChunk 状态保存在 `orchestration/migration_state.py`。详见 [MATLAB → Python Agent Loop](matlab-to-python-agent-loop.md)。

### `agents/`：模型行为

- `agents/semantic_annotation/`：语义模型组件和旧导入路径兼容层；
- `agents/matlab_to_python/strategy.py`：Reason，输出结构化转换策略；
- `agents/matlab_to_python/agent.py`：Act，依据策略和源码生成 Python 文件；
- `agents/matlab_to_python/repair.py`：根据验证事实修复当前迁移单元。

Agent 不直接扫描仓库，不绕过领域模型写文件，也不能把模型推断当成解析事实。

### `workers/` 与 `executors/`：确定性工具

- `scanning.py` / `matlab_parser.py`：发现和解析 `.m` 文件；
- `dependency_analysis.py` / `graph_output.py` / `graph_view.py`：构图和前端视图裁剪；
- `semantic_context.py`：提取受预算约束的源码上下文；
- `python_project_assembler.py`：隔离组装并阻止路径穿越；
- `contract_validator.py` / `differential_validator.py`：行为契约和数值差分；
- `call_chain_observer.py` / `failure_classifier.py`：汇总检查事实并分类失败。

`executors/` 定义 MATLAB/Python 执行适配边界。没有注入真实 runner 时，系统不会伪造执行结果。

### `domain/`：稳定契约

- `models.py`：扫描、函数和依赖分析结果；
- `code_tree.py`：结构/语义代码树；
- `semantics.py`：函数、文件、项目三级语义及事件；
- `migration.py`：WCC、SCC、ActChunk、策略和迁移状态；
- `contracts.py` / `diagnostics.py`：行为契约与 Observation；
- `exceptions.py`：可公开处理的业务异常。

新增 API 或 Agent 字段时，应先判断它是否属于稳定领域事实，避免把前端显示模型反向写进领域层。

### `infrastructure/`：外部实现

- `config.py`：读取进程环境、`.env` 和默认值；
- `interfaces/api/settings.py`：网页设置白名单、脱敏和原子持久化；
- `artifacts.py`：保存大型 JSON 产物并返回引用；
- `llm/client.py`：OpenAI-compatible 请求、超时和结构化响应；
- `llm/token_budget.py`：模型输入/输出预算；
- `logging.py`：不泄露密钥和完整提示词的运行日志。

### `application/`：用例门面

- `SemanticService`：结构分析、三级语义和语义断点恢复；
- `MigrationService`：分析、计划、迁移新任务和迁移续跑。

CLI 和 Web 都调用这些 Service，核心算法不会在不同入口重复实现。

## 两个 Web 应用

### Semantic App

```text
apps/semantic/frontend
        ↓ REST / SSE
apps/semantic/backend/app.py
        ↓
SemanticService
```

后端负责异步 Job、项目历史、渐进调用图、函数源码切片和语义事件。`storage.py` 使用 SQLite 保存可恢复的 Web 项目快照。

### Migration App

```text
apps/migration/frontend
        ↓ REST / SSE
apps/migration/backend/app.py
        ↓
MigrationJobManager → MigrationService
```

迁移页面复用 Semantic App 保存的静态分析引用，但不重复运行 Scanner/Parser/Analyzer。两个应用通过显式 Artifact 和共享 SQLite 路径协作，不通过前端传输大块源码。

## 测试对应关系

| 变更位置 | 至少应运行 |
| --- | --- |
| `analysis/`、解析器、图 | `tests/unit/parser/`、`tests/unit/analyzer/`、`tests/unit/workers/` |
| `semantics/` | `tests/unit/semantics/`、`test_semantic_graph.py` |
| `migration/`、迁移 Agent | `tests/unit/migration/`、`test_migration_graph.py` |
| CLI | `tests/integration/test_cli.py`、`test_migration_cli.py` |
| Web/API | `tests/unit/interfaces/`、`test_migration_web.py` |
| 全仓变更 | `python -m pytest` 和两个前端 `pnpm build` |

## 扩展约定

- 新结构事实放在确定性 Analysis/Worker 中，不交给模型推断。
- 新语义质量规则优先实现为可重复的 Reviewer 检查。
- 新迁移动作通过 `MigrationRuntime` 和图状态机接入，不能在 API 路由里直接调用模型。
- 大型源码、模型响应和执行结果写 Artifact；图状态只保存小字段和引用。
- 输入 MATLAB 仓库只读；生成 Python 工程必须位于隔离输出目录。
