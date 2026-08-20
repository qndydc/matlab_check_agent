# MATLAB Refactor Agent

## Web MVP

项目包含一个本机单用户 Web MVP，可生成可交互的函数调用图，并按需展示函数、文件和项目三级语义注释。

```powershell
python -m pip install -e ".[dev]"
Set-Location frontend
pnpm install
Set-Location ..
python scripts/start_mvp.py
```

浏览器访问 `http://127.0.0.1:5173`。前端会连接本机 `8000` 端口的 API；普通结构图不调用 LLM，只有点击“生成三级注释”后才会使用 LLM 配置。

Web 项目的调用图和三级注释会长期保存到 `var/web-projects.db`。前端“历史项目”面板可以恢复或删除这些本地快照；可通过 `MATLAB_REFACTOR_WEB_DB` 修改数据库路径。

### Windows Docker Desktop

生产镜像由 FastAPI 在一个端口同时提供 React 前端和 API，目标电脑不需要安装 Python、Node.js 或 pnpm。

```powershell
Copy-Item .env.docker.example .env
# 编辑 .env 中的 MATLAB_PROJECTS_PATH 和 MATLAB_DATA_PATH，并先创建这两个目录
docker compose build
docker compose up -d
```

浏览器访问 `http://127.0.0.1:8000`。页面中应输入容器路径，例如 `/projects/my-project`，而不是 Windows 盘符路径。完整说明见 [Windows Docker Desktop 部署](docs/docker-windows.md)。

MATLAB Refactor Agent 是一个面向 MATLAB 代码库的 CLI 重构工具。它能够扫描和解析 `.m` 文件、构建调用图、生成语义与重构计划，并在人工批准后把变更应用到隔离的输出目录。输入项目始终只读。

当前版本提供完整 CLI 流程：

```text
扫描 → 分块解析 → 依赖分析 → 语义注解 → 重构规划
    → 人工审批 → 隔离执行 → 静态验证 → 修复复审 → 报告
```

内部结构见 [structure.md](structure.md)，开发进度与后续路线见 [项目规划.md](项目规划.md)。

## 功能

- 递归发现 MATLAB 文件，并支持排除目录和通配规则。
- 使用 maxx/Tree-sitter 提取函数、脚本、package、局部函数、输入输出、行号和调用名称。
- 分块并行解析大型代码库，输出稳定的 Pydantic/JSON 数据。
- 构建有向调用图，以强连通分量（SCC）识别循环簇，并输出少量代表性环、孤立对象、入口点和未解析调用。
- 由 AI 根据函数源码与邻居上下文识别承担项目重要算法的核心函数；不再按调用次数判断。
- 在终端显示依赖树，并导出 Graph JSON 和 Mermaid `.mmd`。
- 通过 OpenAI-compatible LLM 生成函数、文件和项目三级语义索引。
- 并行生成模块职责与命名目录建议，再由确定性代码合并为重构计划。
- 通过 LangGraph checkpoint 暂停并等待人工批准。
- 在独立目录复制、改写和移动文件，绝不直接写入输入项目。
- 验证源项目完整性、输出完整性、解析结果、计划符合性和依赖图回归。
- 验证失败时生成有限修复建议，经人工复审后输出新版本。
- 生成结构化 JSON 和 Markdown 交付报告。

## 环境要求

- Python 3.10 或更高版本
- Windows、Linux 或 macOS
- 扫描和静态分析不要求安装 MATLAB
- `annotate`、`plan` 和报告生成需要可用的 OpenAI-compatible LLM API

> `maxx` 当前采用 GPL-3.0 许可证。分发本项目之前，请确认整体许可证策略。

## 使用 Conda 安装

```powershell
git clone <repository-url>
cd matlab_check_agent
conda create -n matlab-refactor python=3.11 -y
conda activate matlab-refactor
python -m pip install -e ".[dev]"
```

之后每次使用前激活环境：

```powershell
conda activate matlab-refactor
```

安装完成后可以使用：

```powershell
matlab-refactor --help
matlab-refactor --version
```

也可以通过 Python 模块运行：

```powershell
python -m matlab_refactor_agent --help
```

## 配置

推荐复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

然后至少设置输入目录和 LLM Key：

```dotenv
MATLAB_REFACTOR_INPUT_PATH=D:/projects/my-matlab-project
MATLAB_REFACTOR_CODE_OUTPUT_DIR=D:/outputs/refactored-projects
DEEPSEEK_API_KEY=your-api-key
```

`.env` 已被 Git 忽略，不要提交真实密钥。完整变量及说明见 [.env.example](.env.example)。项目不再读取 YAML 配置。

配置优先级为：

```text
进程环境变量 > .env > 代码默认值
```

相对路径以执行命令时的当前目录为基准。输入项目、重构输出、报告输出和图输出不能互相嵌套；尤其不能把输出目录放在输入项目内部。

主要输出目录默认值：

| 内容 | 默认目录 |
| --- | --- |
| 隔离重构代码库 | `var/refactored-projects` |
| 最终报告 | `var/reports` |
| Graph JSON / Mermaid | `var/graphs` |
| 阶段 artifact | `var/jobs` |
| Job 审计数据库 | `var/refactor-agent.db` |
| LangGraph checkpoint | `var/langgraph-checkpoints.db` |

## 快速开始

### 1. 扫描 MATLAB 项目

```powershell
matlab-refactor scan D:\path\to\matlab-project
matlab-refactor scan D:\path\to\matlab-project --json scan.json
```

省略项目路径时使用 `.env` 中的 `MATLAB_REFACTOR_INPUT_PATH`：

```powershell
matlab-refactor scan
```

### 2. 分析调用关系

```powershell
matlab-refactor analyze D:\path\to\matlab-project
```

显示终端依赖树并导出图：

```powershell
matlab-refactor analyze D:\path\to\matlab-project `
  --tree `
  --graph-json dependency-graph.json `
  --mermaid dependency-graph.mmd
```

如果 `auto_export_graphs` 为 `true`，未显式指定文件时也会自动写入配置的图目录。

### 3. 生成语义索引

```powershell
matlab-refactor annotate D:\path\to\matlab-project
matlab-refactor annotate D:\path\to\matlab-project --json semantic-index.json
```

此命令会调用 LLM，生成函数、文件、项目三级语义，以及模块职责和命名目录候选。

### 4. 生成并审批重构计划

```powershell
matlab-refactor plan D:\path\to\matlab-project
```

`plan` 会输出 Job ID，并在人工审批节点暂停。查看计划：

```powershell
matlab-refactor review <JOB_ID>
```

批准执行：

```powershell
matlab-refactor review <JOB_ID> --decision approve --comment "确认执行"
```

也可以拒绝或要求修改：

```powershell
matlab-refactor review <JOB_ID> --decision reject --comment "不采用该方案"
matlab-refactor review <JOB_ID> --decision request_changes --comment "请调整目录方案"
```

批准后，新代码库写入：

```text
<output_dir>/<JOB_ID>/
```

验证失败并批准修复后，新版本写入：

```text
<output_dir>/<JOB_ID>-repair-<N>/
```

已有输出不会被覆盖。

### 5. 查看状态和报告

```powershell
matlab-refactor status <JOB_ID>
matlab-refactor report <JOB_ID>
matlab-refactor report <JOB_ID> --json final-report.json
```

## 命令概览

| 命令 | 用途 | 是否调用 LLM |
| --- | --- | --- |
| `scan` | 文件发现与 MATLAB 元数据提取 | 否 |
| `analyze` | 调用图、SCC 循环簇、代表性环、入口和孤立对象分析 | 否 |
| `annotate` | 三级语义与规划候选生成 | 是 |
| `plan` | 生成重构计划并等待审批 | 是 |
| `review` | 查看或提交人工决定 | 视后续流程而定 |
| `report` | 查询最终交付报告 | 否 |
| `status` | 查询 Job 与 Worker 状态 | 否 |

所有支持 `--json [FILE]` 的命令，在省略 `FILE` 时将 JSON 写入标准输出，适合 CI 使用。

## 安全边界

- 输入 MATLAB 项目始终只读。
- 执行前后都会计算源目录树哈希。
- 修改仅发生在隔离 staging 副本中。
- 输出完整后通过原子目录替换发布。
- 符号链接和目录 junction 会被拒绝。
- LLM 只返回结构化语义或建议，不能直接写文件。
- 真正的改名、引用改写、移动和验证由确定性代码执行。

当前验证以静态检查为主。未安装 MATLAB Engine 时，运行时、数值和性能验证会标记为 `unavailable`，不会伪装成通过。

## 开发与测试

```powershell
python -m pytest
python -m pytest --cov=matlab_refactor_agent --cov-report=term-missing
```

当前测试覆盖扫描、解析、图分析、CLI、LangGraph 编排、语义聚合、计划仲裁、隔离执行、验证和报告流程。
