# MATLAB → Python Agent Loop

## 目标与当前边界

迁移模块解决的不是“把一段 MATLAB 翻译成 Python”，而是让大型项目的转换过程可拆分、可验证、可恢复、可人工接管。

当前实现已经具备 WCC/SCC 规划、结构化 Reason/Act、隔离组装、Python 语法与相对 import 检查、失败二分和断点续跑。真实 MATLAB/Python 双端执行、数值差分和项目级发布门禁需要注入相应运行环境；缺少环境时不会伪造成功结果。

## 核心单位

- **WCC（弱连通分量）**：一条完整关联调用链，是主要上下文和最终 Observation 单位。
- **SCC（强连通分量）**：WCC 内循环依赖的不可拆原子。
- **ActChunk**：根据 SCC DAG、token 预算和模块亲和性生成的转换批次。

```text
Project Call Graph
  ├── WCC 1
  │   ├── SCC A → SCC B
  │   └── SCC C（循环簇）
  └── WCC 2
```

WCC 防止只看单函数导致上下文丢失；ActChunk 防止一次模型调用塞入过大的完整 WCC；SCC 防止循环依赖被错误拆开。

## Agent Loop

```text
Select ready WCC
        ↓
Build Context
        ↓
Reason
  ├── convert ─→ Plan ActChunks ─→ Act ─→ Assemble ─→ Observation
  │                                                       │
  │                                                       └──→ Reason
  ├── finish ─→ Freeze WCC ─→ Select next WCC
  ├── rebuild_context ─→ 定向补充上下文 ─→ Reason
  └── manual_review / replan ─→ Manual Review

全部 WCC 结束 → Project Validate → Publish Gate
```

图编排位于 `orchestration/migration_graph.py`，外层 `migration/agent.py` 只组合 Session、Runtime 与 Graph。

## 1. 迁移计划与调度

`MigrationPlanBuilder` 从 Analysis Bundle 构造 WCC，并为每个 WCC 保存：

- `symbol_ids` 和入口函数；
- 内部 SCC 列表与 SCC DAG；
- WCC 之间的依赖；
- 可选 SemanticIndex 引用。

调度器只选择依赖已冻结的 ready WCC。当前 Web 后端按 WCC 串行执行，避免多个大型调用链同时争抢模型预算。

## 2. Build Context

Reason 和 Act 使用不同上下文：

### Reason Context

用于理解和决策，包含项目结构、WCC 依赖、完整目标源码、已冻结依赖接口、可选三级语义和上一轮 Observation。

### Act Context

用于生成代码，只包含当前 ActChunk 的原始完整源码、Reason 输出的 `ConversionStratagem` 和必要的已冻结依赖接口。Act 不重新规划项目结构。

这种隔离防止生成阶段因为看到过多项目元数据而偏离已确定策略。

## 3. Reason 的有界降级

Reason 返回结构化动作：`convert`、`finish`、`rebuild_context` 或 `manual_review`。

- **R0**：在预算内使用完整 WCC 上下文；
- **R1**：按具体 symbol 或相对路径定向补充项目内唯一可解析源码；
- **R2**：保留入口、核心函数和直接依赖完整源码，远端函数使用接口/语义摘要；
- R2 仍超预算或结构化响应持续失败时，使用确定性保守策略；
- 鉴权、断网等传输错误如实失败，不伪装成上下文问题。

项目外、未找到或歧义依赖只记录调用点和未知边界，不能被描述成已验证接口。

## 4. ActChunk 规划与执行

确定性 Chunk Planner 对 SCC DAG 做依赖排序和 greedy packing，优先保持：

- 同一 SCC 不拆分；
- 强依赖和同模块函数靠近；
- 输入/输出 token、函数数和模块数不超过配置上限；
- 依赖 chunk 先冻结，调用者后执行。

同一依赖波次的 chunk 可以并发。失败 chunk 会二分重试，直到可处理的小块或单函数兜底；已冻结的兄弟 chunk 不重复生成。

## 5. Assemble

`PythonProjectAssembler` 将模型返回的文件写入 Job 隔离目录：

```text
generated-python/<WCC>/attempt-<n>/...
```

组装器拒绝：

- `..` 或绝对路径穿越；
- 重复文件和越权覆盖；
- 无效 Python 语法；
- 无法解析的 WCC 内相对 import。

每次尝试使用新目录，旧结果不会被覆盖。

## 6. Observation

默认一定执行：

- Python AST/compile 语法检查；
- WCC 内相对 import 检查；
- 生成文件范围和人工复核声明检查。

环境允许时可注入：

- Python runnable test runner；
- MATLAB runner；
- MATLAB/Python 行为契约和数值差分验证。

Observation 只报告事实，不自行决定“修复还是结束”。Reason 根据事实选择下一步。完整 WCC 的 Observation 通过后才能冻结该 WCC。

## 7. 失败分类与修复

`FailureClassifier` 规则优先区分语法、import、契约、数值、执行环境、模型响应和未知失败。可修复问题回到 Act；缺少环境、外部依赖不明或达到重试上限时转人工复核。

修复只针对当前失败 WCC/ActChunk，不允许模型顺便重写已冻结模块。

## 8. Checkpoint 与续跑

每条 WCC 验证通过后立即标记 `frozen`，并原子更新 `migration-state.json`。Checkpoint 保存：

- 原项目绝对路径和 MATLAB 源码指纹；
- Analysis/Semantic 输入引用；
- WCC、ActChunk、尝试和冻结状态；
- Context、策略、生成代码和 Observation 的 artifact 引用。

`migrate --resume <job-id>` 或 Web“断点续跑”会跳过 frozen WCC，从首个未完成 WCC 的 Build Context 安全边界继续。源码发生变化时拒绝复用旧断点，应创建新任务。

## 主要组件

| 组件 | 文件 | 职责 |
| --- | --- | --- |
| WCC/SCC 规划 | `migration/planning.py` | 构造迁移工作单元和依赖 |
| 上下文 | `migration/context.py` | 构造 Reason/Act 不同视图 |
| Reason 策略 | `agents/matlab_to_python/strategy.py` | 输出结构化转换决策 |
| Act | `agents/matlab_to_python/agent.py` | 生成当前 chunk 的 Python 文件 |
| Chunk Planner | `migration/chunking.py` | SCC DAG 装箱、依赖波次和失败二分 |
| Runtime | `migration/runtime.py` | 组合五个可观察运行步骤 |
| Assemble | `workers/python_project_assembler.py` | 隔离写入和静态安全检查 |
| Observe | `workers/call_chain_observer.py` | 汇总静态、运行和差分事实 |
| Graph | `orchestration/migration_graph.py` | Agent Loop 状态路由 |
| Checkpoint | `migration/checkpoint.py` | 新建、恢复、中断和完成生命周期 |

## 主要产物

```text
matlab-to-python-plan.json
migration-checkpoint.json
migration-state.json
migration-progress.json
reason-context-<WCC>-<n>.json
stratagem-<WCC>-<n>.json
act-context-<WCC>-<attempt>.json
translation-<WCC>-<request>.json
assembly-<WCC>-<attempt>.json
observation-<WCC>-<attempt>.json
generated-python/<WCC>/attempt-<n>/...
```

## 完成的含义

`frozen` 表示该 WCC 已通过当前配置的 Observation；`completed` 表示全部 WCC 已结束。若没有注入 MATLAB 运行和数值差分，这两个状态都不代表与原项目数值等价。正式交付仍需准备真实数据、依赖、入口和回归测试，并通过项目级发布门禁。
