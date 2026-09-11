# 内网多用户部署临时开发计划

> 状态：临时执行文档。每完成并验收一个功能，将对应条目改为 `- [x] ~~功能说明~~`，保留原文和完成痕迹；不要直接删除条目。
>
> 原则：优先复用现有 FastAPI、SQLite、Artifact、Job Manager 和 Docker 代码，只在隔离边界与统一调度入口处增加薄层，避免重写现有 Analysis、Semantic、Migration Pipeline。

## 目标

将主程序部署在一台不能主动访问其他代码服务器的内网 Linux 主机上。用户通过浏览器输入工号、上传 MATLAB 项目压缩包、发起语义分析或 Python 迁移任务，并下载生成结果。所有用户数据按工号隔离；整台服务器同时运行的重任务不超过统一上限；内网其他服务器能够通过主机地址访问前端。

```text
用户浏览器
  ├─ 输入工号并建立会话
  ├─ 上传 MATLAB 项目 ZIP
  ├─ 发起 Semantic / Migration Job
  └─ 下载生成的 Python ZIP
           │
           ▼
        Nginx
           │
           ▼
单个 Python 服务进程（两个现有 API）
  ├─ Semantic API :8000
  ├─ Migration API :8001
  ├─ JobCoordinator（限制活跃父任务）
  └─ GlobalHeavyWorkPool（限制全局重任务）
           │
           ▼
 SQLite + /data 持久化目录
```

## 技术选型

| 能力 | 选型 | 最小改动理由 |
| --- | --- | --- |
| 用户标识 | 工号 + HttpOnly Cookie 会话 | 不引入完整账号系统；首次使用自动建用户记录 |
| 元数据 | SQLite，开启 WAL 和 busy timeout | 延续现有 SQLite，不增加独立数据库服务 |
| 项目传入 | 浏览器上传 ZIP，FastAPI `UploadFile` 流式落盘 | 服务端无需访问用户代码服务器，也不需要安装 Git |
| 项目传出 | 服务端生成 ZIP，HTTP 流式下载 | 浏览器可直接取回生成代码和报告 |
| 父任务调度 | `JobCoordinator`，默认 `max_active_jobs=16` | 保留现有同步 Job/Pipeline 写法，只限制活跃控制线程数量 |
| 重任务调度 | 单例 `GlobalHeavyWorkPool`，默认 `max_workers=8` | 统一限制 Semantic、Migration、WCC、Chunk 和大规模解析的并发总量 |
| 待执行任务 | SQLite Job 台账 + 内存协调器 | 以最小改动记录所有任务和重启中断状态，同时限制活跃父任务线程 |
| 文件存储 | Linux 主机目录 bind mount 到容器 `/data` | 用户代码和结果不写入镜像层，升级镜像不丢数据 |
| 服务入口 | Docker Compose + Nginx | 统一控制上传大小、超时和对内网的监听地址 |
| 部署形态 | 一个应用容器内运行一个 Python 进程和两个现有 API | 两个 API 能共享同一个内存中的全局线程池，确保全机进程内重任务不超过 8 |

## 持久化目录

Linux 主机建议使用 `/srv/matlab-agent/data`，挂载为容器内 `/data`：

```text
/srv/matlab-agent/data/
├── config/
│   └── .env
├── control.db
├── jobs/                                  # 以随机 Job ID 隔离的现有 Artifact Store
└── users/
    └── {employee_id}/
        ├── projects/
        │   └── {project_id}/
        │       ├── upload.zip
        │       └── source/
        └── exports/
            └── {job_id}.zip
```

任何 API 都不能接受或拼接任意服务器绝对路径。前端只提交 `project_id`；后端根据当前会话中的 `employee_id` 查询数据库并解析真实路径。

## SQLite 数据模型

在现有数据库初始化/迁移逻辑上增加三张轻量表；现有分析产物表尽量不改结构，只补充所有权关联或通过 `execution_jobs` 关联。

```text
users
  employee_id       TEXT PRIMARY KEY
  created_at        TEXT NOT NULL
  last_login_at     TEXT NOT NULL

projects
  project_id        TEXT PRIMARY KEY
  employee_id       TEXT NOT NULL
  original_filename TEXT NOT NULL
  source_type       TEXT NOT NULL
  storage_path      TEXT NOT NULL
  upload_bytes      INTEGER NOT NULL
  unpacked_bytes    INTEGER
  sha256            TEXT NOT NULL
  state             TEXT NOT NULL
  created_at        TEXT NOT NULL

execution_jobs
  job_id             TEXT PRIMARY KEY
  employee_id        TEXT NOT NULL
  project_id         TEXT NOT NULL
  job_type           TEXT NOT NULL
  state              TEXT NOT NULL
  stage              TEXT
  settings_snapshot  TEXT
  error              TEXT
  created_at         TEXT NOT NULL
  updated_at         TEXT NOT NULL
```

`job_id` 和 `project_id` 使用“时间戳 + UUID 随机后缀”，不能继续只使用时间戳。所有项目、任务、事件、结果、删除和下载查询都必须同时校验资源所属工号。

## 预计修改范围

### 1. 用户会话与所有权

- 在两个 FastAPI 应用共用层增加用户会话依赖：首次提交工号时 `INSERT OR IGNORE`，以后更新 `last_login_at`。
- 使用签名 HttpOnly Cookie 保存会话标识；工号只作为轻量隔离标识，不宣称是安全认证。
- 现有项目列表、任务详情、SSE、恢复、删除、导出接口增加所有权过滤。
- Semantic 前端现有 `localStorage` 项目状态按工号加前缀，避免同一浏览器切换工号时串数据。
- `.env` 是服务器级全局配置，只允许管理员工号修改；普通用户任务保存一份非敏感设置快照。

### 2. 项目上传与安全解压

- 新增 `POST /api/projects/upload`，使用流式写入 `upload.zip.part`，计算 SHA-256，完成后原子改名。
- 上传完成后提交 `project_ingest` 重任务，安全解压到当前用户的 `projects/{project_id}/source`。
- 第一版只接受 ZIP；不处理 Git 地址、RAR、7z、symlink 或 hardlink。
- 解压必须阻止 Zip Slip、绝对路径、盘符路径、软/硬链接、控制字符、路径逃逸和压缩炸弹。
- 增加上传大小、解压后大小、文件数和用户配额限制，默认建议如下：

```dotenv
MATLAB_MAX_UPLOAD_BYTES=2147483648
MATLAB_MAX_UNPACKED_BYTES=5368709120
MATLAB_MAX_PROJECT_FILES=100000
MATLAB_USER_QUOTA_BYTES=10737418240
```

- 新增项目列表、项目详情和项目删除接口。删除前检查不存在运行中的关联任务。
- 将现有前端“输入项目路径”改为“上传 ZIP / 选择已上传项目”；CLI 的本地路径参数保持不变。

### 3. 生成结果导出

- 新增 `POST /api/jobs/{job_id}/export`，在重任务池中生成当前用户的结果 ZIP。
- 新增 `GET /api/jobs/{job_id}/download`，校验所有权后流式返回 ZIP；下载网络传输不占重任务槽位。
- ZIP 至少包含生成的 Python 工程、迁移/分析摘要和必要的诊断报告，不包含 `.env`、密钥、SQLite、原始上传包和服务日志。
- 文件名只使用安全的项目名与 Job ID，响应头使用兼容中文名称的编码方式。

### 4. 统一并发调度

- 新增进程级单例 `GlobalHeavyWorkPool(max_workers=8)`。
- 在提交前使用容量信号量限制“运行中 + 已提交等待”的数量，避免 `ThreadPoolExecutor` 无界队列积压大量 Future 和上下文。
- 将 Semantic cluster/file LLM、Migration WCC/Act Chunk、重解析、项目解压和结果打包逐步改为 `global_pool.submit(...)`。
- 父 Job 不进入重任务池，继续在 Job Coordinator 控制线程中同步等待子 Future，因此不占用 8 个重任务槽位。
- 禁止重任务 worker 再向同一重任务池提交任务并等待结果；增加运行时保护或明确断言，避免嵌套等待死锁。
- 新增 `JobCoordinator(max_active_jobs=16)`：最多激活 16 个父流程，其余留在有界内存队列；所有状态同步写入 SQLite 台账。
- 保留现有 `SemanticJobManager`、`MigrationJobManager` 和 Pipeline 主流程；只替换内部创建的局部线程池与 Web Job 提交入口。
- 进程重启时将遗留的 `running` 任务标为可恢复/中断状态，不自动假装成功。

### 5. Docker Compose 与内网访问

- 合并现有两个后端到同一个应用容器和同一个 Python 进程；继续暴露现有 Semantic `:8000` 与 Migration `:8001` 接口。
- 增加 Nginx 服务作为内网入口，监听 `0.0.0.0`，将两个端口分别转发到应用容器。
- 应用端口只在 Compose 内部网络开放；主机只发布 Nginx 的端口。
- Nginx 配置上传大小、上传/下载/长任务超时和 SSE 禁用缓冲。
- `/srv/matlab-agent/data:/data` 使用可写 bind mount；容器镜像和容器可随版本替换，业务数据独立保留。
- 保留 `data-init` 或等价启动检查，统一创建目录并校验 UID/GID 写权限。
- 主服务器防火墙只向需要的内网网段开放服务端口，不开放 SQLite 文件或容器内部端口。

### 6. 监控与运维

- 新增管理员接口：`GET /api/admin/runtime`、`GET /api/admin/jobs`、`GET /api/admin/storage`。
- 至少展示 active jobs、queued jobs、active heavy workers、heavy queue depth、各用户占用空间和磁盘剩余量。
- 日志写入 `employee_id`、`project_id`、`job_id` 等关联字段，但不记录源码全文、Cookie、API Key 或完整模型提示词。
- 第一版不自动清理用户数据，采用配额、磁盘告警和管理员手动删除，避免误删成果。

## 分阶段实施清单

### 阶段 A：隔离基础

- [x] ~~建立 `users`、`projects`、`execution_jobs` 表和向后兼容初始化逻辑。~~
- [x] ~~实现工号首次登记、重复登录读取和 HttpOnly Cookie 会话。~~
- [x] ~~将 Job/Project ID 改为时间戳加 UUID 后缀。~~
- [x] ~~为所有项目与任务 API 增加 `employee_id` 所有权校验。~~
- [x] ~~将用户目录限制在 `/data/users/{employee_id}`，阻止路径逃逸。~~
- [x] ~~将前端本地状态按工号隔离。~~
- [x] ~~将 `.env` 修改权限限制为管理员，并验证修改只影响后续任务。~~

### 阶段 B：项目上传与取回

- [x] ~~实现 ZIP 流式上传、临时文件、SHA-256 和原子落盘。~~
- [x] ~~实现安全解压及上传大小、解压大小、文件数、用户配额限制。~~
- [x] ~~实现用户项目列表、详情和安全删除接口。~~
- [x] ~~将 Web 项目入口从服务器路径改为上传或选择 `project_id`。~~
- [x] ~~保持 CLI 对 Windows/Linux/Python `pathlib.Path` 路径的兼容，不改变 CLI 使用方式。~~
- [x] ~~实现 Job 结果打包接口，确保不带入敏感文件和原始上传包。~~
- [x] ~~实现鉴权后的结果 ZIP 流式下载及前端下载入口。~~

### 阶段 C：统一并发

- [x] ~~实现全局 `GlobalHeavyWorkPool` 和有界提交机制，默认容量 8。~~
- [x] ~~实现 SQLite Job 台账和 `JobCoordinator`，默认最多 16 个活跃父任务。~~
- [x] ~~Semantic 重执行单元迁移到全局池并移除对应局部线程池。~~
- [x] ~~Migration WCC/Chunk 重执行单元迁移到全局池并移除对应局部线程池。~~
- [x] ~~项目解压、重解析和结果打包接入全局池。~~
- [x] ~~增加禁止全局池 worker 嵌套提交并同步等待的保护。~~
- [x] ~~实现服务重启后的 queued/running Job 状态恢复规则。~~

### 阶段 D：单进程容器与内网部署

- [x] ~~增加同时托管两个现有 API 的单进程启动入口。~~
- [x] ~~合并两个应用镜像，确保两个 API 共享同一个全局池实例。~~
- [x] ~~更新 Compose 为 `nginx + matlab-app + data-init`。~~
- [x] ~~增加 Nginx 上传、超时、SSE 和双端口反向代理配置。~~
- [ ] 挂载并验证 `/srv/matlab-agent/data:/data` 的持久化与权限。
- [ ] 在 Linux Docker 环境验证同网段其他服务器可通过主机 IP 访问两个前端。
- [ ] 验证替换镜像、重建容器后用户、项目、任务和下载产物仍存在。

### 阶段 E：监控、测试与发布

- [x] ~~增加运行队列、重任务池和存储占用的管理员监控接口。~~
- [x] ~~补充跨用户越权读取、下载、删除、SSE 和恢复测试。~~
- [x] ~~补充恶意 ZIP、超额 ZIP、同名文件、链接文件和失败清理测试。~~
- [x] ~~补充 16 个以上父 Job 排队和全局重任务始终不超过 8 的并发测试。~~
- [x] ~~补充服务重启状态和 SQLite WAL 测试。~~
- [ ] 补充容器重建和磁盘空间不足测试。
- [x] ~~运行完整 Python 测试和两个前端生产构建。~~
- [ ] 构建 Linux `amd64` 镜像并完成联网主机与断网内网主机的导入部署演练。
- [x] ~~更新 README/部署文档，记录备份、升级、回滚和数据目录恢复步骤。~~

## 验收标准

- 两个不同工号看不到、下载不到、删除不了对方的项目和任务。
- 用户上传 ZIP 后，服务端只在自己的 `/data/users/{employee_id}` 目录中解压和生成文件。
- Web 端不再依赖输入服务器本地路径；CLI 仍可在 Windows 和 Linux 使用本地路径。
- 同时提交大量 Semantic、Migration、WCC、Chunk、解压和打包任务时，活跃重 worker 始终不超过配置值 8。
- 父任务超过 16 个时，其余任务留在协调队列且状态记录在 SQLite 中，不无限创建线程。
- 任意容器重建和镜像升级不会删除 `/data` 中的用户数据、任务记录和导出文件。
- 内网其他服务器能通过 Linux 主机 IP 访问页面、上传项目、查看进度并下载结果。
- 普通用户不能修改全局 `.env`；管理员修改后不泄露密钥，且只影响后续新任务。

## 暂不纳入第一版

- 完整账号密码、LDAP/AD、OAuth 或单点登录；工号登录只提供轻量数据隔离。
- 多台应用主机共享同一任务池；第一版只保证单主机、单应用进程内的全局并发上限。
- Git 仓库地址拉取、断点续传、大文件分片上传、RAR/7z 解压。
- Redis、Celery、RabbitMQ、PostgreSQL、Kubernetes 或完整 DAG Scheduler。
- 自动数据生命周期删除；在具备回收站、备份和审计前不启用自动清理。

## 工作量估算

在尽量复用现有代码、单主机部署、轻量工号会话的前提下，预计共 **14～22 人日**：

| 工作项 | 预计人日 |
| --- | ---: |
| 用户、SQLite 与所有权隔离 | 3～4 |
| 安全上传、项目管理与结果下载 | 3～5 |
| Job Coordinator 与全局重任务池 | 4～6 |
| 单进程容器、Nginx 与持久化 | 2～3 |
| 测试、监控、部署演练和文档 | 2～4 |

实际实施顺序以阶段 A → B → C → D → E 为准。每个条目只有在代码、测试和对应验收均完成后才划掉。
