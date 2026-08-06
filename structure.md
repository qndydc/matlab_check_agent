# 项目文件夹结构

本文档记录 MATLAB Refactor Agent 当前的目录结构及重要文件用途。`.git`、Python 缓存、测试缓存和包构建元数据未展开；`var/` 仅记录关键运行产物类型。

本文展示的是**当前已落地结构**。四大子系统、Multi-Agent 拆分和未来目标目录以 [`项目文件规划.md`](项目文件规划.md) 的 v2 规划为准；后续按里程碑渐进迁移，不一次性破坏现有模块。

```text
matlab_check_agent/
├── .env.example                         # 环境变量模板；不保存真实密钥
├── .gitignore                           # Git 忽略规则
├── config.example.yaml                  # CLI 项目扫描和日志配置示例
├── pyproject.toml                       # Python 包、依赖、CLI 入口和测试配置
├── README.md                            # 安装、使用、输出语义和开发约定
├── structure.md                         # 当前项目目录结构说明
├── 设计文档.md                           # 系统目标、总体架构和完整功能设计
├── 项目文件规划.md                        # 分阶段目录规划和实施路线
│
├── docs/
│   └── orchestrator-worker.md            # 多 Worker 职责、状态流和大库分片原则
│
├── src/
│   └── matlab_refactor_agent/           # Python 主程序包
│       ├── __init__.py                  # 包版本信息
│       ├── __main__.py                  # `python -m matlab_refactor_agent` 入口
│       │
│       ├── application/                 # 应用用例编排层
│       │   ├── __init__.py
│       │   └── services.py              # 通过 Orchestrator 提供 scan/analyze 用例
│       │
│       ├── capabilities/                # 可独立替换和测试的核心能力
│       │   ├── __init__.py
│       │   ├── analyzer/
│       │   │   ├── __init__.py
│       │   │   └── dependency_graph.py # 构建调用图，检测循环、孤立和核心函数
│       │   ├── parser/
│       │   │   ├── __init__.py
│       │   │   ├── base.py              # MATLAB 解析器协议接口
│       │   │   ├── matlab_scanner.py    # 文件发现器及兼容同步扫描器
│       │   │   └── maxx_parser.py       # maxx 适配器；提取签名、作用域和调用信息
│       │   └── visualizer/
│       │       ├── __init__.py
│       │       └── dependency_view.py   # 终端调用树、Graph JSON 和 Mermaid 导出
│       │
│       ├── domain/                      # 无 UI 依赖的领域数据和错误定义
│       │   ├── __init__.py
│       │   ├── enums.py                 # MATLAB、Job、Task 和 Worker 状态枚举
│       │   ├── exceptions.py            # 路径、配置、调度、冲突和质量门禁异常
│       │   ├── models.py                # FunctionInfo、ScanResult、AnalysisResult 等模型
│       │   └── orchestration.py         # Job、Task、WorkerResult 和 Outcome 契约
│       │
│       ├── infrastructure/              # 配置、日志等外部基础设施适配
│       │   ├── __init__.py
│       │   ├── artifacts.py             # Job 隔离的大对象 JSON 存储及路径安全
│       │   ├── config.py                # 加载 YAML 并通过 Pydantic 严格校验
│       │   └── logging.py               # 标准日志初始化
│       │
│       ├── orchestration/               # Orchestrator 控制组件
│       │   ├── __init__.py
│       │   ├── conflict_resolver.py     # Worker 路径读写声明与冲突仲裁
│       │   ├── orchestrator.py          # Job 流程、任务依赖和失败边界
│       │   ├── quality_gate.py          # 阶段 artifact 和图不变量校验
│       │   ├── state_manager.py         # SQLite Job/Task 状态持久化
│       │   ├── task_queue.py            # 稳定优先级任务队列
│       │   └── worker_pool.py           # Worker 注册、生命周期和任务路由
│       │
│       ├── interfaces/                  # 用户接口层
│       │   ├── __init__.py
│       │   └── cli/
│       │       ├── __init__.py
│       │       ├── main.py              # scan/analyze/status 命令和 JSON 输出
│       │       └── render.py            # 分析、调用树和 Job 状态渲染
│       │
│       └── workers/                     # 七类 Worker 的统一边界
│           ├── __init__.py
│           ├── base.py                  # BaseWorker 和 WorkerContext
│           ├── scanner_agent.py         # Worker-1：发现文件并生成稳定清单
│           ├── parser_agent.py          # Worker-2：分片解析、完整性校验和聚合
│           ├── analyzer_agent.py        # Worker-3：依赖图和指标分析
│           ├── planner_agent.py         # 旧 Worker-4 预留；将由多个专责 Agent 替代
│           ├── executor_agent.py        # Worker-5：重构执行预留接口及演示入口
│           ├── validator_agent.py       # 验证 Agent 预留接口及演示入口
│           ├── reporter_agent.py        # 自然语言报告 Agent 预留接口及演示入口
│           └── reserved_agents.py       # 未实现 Worker 的共享安全基类
│
├── tests/
│   ├── fixtures/
│   │   ├── fanout-config.yaml           # Worker-2 小分片 CLI 验证配置
│   │   └── matlab_projects/
│   │       └── basic/                   # 小型 MATLAB 集成测试项目
│   │           ├── +utils/
│   │           │   └── normalize.m      # MATLAB package 函数样例
│   │           ├── build/
│   │           │   └── ignored.m        # 验证 build 目录排除规则的样例
│   │           ├── cycleA.m             # 循环依赖节点 A
│   │           ├── cycleB.m             # 循环依赖节点 B
│   │           ├── loadValues.m         # 普通独立函数样例
│   │           ├── main.m               # 脚本入口样例
│   │           ├── orphan.m             # 孤立函数样例
│   │           └── processData.m        # 主函数及局部函数样例
│   ├── integration/
│   │   ├── test_cli.py                  # 从 CLI 到 JSON 报告的集成测试
│   │   └── test_orchestrator.py         # Worker 流水线、SQLite 和 status 测试
│   ├── unit/
│   │   ├── test_module_headers.py       # 强制检查所有 `.py` 文件标准模块头
│   │   ├── workers/
│   │   │   └── test_worker_entrypoints.py # 七类 Worker 独立演示入口测试
│   │   ├── analyzer/
│   │   │   └── test_dependency_graph.py # 依赖图、入口点和作用域消歧测试
│   │   ├── orchestration/
│   │   │   └── test_components.py       # 队列、artifact 和冲突仲裁测试
│   │   ├── parser/
│   │   │   ├── test_maxx_parser.py      # 函数签名、包、类和局部函数解析测试
│   │   │   └── test_scanner.py          # 文件发现及排除规则测试
│   │   └── visualizer/
│   │       └── test_dependency_view.py  # 图契约、终端树和 Mermaid 导出测试
│
└── var/                                 # 运行时输出，不纳入 Git
    ├── jobs/<job-id>/                   # Worker 间传递的大型 artifact
    ├── refactor-agent.db                # Orchestrator Job/Task SQLite 状态库
    ├── dependency-graph.json            # Web-ready 节点/边图数据样例
    ├── dependency-graph.mmd             # Mermaid 函数调用图样例
    ├── matlab-check-analysis.json       # matlab_check 环境生成的分析样例
    └── sample-analysis.json             # 开发验证生成的分析样例
```

## 代码数据流

```text
CLI 参数
  -> infrastructure/config.py 加载配置
  -> application/services.py 请求 Orchestrator 用例
  -> orchestration/orchestrator.py 创建 Job 和阶段任务
  -> workers/scanner_agent.py 发现文件并写 file-manifest artifact
  -> orchestrator 按 parser_chunk_size fan-out Worker-2 并发解析任务
  -> workers/parser_agent.py 校验并聚合分片，写 scan-result artifact
  -> workers/analyzer_agent.py 读取 scan 引用、分析调用图并写 analysis artifact
  -> quality_gate.py 校验，state_manager.py 持久化状态
  -> capabilities/visualizer/dependency_view.py 投影终端树/Graph JSON/Mermaid
  -> interfaces/cli/render.py 或 main.py 输出结果
```

## 维护约定

- 新增、删除或移动重要文件后，应同步更新本文档。
- 缓存、构建产物、虚拟环境和临时分析结果不应逐项记录。
- 新增重要文件时，目录树注释应说明其职责，而不是复述文件名。
- Python 函数和类继续遵循 README 中的“作用、输入、输出、数据流”注释头规范。
- 所有 `.py` 文件必须包含 `Description`、`References`、`Referenced By` 模块头，并由自动化测试强制检查。
- Worker 模块应保留 `if __name__ == "__main__"` 入口，便于脱离 Orchestrator 展示其输入、输出和 artifact 数据流。
