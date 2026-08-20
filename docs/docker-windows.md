# Windows Docker Desktop 部署

## 前置条件

- Windows 10/11 x64，已启用硬件虚拟化和 WSL 2。
- 已安装 Docker Desktop，并切换到 Linux containers。
- 首次构建需要访问 Docker Hub、npm/pnpm 软件源和 Python PyPI。

## 首次启动

在 PowerShell 中进入项目根目录：

```powershell
Copy-Item .env.docker.example .env
notepad .env
```

至少把 `MATLAB_PROJECTS_PATH` 改成存放 MATLAB 项目的父目录，并创建数据目录。例如：

```powershell
New-Item -ItemType Directory -Force D:\MATLAB\projects
New-Item -ItemType Directory -Force D:\MATLAB\matlab-atlas-data
docker compose build
docker compose up -d
docker compose ps
```

浏览器访问 <http://127.0.0.1:8000>。页面中输入容器路径，而不是 Windows 路径：

```text
/projects/具体项目目录
```

`/projects` 在容器内为只读；历史项目、SQLite 数据库、报告和图保存在
`MATLAB_DATA_PATH` 对应的 Windows 目录中。

## 日常命令

```powershell
docker compose logs -f matlab-atlas
docker compose restart
docker compose stop
docker compose down
docker compose up -d --build
```

`docker compose down` 不会删除绑定到 Windows 目录的项目数据。升级前仍建议备份
`MATLAB_DATA_PATH` 整个目录。

## 局域网访问

将 `.env` 中的 `MATLAB_BIND_ADDRESS` 改为 `0.0.0.0`，重新运行：

```powershell
docker compose up -d
```

然后在 Windows 防火墙中仅对可信内网开放 `MATLAB_WEB_PORT`。当前 Web MVP 没有身份认证，
不要直接暴露到公网。

## LLM 功能

不填写 `DEEPSEEK_API_KEY` 时，扫描和调用图仍可使用；三级语义注释会因没有密钥而失败。
真实密钥只放在本地 `.env`，不要提交到 Git。
