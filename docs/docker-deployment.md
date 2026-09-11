# Docker 部署指南

## 准备配置

联网构建机安装并启动 Docker Desktop（Windows 切换到 Linux containers）或 Docker Engine + Compose（Linux），然后复制配置：

~~~bash
cp .env.docker.example .env
~~~

编辑 .env，至少确认：

~~~dotenv
MATLAB_DATA_PATH=/srv/matlab-agent/data
MATLAB_BIND_ADDRESS=0.0.0.0
MATLAB_WEB_PORT=8000
MATLAB_MIGRATION_WEB_PORT=8001
MATLAB_REQUIRE_EMPLOYEE_ID=1
MATLAB_ADMIN_EMPLOYEE_IDS=你的管理员工号
MATLAB_ACTIVE_JOBS=16
MATLAB_GLOBAL_WORKERS=8
~~~

Windows Docker Desktop 可将数据目录写成 D:/MATLAB/matlab-atlas-data。

## 联网主机直接部署

~~~bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail 100 -f matlab-app nginx
~~~

同一内网的电脑访问：

- Semantic：http://主服务器IP:8000
- Migration：http://主服务器IP:8001

主服务器防火墙只向需要的内网网段开放 TCP 8000、8001。应用容器端口没有直接发布，由 Nginx 统一转发。

## 断网内网部署

在联网构建机生成并导出应用和 Nginx 镜像：

~~~bash
docker compose build
docker pull nginx:1.27-alpine
docker image save -o matlab-atlas-0.1.0.tar matlab-atlas:0.1.0 nginx:1.27-alpine
~~~

将 matlab-atlas-0.1.0.tar、compose.yaml、.env 和 packaging/nginx.conf 一起复制到内网 Linux 主服务器，然后执行：

~~~bash
docker image load -i matlab-atlas-0.1.0.tar
sudo mkdir -p /srv/matlab-agent/data
docker compose up -d --no-build
docker compose ps
~~~

## 使用

1. 打开 Semantic 页面并输入工号；首次使用会自动创建用户记录和个人项目目录。
2. 上传只包含项目源码的 ZIP，选择该项目后执行静态分析和三级语义。
3. 打开 Migration 页面；同一浏览器会复用工号会话，选择自己的静态分析快照开始迁移。
4. 任务完成后点击“下载结果”取得 ZIP。
5. 管理员工号可以打开“设置”修改持久化 .env；修改只作用于之后启动的任务。

不要把 .env、API Key、数据库、node_modules 或构建缓存放进上传包。

## 备份、升级与回滚

数据全部位于 MATLAB_DATA_PATH。备份前先停止服务，确保 SQLite 与文件快照一致：

~~~bash
docker compose stop
sudo tar -C /srv/matlab-agent -czf matlab-atlas-data-backup.tgz data
docker compose start
~~~

升级时先备份数据，然后导入新镜像并重建容器：

~~~bash
docker image load -i matlab-atlas-new-version.tar
docker compose up -d --no-build --force-recreate
~~~

若升级失败，重新载入旧镜像、恢复旧版 compose.yaml，再执行同一条 up 命令。数据库结构不兼容时，应停止服务并从备份恢复整个数据目录。

## 检查与故障定位

~~~bash
docker compose ps
docker compose logs --tail 200 matlab-app
docker compose logs --tail 100 nginx
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8001/api/health
~~~

管理员登录后还可以访问 /api/admin/runtime、/api/admin/jobs 和 /api/admin/storage。
