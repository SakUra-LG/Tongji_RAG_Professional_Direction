# SynapseQ 同济大学 AI 校园问答系统

SynapseQ 是一个面向校园场景的 RAG 问答系统。系统提供统一的智能问答入口，用户以访客、学生、教师或管理员身份进入后，后端会根据身份自动决定可访问的数据范围，并通过 JWT、Redis 会话、MySQL 结构化数据和 Milvus 元数据过滤控制访问权限。

本仓库已经包含前端、后端、数据库、向量库、对象存储和初始化脚本，推荐使用 Docker Compose 在本地一键部署。

## 项目功能

- 统一问答入口：前端不再让用户手动选择公开、学术、内部、个人等模块，统一调用 `type=auto`，由后端自动路由。
- 身份权限控制：访客只能访问公开资料和 FAQ；学生、教师可访问公开资料、校内资料、本人结构化信息和授权通知；管理员可进入管理后台。
- 结构化个人问答：学生/教师登录后，可询问“我的 GPA 是多少”“我的课程表”“我的考试信息”等问题，后端只读取当前登录用户自己的 MySQL 记录。
- RAG 检索与降级：优先匹配人工 FAQ，再结合 Milvus 向量检索、关键词检索和重排序生成回答；DashScope 暂不可用时，会尽量返回数据库命中的资料摘要。
- 管理后台：管理员可在前端维护 FAQ、编辑资料库文本块、预览网页爬取结果，并保存到公开库或师生知识库。
- 可重复初始化：首次部署会自动建表、建 Milvus 集合、导入种子 CSV、同步人工 FAQ；后续重启不会清空已有业务数据。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、Tailwind CSS、lucide-vue-next、Nginx |
| 后端 | FastAPI、LangChain、DashScope、SQLAlchemy、Redis |
| 数据库 | MySQL 8.0、Redis 7、Milvus 2.3.4、MinIO、Etcd |
| 部署 | Docker Compose |

## 目录结构

```text
tongji_rag/
├── backend/                  # FastAPI 后端、RAG 管线、初始化和爬虫脚本
│   ├── app/                  # API、配置、数据库模型、问答组件
│   └── scripts/              # bootstrap、Milvus 导入导出、FAQ 同步、网站爬取
├── frontend/                 # Vue 3 前端和 Nginx 配置
├── data/                     # Docker 持久化数据目录，运行后生成或更新
├── docker-compose.yml        # 本地完整服务编排
├── .env.example              # 环境变量模板
├── local_deploy_import.ps1   # Windows 一键启动脚本
├── DEPLOYMENT.md             # 更详细的部署说明
└── USER_ACCOUNTS.md          # 演示账号和数据说明
```

## 本地部署

### 1. 准备环境

本地机器需要安装：

- Docker Desktop，或 Docker Engine + Docker Compose Plugin
- Git
- 可访问阿里云 DashScope / 百炼 API 的网络

建议配置：

- 内存：至少 8 GB，推荐 12 GB 以上
- 磁盘：至少 15 GB 可用空间

检查 Docker 是否可用：

```bash
docker version
docker compose version
```

如果命令能正常输出版本号，说明 Docker 环境可用。

### 2. 获取代码并进入项目

如果是从 GitHub 克隆：

```bash
git clone <本仓库地址>
cd tongji_rag
```

如果老师拿到的是压缩包，请解压后进入 `tongji_rag` 目录，也就是包含 `docker-compose.yml` 的目录。

### 3. 配置环境变量

Windows PowerShell：

```powershell
Copy-Item .env.example .env
notepad .env
```

Linux/macOS：

```bash
cp .env.example .env
nano .env
```

至少需要修改以下配置：

```dotenv
DASHSCOPE_API_KEY=你的阿里云DashScope_API_Key
JWT_SECRET_KEY=一个较长的随机字符串
REDIS_PASSWORD=自定义Redis密码
MYSQL_PASSWORD=自定义MySQL普通用户密码
MYSQL_ROOT_PASSWORD=自定义MySQLRoot密码
MINIO_ROOT_PASSWORD=自定义MinIO密码
```

本地课程演示时，端口可以保持默认：

```dotenv
FRONTEND_PORT=80
BACKEND_PORT=8000
ATTU_PORT=8001
MYSQL_HOST_PORT=3306
REDIS_HOST_PORT=6379
MINIO_API_PORT=9000
MINIO_CONSOLE_PORT=9001
```

如果本机已经有 MySQL、Redis、Web 服务占用了端口，请修改对应端口。例如本机已有 MySQL 占用 `3306`，可改为：

```dotenv
MYSQL_HOST_PORT=3307
```

注意：不要把包含真实 API Key 或密码的 `.env` 提交到 GitHub。

### 4. 启动项目

Windows 推荐使用项目内的一键脚本：

```powershell
.\local_deploy_import.ps1
```

通用启动方式：

```bash
docker compose up -d --build
```

首次启动时会自动执行 `rag-init` 初始化容器，主要完成：

1. 等待 MySQL、Redis、Etcd、MinIO、Milvus 健康检查通过。
2. 创建或升级 MySQL 表结构。
3. 创建缺失的 Milvus 集合：`rag_faq`、`rag_standard`、`rag_knowledge`、`rag_internal`、`rag_person_info`。
4. 如果集合为空，导入 `backend/scripts/milvus_exports/` 中的种子 CSV。
5. 将 `backend/app/manual_faqs.py` 中的人工 FAQ 同步到 `managed_faqs` 表和 `rag_faq` 集合。
6. 写入演示用户、学生档案、教师档案、课表、考试信息和通知数据。
7. 启动 FastAPI 后端和 Vue/Nginx 前端。

查看启动状态：

```bash
docker compose ps
```

正常情况下，`rag-backend`、`rag-frontend`、`rag_mysql`、`rag_redis`、`milvus-standalone`、`milvus-attu` 等服务应处于 `running` 或 `healthy` 状态，`rag-init` 应为已成功退出。

### 5. 访问系统

默认地址如下：

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| SynapseQ 前端 | <http://localhost> | 老师主要访问这里 |
| 后端健康检查 | <http://localhost:8000/health> | 返回 `{"status":"ok"}` 表示后端正常 |
| Swagger API 文档 | <http://localhost:8000/docs> | 可调试后端接口 |
| Attu 向量库管理 | <http://localhost:8001> | 查看 Milvus 集合 |
| MinIO 控制台 | <http://localhost:9001> | 查看 Milvus 对象存储 |
| MySQL | `127.0.0.1:3306` | 可用 Navicat/DataGrip 连接 |

如果修改过 `.env` 中的端口，请以实际端口为准。

前端 Nginx 会把 `/api/` 代理到 `rag-backend:8000`，因此浏览器只需要访问前端地址即可。

## 演示账号

初始化脚本会写入以下账号：

| 身份 | 用户名 | 密码 | 说明 |
| --- | --- | --- | --- |
| 访客 | 无需账号 | 无需密码 | 点击“以访客身份进入” |
| 学生 | `zhangsan` | `123456` | 有 GPA、排名、课程等演示数据 |
| 教师 | `prof_li` | `123456` | 有教师档案和授权通知演示数据 |
| 学生 | `Lee` | `123456` | 有完整一周课表和考试信息 |
| 学生 | `zeq` | `050922` | 有电子信息学院档案和考试信息 |
| 管理员 | `admin` | `123456` | 登录后可打开右侧“管理后台” |

账号 ID 由数据库自增生成。如果在已有历史数据的环境中初始化，实际 `users.id` 可能与文档示例不同，请以 MySQL `users` 表为准。

## 推荐验收问题

访客模式可测试：

```text
同济大学创建于哪一年？
同济大学的校训是什么？
嘉定校区地址在哪里？
```

学生 `zhangsan` 可测试：

```text
我的绩点是多少？
我的学院是什么？
我的学院成立于哪一年？
最近有什么校内通知？
```

学生 `Lee` 可测试：

```text
查看我的课程表
我的考试信息
星期四晚上我有什么课？
```

学生 `zeq` 可测试：

```text
我的 GPA 是多少？
我的考试安排是什么？
光纤通信系统在哪里考试？
```

教师 `prof_li` 可测试：

```text
我的职称是什么？
最近有什么校内通知？
同济大学软件学院相关信息
```

管理员 `admin` 可测试：

1. 登录后点击右侧“管理后台”。
2. 在“FAQ 管理”中新增或修改 FAQ。
3. 在“资料库”中查看、编辑、删除文本块。
4. 在“爬取入库”中输入网页地址，预览文本块后保存到公开库或师生知识库。

## 权限与数据范围

| 身份 | 可访问数据 |
| --- | --- |
| 访客 | 公开 FAQ、`rag_standard` 公开知识 |
| 学生 | 访客数据、`rag_knowledge` 师生知识、本人档案、本人课表、本人考试、本人学生成绩、授权通知 |
| 教师 | 访客数据、`rag_knowledge` 师生知识、本人教师档案、本人课表、授权通知 |
| 管理员 | 问答入口、FAQ 管理、资料库管理、网页爬取入库 |

个人数据的关键约束：

- 登录成功后，JWT 的 `sub` 保存的是 MySQL `users.id`。
- 后端每次请求都会重新读取 MySQL 中的角色、院系和启用状态，不直接信任前端传入的用户 ID。
- `GET /api/v1/me/profile` 只返回当前登录用户自己的档案、课表、成绩和考试信息。
- 明确询问其他用户的 GPA、课表、考试等个人数据时，后端不会返回越权信息。
- 会话记录绑定到当前用户 ID，其他账号不能读取或继续该会话。

## 管理后台说明

管理员登录后，前端右侧会出现“管理后台”按钮。后台包含三个功能：

| 功能 | 说明 |
| --- | --- |
| 爬取入库 | 输入网页入口，预览可编辑文本块，并选择“访客可访问”或“仅限师生”后写入知识库 |
| FAQ 管理 | 新增、编辑、删除 FAQ；保存后同步到 MySQL 和 Milvus FAQ 集合 |
| 资料库 | 查看公开库、师生知识库和 FAQ；可编辑文本内容、访问范围，或删除记录 |

后台接口均以 `/api/v1/admin/...` 开头，只有 `role=admin` 的账号可以访问。

## 维护 FAQ 和校园网站数据

人工 FAQ 的代码维护文件为：

```text
backend/app/manual_faqs.py
```

修改后可重新构建启动：

```bash
docker compose up -d --build
```

也可以在服务运行时单独同步：

```bash
docker compose exec -T rag-backend python scripts/sync_manual_faqs.py
```

项目也提供命令行定向爬虫，用于更新预设校园网站数据：

```bash
docker compose exec -T rag-backend python scripts/crawl_requested_sources.py
```

默认来源与权限：

| 来源 | 写入集合 | 可见身份 |
| --- | --- | --- |
| 同济大学百度百科 | `rag_standard` | 访客、学生、教师 |
| 同济大学官网新闻 | `rag_standard` | 访客、学生、教师 |
| 同济大学计算机学院 | `rag_knowledge` | 学生、教师 |

只更新单个来源：

```bash
docker compose exec -T rag-backend python scripts/crawl_requested_sources.py --source "同济大学官网新闻"
```

如果 Windows 宿主机能访问网站，但 Docker 容器中出现 TLS 错误，可临时使用宿主机代理：

```powershell
# 终端 1
python backend/scripts/host_fetch_proxy.py

# 终端 2
docker compose exec -T `
  -e CRAWL_FETCH_PROXY=http://host.docker.internal:8765 `
  rag-backend python scripts/crawl_requested_sources.py
```

抓取完成后，在终端 1 按 `Ctrl+C` 关闭代理。

## 常用命令

查看容器：

```bash
docker compose ps
```

查看后端日志：

```bash
docker compose logs -f rag-backend
```

查看前端日志：

```bash
docker compose logs -f frontend
```

查看初始化日志：

```bash
docker compose logs rag-init
```

检查 Milvus 记录数：

```bash
docker compose exec -T rag-backend python scripts/check_milvus_counts.py
```

停止服务但保留数据：

```bash
docker compose down
```

重新启动：

```bash
docker compose up -d
```

重新构建代码并启动：

```bash
docker compose up -d --build
```

注意：不要执行 `docker compose down -v`，也不要删除 `data/`，除非明确想清空数据库和向量库。

## 使用 Navicat 查看 MySQL

Navicat 只能查看 MySQL 业务数据，不能查看 Milvus 向量集合。

新建 MySQL 连接时填写：

```text
连接名：SynapseQ MySQL
主机：127.0.0.1
端口：3306
用户名：rag_user
密码：.env 中 MYSQL_PASSWORD 的值
数据库：tongji_rag_db
```

如果 `.env` 中修改过 `MYSQL_HOST_PORT`，Navicat 端口也要对应修改。

常用表：

| 表 | 用途 |
| --- | --- |
| `users` | 用户、密码哈希、角色、院系、启用状态 |
| `student_profiles` | 学生档案 |
| `teacher_profiles` | 教师档案 |
| `course_schedules` | 课程表 |
| `student_grades` | 学生成绩 |
| `student_exams` | 学生考试安排 |
| `campus_notices` | 院系和受众过滤的校内通知 |
| `managed_faqs` | 后台可维护 FAQ |
| `crawl_tasks` | 爬取或导入任务 |
| `crawl_blocks` | 文本块元数据和 Milvus ID 映射 |

## 使用 Attu 查看 Milvus

打开 <http://localhost:8001> 后填写：

```text
Milvus Address: milvus-standalone:19530
Milvus Username: 留空
Milvus Password: 留空
Prometheus: 关闭
```

点击 `Connect` 后可以看到：

| 集合 | 用途 |
| --- | --- |
| `rag_faq` | 人工 FAQ |
| `rag_standard` | 访客可见公开数据 |
| `rag_knowledge` | 师生可见校园知识和爬取资料 |
| `rag_internal` | 兼容保留的内部资料 |
| `rag_person_info` | 兼容保留的个人向量资料 |

Attu 连接的是 Milvus，不是 MinIO，因此不能使用 MinIO 的账号密码登录 Attu。

## 常见问题

### 前端打不开

先查看容器状态：

```bash
docker compose ps
```

如果 `frontend` 没有启动，查看日志：

```bash
docker compose logs frontend
```

如果本机 80 端口被占用，修改 `.env`：

```dotenv
FRONTEND_PORT=8080
```

然后重新启动：

```bash
docker compose up -d
```

访问 <http://localhost:8080>。

### 后端健康检查失败

查看后端和初始化日志：

```bash
docker compose logs rag-init
docker compose logs rag-backend
```

常见原因是 `.env` 中密码为空、DashScope Key 未配置，或 MySQL/Milvus 尚未健康。

### MySQL 端口冲突

如果本机已经有 MySQL 占用 `3306`，修改：

```dotenv
MYSQL_HOST_PORT=3307
```

重启后，Navicat 也改连 `127.0.0.1:3307`。

### DashScope 不可访问

请确认：

- `.env` 中 `DASHSCOPE_API_KEY` 是真实可用 Key。
- 宿主机和 Docker 容器都能访问 DashScope。
- 本机代理、VPN、防火墙没有拦截 Docker 的 HTTPS 请求。

DashScope 暂不可用时，系统仍会尝试使用本地 FAQ、MySQL 结构化数据和 Milvus 关键词降级检索，但生成式回答质量会受影响。

## 更多文档

- [DEPLOYMENT.md](./DEPLOYMENT.md)：更详细的 Docker 部署和运维说明
- [USER_ACCOUNTS.md](./USER_ACCOUNTS.md)：演示账号、档案、课表说明
- [CRAWLED_WEBSITE_DEMO.md](./CRAWLED_WEBSITE_DEMO.md)：预设网站抓取内容和演示问题
- [接口文档.md](./接口文档.md)：接口说明
