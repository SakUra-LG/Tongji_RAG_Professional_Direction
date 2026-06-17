# SynapseQ Docker 部署说明

## 1. 环境要求

- Windows 10/11、Linux 或 macOS
- Docker Desktop，或 Docker Engine + Docker Compose Plugin
- 可访问阿里云 DashScope
- 建议至少 8 GB 内存、15 GB 可用磁盘

检查环境：

```bash
docker version
docker compose version
```

## 2. 配置环境变量

在项目根目录执行：

```powershell
Copy-Item .env.example .env
```

Linux/macOS：

```bash
cp .env.example .env
```

必须修改：

- `DASHSCOPE_API_KEY`
- `JWT_SECRET_KEY`
- `REDIS_PASSWORD`
- `MYSQL_PASSWORD`
- `MYSQL_ROOT_PASSWORD`
- `MINIO_ROOT_PASSWORD`

生成随机 JWT 密钥的 PowerShell 示例：

```powershell
-join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
```

远程部署时，将 `CORS_ORIGINS` 改为实际域名，例如：

```dotenv
CORS_ORIGINS=https://qa.example.edu.cn
```

## 3. 一键启动

Windows 推荐：

```powershell
.\local_deploy_import.ps1
```

通用方式：

```bash
docker compose up -d --build
```

启动顺序为：

1. MySQL、Redis、Etcd、MinIO、Milvus 启动并通过健康检查。
2. `rag-init` 创建 SQL 表和缺失的 Milvus 集合。
3. `rag-init` 检查集合记录数，只向空集合导入 CSV 种子数据。
4. `rag-init` 将 `backend/app/manual_faqs.py` 中的 FAQ 去重同步到 `rag_faq`。
5. 后端启动并通过 `/health` 检查。
6. 前端 Nginx 启动，通过 `/api` 同源代理后端。

该流程是幂等的，普通重启不会删除已有数据。

## 4. 查看状态和日志

```bash
docker compose ps
docker compose logs -f rag-init
docker compose logs -f rag-backend
docker compose logs -f frontend
```

所有服务正常后访问：

- 前端：`http://localhost:${FRONTEND_PORT}`
- Swagger：`http://localhost:${BACKEND_PORT}/docs`
- Attu：`http://localhost:${ATTU_PORT}`
- MinIO：`http://localhost:${MINIO_CONSOLE_PORT}`

未配置端口变量时，默认分别为 `80`、`8000`、`8001`、`9001`。

### 服务凭据速查

| 服务 | 用户名 | 密码 |
| --- | --- | --- |
| Attu / Milvus | 留空 | 留空 |
| MinIO | `.env` 的 `MINIO_ROOT_USER`，未设置时为 `minioadmin` | `.env` 的 `MINIO_ROOT_PASSWORD`，未设置时为 `change_me_minio_secret` |
| MySQL 普通用户 | `.env` 的 `MYSQL_USER` | `.env` 的 `MYSQL_PASSWORD` |
| MySQL 管理员 | `root` | `.env` 的 `MYSQL_ROOT_PASSWORD` |
| Redis | 无用户名 | `.env` 的 `REDIS_PASSWORD` |

当前 Compose 未开启 Milvus 鉴权。Attu 登录页面应填写：

```text
Milvus Address: milvus-standalone:19530
Milvus Username: 留空
Milvus Password: 留空
Prometheus: 关闭
```

点击 `Connect` 后即可查看 `rag_standard`、`rag_knowledge`、`rag_faq`、
`rag_internal` 和 `rag_person_info`。

### SynapseQ 演示账户

| 角色 | 用户名 | 密码 | 自动检索范围 |
| --- | --- | --- | --- |
| 学生 | `zhangsan` | `123456` | 公开、校内爬取、本人个人信息、授权通知 |
| 教师 | `prof_li` | `123456` | 公开、校内爬取、本人个人信息、授权通知 |
| 学生 | `Lee` | `123456` | 公开、校内爬取、本人个人信息、授权通知 |
| 访客 | 无需账户 | 无需密码 | 仅公开数据 |

前端不提供模块切换。每个问题先由 LLM 生成结构化路由，再决定读取 FAQ、个人
档案、校内数据或公开数据。只有 `personal_fact` 路由可以读取个人结构化信息。
例如“我的学院是什么”读取本人学院；“我的学院成立于哪一年”会将学院指代解析为
具体学院后查询校园知识库。LLM 服务不可用时使用保守规则兜底。

若部署机器暂时无法连接 DashScope，个人 MySQL 查询仍可正常使用，Milvus 会降级为
本地关键词检索并返回命中的资料摘要；向量语义检索和通用模型回答会在网络恢复后
自动恢复。

### 查看当前 `.env` 配置

PowerShell：

```powershell
Get-Content .env | Select-String '^(FRONTEND_PORT|BACKEND_PORT|ATTU_PORT|MINIO_CONSOLE_PORT|MINIO_ROOT_USER|MYSQL_USER|MYSQL_DATABASE)='
```

为避免密码进入终端历史和截图，密码类变量建议直接在编辑器中查看 `.env`。

### 使用 Navicat 连接 MySQL

在 Navicat 中选择“连接” -> “MySQL”，填写：

| Navicat 字段 | 当前默认值 |
| --- | --- |
| 连接名 | `SynapseQ MySQL` |
| 主机 | `127.0.0.1` |
| 端口 | `3306` |
| 用户名 | `rag_user` |
| 密码 | `change_me_mysql_user_secret` |
| 初始数据库 | `tongji_rag_db` |

保持 SSH 和 SSL 关闭，点击“测试连接”。连接成功后可查看：

| 表 | 用途 |
| --- | --- |
| `users` | 用户、密码哈希、角色、部门 |
| `crawl_tasks` | 爬取与 CSV 导入任务记录 |
| `crawl_blocks` | 文本块元数据及对应的 Milvus ID |

如果测试连接失败，先检查：

```bash
docker compose ps mysql
docker compose port mysql 3306
```

当前端口映射应为 `0.0.0.0:3306`。如果宿主机已有其他 MySQL 占用
`3306`，修改 `.env`：

```dotenv
MYSQL_HOST_PORT=3307
```

重新运行 `docker compose up -d`，然后把 Navicat 端口改为 `3307`。

Navicat 不支持直接查看 Milvus 向量集合。向量数据应通过 Attu 查看。

## 5. 查看向量数据库数量

```bash
docker compose exec -T rag-backend python scripts/check_milvus_counts.py
```

本次审计读取到的现有数据为：

| 集合 | 记录数 |
| --- | ---: |
| `rag_standard` | 502 |
| `rag_knowledge` | 260 |
| `rag_faq` | 51 |
| `rag_internal` | 2 |
| `rag_person_info` | 3 |
| 合计 | 818 |

本次新增抓取数据共 113 条分块：同济大学百度百科 37 条、同济大学官网 42 条、
计算机科学与技术学院官网 34 条。另有 51 条人工 FAQ 保存在
`backend/app/manual_faqs.py`，启动时会严格镜像该文件，包含删除数据库中已移除的
FAQ。
当前持久化库中超过 CSV 数量的公开、校内、内部和个人数据在重置前仍需备份。

### 维护或同步 FAQ

编辑 `backend/app/manual_faqs.py`，每条记录包含标准问题 `q`、标准答案 `a`、
来源 `source` 和常见问法 `aliases`。修改后执行：

```bash
docker compose up -d --build rag-backend
```

或在已构建的容器中单独同步：

```bash
docker compose exec -T rag-backend python scripts/sync_manual_faqs.py
```

### 更新指定网站数据

```bash
docker compose exec -T rag-backend \
  python scripts/crawl_requested_sources.py
```

默认更新百度百科同济大学词条、同济大学官网和计算机科学与技术学院官网。
前两项写入公开集合 `rag_standard`，学院网站写入师生可见的
`rag_knowledge`。脚本按来源覆盖旧分块，可以重复执行。

仅更新一个来源：

```bash
docker compose exec -T rag-backend \
  python scripts/crawl_requested_sources.py --source "同济大学官网新闻"
```

Windows 下若容器访问网站出现 TLS 错误，先在项目根目录启动受限宿主机代理：

```powershell
python backend/scripts/host_fetch_proxy.py
```

再在另一个终端执行：

```powershell
docker compose exec -T `
  -e CRAWL_FETCH_PROXY=http://host.docker.internal:8765 `
  rag-backend python scripts/crawl_requested_sources.py
```

抓取完成后关闭代理。

## 6. 停止与重启

停止容器但保留数据：

```bash
docker compose down
```

重启：

```bash
docker compose up -d
```

重新构建代码：

```bash
docker compose up -d --build
```

不要使用 `docker compose down -v`，也不要删除 `data/`，除非明确要永久清空数据库。

## 7. 备份

导出 Milvus：

```bash
docker compose exec -T rag-backend python scripts/export_milvus_to_csv.py \
  --output-dir /app/scripts/milvus_exports_backup \
  --include-vector \
  --for-import
```

MySQL、Redis、Milvus、MinIO 和 Etcd 的持久化文件位于项目根目录的 `data/`。停服后整体复制该目录，可以作为本地完整备份。

## 8. 显式重置向量库

以下操作会删除五个集合中的全部数据，只能在确认已有备份时执行：

```bash
docker compose exec -T rag-backend python scripts/reset_milvus_collections.py
docker compose exec -T rag-backend python scripts/import_csv_to_milvus.py
```

正常部署和升级不需要执行这两个命令。

## 9. 生产部署建议

- 使用 HTTPS 反向代理，不要直接暴露 HTTP。
- 将 MySQL、Redis、Milvus 和 MinIO 端口限制在内网。
- 删除默认演示账户并使用真实统一身份认证。
- 轮换曾经出现在仓库中的 DashScope Key。
- 使用 Docker Secret 或主机密钥管理服务保存生产密钥。
- 为 `data/` 配置定期备份和恢复演练。
- 将 MinIO、Milvus、Etcd 镜像升级到经过兼容性测试的新版本。

## 10. 常见问题

查看初始化失败原因：

```bash
docker compose logs rag-init
```

端口冲突时修改 `.env` 中的 `FRONTEND_PORT`、`BACKEND_PORT`、`ATTU_PORT` 等变量，再重新启动。

Milvus 长时间未就绪时：

```bash
docker compose logs milvus-standalone
docker compose logs minio
docker compose logs etcd
```

前端能打开但 API 失败时：

```bash
curl http://localhost:8000/health
docker compose logs rag-backend
```

若宿主机浏览器或 `curl.exe` 可以访问 DashScope，但容器或 Python 报
`SSL: UNEXPECTED_EOF_WHILE_READING`，通常是本机 VPN、代理、防火墙或安全软件拦截了
OpenSSL 流量。请放行 `dashscope.aliyuncs.com:443`，或为 Docker/Python 配置可用的
HTTPS 代理；不要通过关闭证书校验规避该问题。
