# 独立应用

仓库包含两个产品应用。代码层分为 Analysis Pipeline、Semantic Pipeline 和 Migration Agent；产品层仍保持两个独立前后端。

```text
MATLAB repository
       ↓
Deterministic Analysis Core
scan / parse / symbols / call graph / SCC index
       ↓
       ├── semantic/   已完成：三级语义生成与自检 Pipeline
       └── migration/  主力开发：MATLAB 到 Python Agent
```

## Semantic App

- 前端：`apps/semantic/frontend`
- 后端：`matlab_refactor_agent.apps.semantic.backend`
- 端口：前端 `5173`，后端 `8000`
- 启动：`python scripts/start_mvp.py`

## Migration App

- 前端：`apps/migration/frontend`
- 后端：`matlab_refactor_agent.apps.migration.backend`
- 端口：前端 `5174`，后端 `8001`
- 启动：`python scripts/start_migration.py`

迁移前端提供同一项目工作台内的 `Analysis / Migration` 双视图：Analysis 查看 WCC/SCC、入口和可选三级语义；Migration 查看 WCC 队列、Reason、Observation、attempt 与生成文件。Web 已接通 MigrationService，支持新建任务、查看历史和续跑未完成 WCC；不额外建立一套 Agent。

迁移 Agent 已提供独立 CLI，不依赖迁移前端：

```powershell
matlab-refactor migrate D:\path\to\matlab-project
matlab-refactor migrate --resume <job-id>
```

CLI 会为每个迁移 Job 保存 WCC checkpoint。恢复时跳过 `frozen` 调用链，
从首个未完成 WCC 的 Build Context 安全边界继续；MATLAB 源码发生变化时拒绝
复用旧断点。该能力已在核心、CLI 和 Web 接通。默认检查仅限语法与相对 import，不等于 MATLAB 数值验证。

完整的安装、配置、全部 CLI 和两套前后端启动方法见 [使用教程](../docs/使用教程.md)。
