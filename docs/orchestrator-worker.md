# Orchestrator–Worker 架构

## 1. 目标

项目从单进程能力调用演进为 Orchestrator–Worker 模式。核心约束是：Worker 之间不传递整个 MATLAB 代码库或大段源码，只传递轻量任务信封和持久化 artifact 引用。

```text
用户请求
  -> Orchestrator 创建 Job
  -> TaskQueue 安排阶段任务
  -> WorkerPool 按 worker_kind 路由
  -> Worker 读取输入 artifact，生成输出 artifact
  -> QualityGate 校验阶段输出
  -> SQLiteStateManager 记录状态和结果引用
  -> 下一 Worker 只接收 artifact 路径
```

这种边界让后续 LLM Worker 只加载当前任务所需的函数摘要、文件分片或图子集，避免把整个代码库放入同一个上下文。

## 2. 调度组件

| 组件 | 当前职责 |
| :--- | :--- |
| `Orchestrator` | 创建 Job、生成阶段任务、控制依赖顺序、处理失败边界 |
| `TaskQueue` | 按优先级和稳定序号管理任务 |
| `WorkerPool` | 注册 Worker，并按 `worker_kind` 路由任务 |
| `SQLiteStateManager` | 持久化 Job、Task、状态、payload 和 WorkerResult |
| `ArtifactStore` | 按 Job 隔离保存大型 Pydantic JSON，阻止路径越界 |
| `ConflictResolver` | 管理路径读写声明，拒绝并行写冲突 |
| `QualityGate` | 校验 artifact 存在性、节点唯一性、边和循环引用完整性 |

当前采用进程内线程 WorkerPool 和 SQLite。`parse_chunk` 任务会受 `max_workers` 限制并发执行；任务/Worker 接口不依赖具体执行方式，后续可将 WorkerPool 替换为进程池、远程队列或 Redis consumer，而无需改变 CLI 和领域模型。

## 3. 当前前处理 Worker 划分

| Worker | 状态 | 输入 | 输出 |
| :--- | :--- | :--- | :--- |
| Worker-1 `ScannerAgent` | 已实现 | `project_root` | `file-manifest.json` |
| Worker-2 `ParserAgent` | 已实现 | 文件清单及分片路径 | `parse-chunk-*.json`、`scan-result.json` |
| Worker-3 `AnalyzerAgent` | 已实现 | `scan_result` 引用 | `analysis-result.json` |
| Worker-4～7 | 旧接口预留 | — | 将按 v2 Multi-Agent 规划替换 |

Worker-1 只负责文件发现、排除规则和稳定排序。Orchestrator 按 `parser_chunk_size` 将清单切为固定文件数的分片，Worker-2 并发解析各分片，再校验文件集合并聚合为 `ScanResult`。这种 fan-out/fan-in 边界可在后续替换为按 MATLAB package 或目录分片，而不改变 Analyzer 输入契约。

当前预留 Worker 不会执行伪逻辑；收到任务时会返回明确的 `success=false` 和“尚未实现”诊断。后续不再让单一 Planner 包办语义、注释和结构规划，而是拆为 `SemanticAnnotationAgent`、`ModuleResponsibilityAgent`、`NamingDirectoryAgent`、`ValidationAgent` 和 `NaturalLanguageReportAgent`。详细契约与实施顺序见 [`../项目文件规划.md`](../项目文件规划.md)。

## 4. 当前分析流水线

```text
Job: RUNNING
  |
  +-- Task(scanner, priority=10)
  |     project_root -> ScannerAgent -> file-manifest.json
  |
  +-- N x Task(parser/parse_chunk, priority=20, depends_on=scanner)
  |     manifest + relative file paths
  |       -> ParserAgent（受 max_workers 限制并发）
  |       -> parse-chunk-00000.json ... parse-chunk-N.json
  |
  +-- Task(parser/aggregate, priority=30, depends_on=all parse chunks)
  |     manifest + chunk artifacts -> ParserAgent -> scan-result.json
  |
  +-- Task(analyzer, priority=40, depends_on=aggregate)
        scan_result -> AnalyzerAgent -> analysis-result.json
  |
Job: COMPLETED / FAILED
```

SQLite 中只记录任务 payload 和 artifact 引用。源码内容保留在原 MATLAB 项目，扫描和分析模型保留在 Job artifact 目录。

## 5. 状态模型

Job 状态：

```text
created -> running -> completed
                   -> failed
                   -> waiting_approval  # 阶段二启用
```

Task 状态：

```text
pending -> running -> completed
                   -> failed
        -> blocked
```

查询状态：

```powershell
matlab-refactor status <job-id>
```

`scan` 和 `analyze` 完成后会输出 Job ID。

## 6. 面向大代码库的分片原则

- Scanner 只生成文件清单、类型和基础元数据。
- Parser 按 MATLAB package、目录或文件数量切分任务，不按任意字符截断源码。
- Analyzer 使用聚合后的符号表和边列表，不加载完整函数正文。
- Analyzer/Clustering 按强连通分量、package、目录和 token 预算划分语义工作单元。
- SemanticAnnotationAgent 每次只读取目标函数簇源码及直接邻居摘要，产出带证据和置信度的结构化注解。
- Worker 输出必须结构化并落为 artifact；对话上下文不是状态存储。
- 每一阶段经过 QualityGate 后才能进入下一阶段。

## 7. 后续实现顺序

1. 冻结三级语义注解、AgentProposal、RefactorPlan 和 ChangeSet 数据契约。
2. 实现函数簇划分、token 预算和 SemanticAnnotationAgent fan-out/fan-in。
3. 实现模块职责与命名目录 Agent，以及确定性计划仲裁和人工审批。
4. 实现隔离工作区、快照和原子 ChangeSet Executor。
5. 实现验证、最终报告、Web 代码树和容器部署。
