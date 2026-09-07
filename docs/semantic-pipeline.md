# 三级语义生成算法

## 为什么需要三级语义

调用图能说明“谁调用谁”，但不能直接说明函数负责什么、文件在模块中的角色，以及整个项目如何使用。Semantic Pipeline 在不修改结构事实的前提下生成：

1. **函数级**：用途、输入输出、关键行为、证据与置信度；
2. **文件级**：文件职责和包含的函数关系；
3. **项目级**：项目目标、主要入口、使用方式和模块协作。

它是调度和停止条件固定的 LLM Pipeline，不是自主 Agent。

## 总体流程

```text
Analysis Bundle
  → SemanticPreparation
  → 选择工作单元
  → 生成函数注释
  → 确定性质量检查
      ├── 通过：保存并选择下一单元
      ├── 不通过：携带反馈有限重试
      └── 达到上限：标记人工复核
  → 文件级聚合
  → 项目级聚合
  → SemanticIndex + Semantic Code Tree
```

公共入口为 `matlab_refactor_agent.semantics.SemanticAnnotationPipeline`。

## 1. 工作单元准备

`SemanticPreparation` 读取结构分析结果，将函数组织成受控上下文：

- SCC 循环簇保持整体；
- 补充直接调用邻居和必要接口；
- 优先入口、核心和高连接函数；
- 按 token 预算和最大函数数装箱；
- 单个超大函数在硬上限内允许独立成单元，超过硬上限则明确失败。

准备结果保存为 artifact，模型不能自行改变工作单元成员。

## 2. 函数级生成

Annotator 只接收当前单元的有限源码、结构事实和上一轮质量反馈，输出结构化 `FunctionAnnotation`：

```text
symbol_id
file_path + start_line + end_line
summary / responsibility
source evidence
confidence
```

证据必须指向当前扫描项目中的实际源码范围。模型给出的 symbol、路径和行号会在进入聚合前重新校验。

## 3. 自检与有限重试

`SemanticQualityReviewer` 执行可重复的规则检查：

- 单元内函数是否全部覆盖；
- symbol 是否唯一，是否出现簇外结果；
- summary 是否为空；
- 源码证据是否存在；
- 文件、行号和函数边界是否一致；
- confidence 是否达到配置阈值。

失败项转换成精确 `quality_feedback` 交给下一次生成。模型不能仅提高 confidence 来绕过缺失证据。重试达到上限后进入 `manual_review`，不会无限循环。

## 4. 文件级与项目级聚合

所有可接受函数注释准备完成后：

1. 按文件聚合函数职责，生成 `FileAnnotation`；
2. 文件聚合可按配置并发，但输出顺序固定；
3. 基于结构入口、文件职责和冲突信息生成 `ProjectAnnotation`；
4. 组合成唯一的 `SemanticIndex`。

结构树和语义树分开保存：`structural-code-tree.json` 不会被语义结果覆盖。

## 5. LangGraph 状态机

```text
initialize
    ↓
select_unit ───────────────→ aggregate → end
    ↓                           ↑
annotate                       所有单元完成
    ↓
quality_check
    ├── accepted ─────────→ select_unit
    └── retry ────────────→ annotate
```

图状态只保存当前单元、尝试次数、小型结果和 artifact 引用。源码、模型完整响应与大列表不塞入状态对象。

## 6. 断点与源码一致性

Pipeline 按工作单元保存成功结果和 checkpoint。恢复时：

- 已通过的函数簇不重复调用模型；
- 未完成簇从安全节点重新执行，不拼接中断的 JSON；
- 文件/项目聚合只有在输入语义未变化时才能复用；
- MATLAB 文件内容或清单发生变化时拒绝静默复用旧断点。

## 7. 实时进度

后端把固定图节点映射成简短事件，通过 REST 或 SSE 提供：

```http
GET /api/jobs/<job-id>/semantic-events?after_sequence=12
GET /api/jobs/<job-id>/semantic-events/stream?after_sequence=12
```

前端显示当前单元、节点、尝试和质量结论。事件不包含 API Key、完整提示词或整段源码。

## 标准产物

```text
semantic-preparation.json
semantic-work-units.json
function-annotations-<unit>-attempt-<n>.json
semantic-quality-report.json
semantic-conflicts.json
semantic-index.json
semantic-code-tree.json
semantic-progress.json
semantic-checkpoint.json
```

## 设计取舍

- 结构事实优先于模型推断；
- 有限上下文优先于一次发送整个仓库；
- 确定性 Reviewer 优先于“让模型自我感觉正确”；
- 失败和低置信度显式保留；
- SemanticIndex 是迁移的可选增强信息，不是迁移的强制前置步骤。
