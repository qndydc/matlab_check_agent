# 项目创作背景与技术路线

## 1. 创作背景

工程和科研领域存在大量长期积累的 MATLAB 代码库。这些项目往往不只是若干独立函数，而是包含入口脚本、局部函数、类、package、工具箱调用、全局状态、数据文件和循环依赖的完整系统。随着部署环境、许可证成本、生态集成和工程维护需求变化，团队经常需要：

- 快速理解陌生或缺少文档的 MATLAB 工程；
- 找到入口、算法核心、调用关系和风险点；
- 将项目迁移到 Python 或 C++ 等目标语言；
- 保证迁移后的工程在另一套环境中可以独立构建和运行；
- 对迁移前后的行为等价性给出可追溯证据。

这类任务的困难不只是语法不同。MATLAB 的一基索引、列主序、多返回值、动态类型、隐式扩展、函数调用与数组索引歧义、复数、空矩阵、`global`/`persistent`、工具箱和 MEX 等特性，都会影响目标代码的真实行为。大型项目还会超过单次大模型调用的上下文窗口；如果逐文件独立翻译，模型又无法保持跨文件接口、数据语义和模块结构的一致性。

因此，本项目的目标不是做一个简单的“语法替换器”，而是逐步构建一个面向大型代码库的、契约驱动且可验证的迁移系统：

```text
代码树与调用图
→ 函数/文件/项目三级语义
→ 行为契约与迁移计划
→ 按依赖分批转换
→ 构建、差分验证与局部修复
→ 可在目标环境运行的独立代码库
```

## 2. 市面上的主要技术路线

### 2.1 MATLAB Coder：面向 C/C++ 的静态代码生成

[MATLAB Coder](https://www.mathworks.com/help/coder/index.html) 可以从受支持的 MATLAB 代码生成 C/C++ 源码、静态库、动态库、可执行文件或 MEX，并提供类型定义、代码追踪、SIL/PIL 等工程能力。它适合嵌入式部署、性能优化和接口边界清晰的算法代码。

它的主要约束是：动态 MATLAB 语义需要被收敛为静态 C/C++ 类型和尺寸，通常需要明确入口函数及输入类型，并可能经历多轮代码适配和生成故障排查。它也不是 MATLAB 到 Python 的迁移工具，目标更偏向生成可部署的 C/C++，而不是产生符合 Python 生态习惯、便于继续维护的代码库。相关工作方式可参考 [MATLAB Coder code generation workflow](https://www.mathworks.com/help/coder/ug/code-generation-workflow.html)。

### 2.2 SMOP：MATLAB/Octave 到 Python 的编译器

[SMOP](https://github.com/victorlei/smop) 使用 Lexer、Parser、AST、名称解析和 Python 后端实现 MATLAB/Octave 到 Python 的转换。它会通过局部 use-def 信息区分 `x(...)` 是数组访问还是函数调用，并使用 `libsmop` 等运行时设施尽量保留 MATLAB 行为。

SMOP 的重要贡献是证明了以下路线可行：

```text
MATLAB 源码 → AST → 语义消歧 → Python 后端
```

但生成代码通常保留明显的 MATLAB 风格，并依赖兼容运行时。它主要解决编译和语言语义模拟问题，不负责大型工程的业务理解、目标 Python 架构设计、跨批次上下文管理及项目级行为验证。

### 2.3 matlab2python：基于 SMOP 的轻量 NumPy 风格转译

本项目重点调研了 GitCode 镜像对应的上游项目 [ebranlard/matlab2python](https://github.com/ebranlard/matlab2python)，源码检查基于提交 `2fd204515c056d1f4e8ce6cdfbbb97e9bcd7cc55`。

`matlab2python` 建立在 SMOP 之上，主要目标是生成更接近日常 NumPy 写法、较少依赖 `libsmop` 的 Python。其核心过程是：

1. `MatlabFile` 预处理 MATLAB 文件，区分 script、function 和 class，并提取函数签名、类属性与方法；
2. SMOP Lexer/Parser 将源码解析为 AST；
3. `resolve.py` 在单个语法树中计算标识符的定义与引用，辅助区分函数调用和数组索引；
4. `backend_m2py.py` 按 AST 节点生成 Python，并实现 `zeros`、`ones`、`reshape`、range、多返回值等规则；
5. 文本后处理根据生成内容补充 NumPy、SciPy、Matplotlib 等 import。

其 README 也明确说明：该项目处于 alpha 阶段，生成结果通常仍需人工调整，并不保证直接得到生产可用代码。[项目说明](https://github.com/ebranlard/matlab2python)

#### 对“大型项目”的实际处理方式

仓库提供的 `batchProcessing.sh` 会递归查找 `.m` 文件，然后逐个运行转换命令：

```text
find project -name "*.m"
→ 对每个文件单独执行 matlab2python.py
→ 在原文件旁生成同名 .py
```

CLI 虽然接受多个输入文件，但内部同样只是循环调用单文件转换。因此，它的“大型项目支持”本质上是批量文件处理，而不是项目级迁移。

#### 主要不足

- 没有建立跨文件调用图、共享符号表和 SCC 迁移顺序；
- 没有统一的 MATLAB 符号到 Python 模块/符号映射；
- 无法依靠项目级语义解决跨文件接口和命名一致性；
- 没有输入类型、shape、单位、副作用和数值容差等行为契约；
- 没有设计目标 Python 包结构、依赖锁定和入口兼容层；
- 没有对 MATLAB 与 Python 使用相同输入进行系统性差分验证；
- 没有根据构建或运行失败进行归因、重新规划和局部修复；
- 对工具箱、动态调用、MEX、GUI、Simulink 和外部资源缺少工程级迁移策略。

需要特别说明的是，SMOP 中使用 NetworkX 构造的图主要描述单个 AST 内标识符的定义—引用关系，并不是整个 MATLAB 项目的函数调用图。

### 2.4 直接使用大模型逐文件转换

大模型能够理解复杂算法，并生成比纯规则转译更自然的 Python 或 C++。但直接逐文件调用模型会遇到：

- 大型项目无法一次放入上下文；
- 分批后丢失调用方和被调用方约束；
- 相同概念在不同批次中使用不同类型、名称或模块；
- 模型可能臆造不存在的工具箱映射；
- 生成代码看似合理，却没有行为等价证据；
- 修复一个文件可能破坏其他批次已经形成的接口。

因此，大模型适合承担语义理解、契约推断、代码重写和失败归因，但不应替代确定性解析、文件管理、构建和验证工具。

## 3. 本项目采用的部分

我们不会把 `matlab2python` 整体作为大型工程转换引擎，也不计划直接依赖其生成结果作为最终交付。可借鉴或吸收的部分主要包括：

### 3.1 AST 驱动而非纯文本替换

保留“源码 → AST/统一中间表示 → 目标语言后端”的基本思想。语法结构、索引、运算符和函数调用应由解析结果驱动，避免使用脆弱的正则批量替换。

### 3.2 MATLAB 到目标库的映射知识

将 NumPy/SciPy 等替换规则整理为独立、版本化、可审计的 `ApiMappingRegistry`，例如：

```json
{
  "source_api": "reshape",
  "target_api": "numpy.reshape",
  "argument_policy": "shape_tuple",
  "semantic_notes": ["保持列主序 order=F"],
  "confidence": 0.95
}
```

这些映射可以为规则转译器和大模型共同使用，但必须接受行为契约和测试验证。

### 3.3 将规则转译作为候选代码生成器

未来可以把 SMOP/matlab2python 类的转译方式封装为一种 Translation Skill：先生成可追踪的初始 Python，再由转换 Agent 根据项目语义和行为契约重写，最后通过构建与差分测试决定是否接受。

### 3.4 MATLAB 特殊语义检查清单

将一基索引、多返回值、列主序、函数/数组歧义、cell、class、global、persistent 等难点转化为迁移行为契约和确定性质量门禁，而不是散落在代码生成后端中的隐含修补逻辑。

## 4. 本项目已有优势

截至当前阶段，本项目已经具备：

- 递归发现和解析 MATLAB 文件；
- 函数、脚本、类、package 与局部函数识别；
- 项目级函数调用图；
- SCC 循环簇、入口点、孤立节点和未解析调用分析；
- 按调用关系、SCC 和 token 预算拆分大型工程；
- 函数、文件、项目三级中文语义；
- 源码范围、源码哈希、置信度和待复核项；
- Artifact、Job 状态和 Web 历史项目持久化；
- 交互式项目树、调用图和语义检查界面。

这些能力构成后续迁移系统的“感知与项目记忆层”。它们解决了逐文件转译工具没有解决的大型工程上下文问题。

## 5. 计划中的创新点

以下能力是项目目标和技术创新方向，尚不能全部视为已经完成：

### 5.1 代码图驱动的上下文检索

不把整个项目或随机相似代码塞给模型，而是按照当前符号、SCC、调用者、被调用者、类型和模块关系构建有限上下文。大型工程的一致性来自持久化项目事实，而不是依赖模型聊天历史。

### 5.2 三级语义到行为契约

在自然语言语义之上建立结构化 `BehaviorContract`，记录类型、shape、单位、副作用、异常、数值容差、算法不变量和源语言特殊语义。契约成为不同转换批次之间稳定的接口边界。

### 5.3 SCC 与依赖拓扑驱动的批次迁移

循环依赖单元保持原子性，其他模块从依赖叶子向入口逐批转换。每批只读取必要源码和邻居契约，并在成功后冻结公共接口，避免大型项目分批转换后无法集成。

### 5.4 Agent 决策与确定性 Workflow 结合

代码事实由 Analysis Pipeline 产生，三级语义由固定 Semantic Pipeline 生成并自检；只有迁移阶段把 Agent 用于代码生成、失败归因和下一步决策。文件写入、执行、差分和发布结论始终由确定性工具控制。

### 5.5 MATLAB/Python 行为差分闭环

对相同输入分别运行 MATLAB 与 Python，并比较标量、数组 shape、dtype、浮点容差、复数、NaN/Inf、异常和声明的副作用。确定性 Validator 产生失败事实，Migration Agent 再决定局部修复、扩展上下文或转人工复核。

### 5.6 面向目标环境的独立交付

最终目标不是“生成若干 `.py` 文件”，而是生成包含包结构、依赖声明、入口、测试、GoldenCase、符号映射、契约和验证报告的独立 Python 代码库，使其能够脱离 MATLAB 在目标环境中运行。

### 5.7 面向多语言的统一模型

长期方向是在统一 Repository/File/Symbol/Call/Type 模型之上接入 MATLAB、Python 和 C++ Adapter，并表达 MEX、pybind11 等跨语言绑定。不同转换方向共享代码图、三级语义、行为契约、项目记忆和验证框架，只替换语言映射与目标后端。

## 6. 项目定位

本项目不是传统的逐文件转译器，也不追求让大模型无约束地修改整个仓库。它的定位是：

> 面向大型科学计算代码库的代码理解与迁移系统，以确定性代码图为事实基础，以三级语义和行为契约保持跨批次一致性，以 Agent 完成不确定推理，以构建和差分测试提供行为等价证据。

`matlab2python` 等项目提供了重要的编译器和规则映射经验；本项目在其基础问题之上，重点解决大型工程理解、项目级一致性、可验证迁移和目标环境交付。

## 7. 参考资料

- [matlab2python](https://github.com/ebranlard/matlab2python)
- [SMOP: Small Matlab/Octave to Python compiler](https://github.com/victorlei/smop)
- [MATLAB Coder Documentation](https://www.mathworks.com/help/coder/index.html)
- [MATLAB Coder code generation workflow](https://www.mathworks.com/help/coder/ug/code-generation-workflow.html)
- [MATLAB Coder code generation options](https://www.mathworks.com/help/coder/generating-code.html)
