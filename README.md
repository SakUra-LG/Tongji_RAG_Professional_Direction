# SynapseQ 同济大学 AI 校园问答系统

SynapseQ 是一个基于 RAG 的校园垂直问答系统。前端只提供一个智能问答入口，
后端根据登录身份自动决定检索范围，并通过 JWT、RBAC、Redis 会话、MySQL
用户 ID 和 Milvus 元数据过滤控制访问权限。

## 核心流程

1. 用户以师生账户登录，或以访客身份进入；用户不能手动切换数据模块。
2. 师生自动获得公开、校内和本人个人数据的检索权限；访客只能检索公开数据。
3. 每个问题首先由 LLM 输出结构化路由结果，包括意图、改写后的检索问题和允许
   读取的个人字段；不再先用关键词直接返回个人数据。
4. “我的学院是什么”属于个人字段查询；“我的学院成立于哪一年”会先利用当前
   身份把“我的学院”解析为具体学院，再进入校园知识库，而不会返回院系代码。
5. 只有 LLM 明确判定为个人事实查询时才读取 MySQL 个人档案、绩点和课表。
   模型不可用时使用保守的本地语义规则兜底，实体属性问题仍禁止读取个人值。
6. 未命中个人数据时，检索 FAQ、公开数据和授权的校内数据；`ACADEMIC_URLS`
   爬取结果存入 `rag_knowledge`，在统一入口中作为校内数据使用。
7. 系统合并向量相似度和关键词结果并进行重排序，再使用 RAG 生成回答。
8. 授权数据库没有相关内容时，才使用传统大模型的通用知识回答。
9. FastAPI 通过 SSE 将回答流式返回前端。

当 DashScope 暂时不可访问时，系统会对现有 Milvus 文本执行本地关键词降级检索；
若数据库命中但生成模型不可用，则直接返回最相关的数据库资料摘要。

### FAQ 快速回答

项目内置 51 条人工整理 FAQ，覆盖校史校区、访客入校、校园卡、校园网、统一认证、
教务选课、成绩课表、图书馆、宿舍后勤、医疗安全等常见问题。FAQ 会在向量检索和
大模型调用之前进行本地匹配，因此命中后可以直接返回标准答案。

FAQ 的唯一维护文件为：

```text
backend/app/manual_faqs.py
```

修改后重新运行 `docker compose up -d --build`，初始化容器会把 `rag_faq` 严格同步
为该文件的当前内容：新增项会写入、修改项会更新、已从文件删除的项也会从数据库
删除。也可以单独执行：

```bash
docker compose exec -T rag-backend python scripts/sync_manual_faqs.py
```

### 定向更新校园网站数据

项目提供可重复执行的定向爬虫：

```bash
docker compose exec -T rag-backend \
  python scripts/crawl_requested_sources.py
```

来源与权限映射：

| 来源 | Milvus 集合 | 可见身份 |
| --- | --- | --- |
| 同济大学百度百科 | `rag_standard` | 访客、学生、教师 |
| 同济大学官网 | `rag_standard` | 访客、学生、教师 |
| 计算机科学与技术学院官网 | `rag_knowledge` | 学生、教师 |

脚本会按来源前缀删除上一轮数据后重新写入，因此重复执行不会不断制造重复记录。
可使用 `--source "同济大学百度百科"` 等参数只更新一个来源。

若 Windows 宿主机能访问网站、但 Docker 容器出现 TLS 握手错误，可临时运行：

```powershell
# 终端 1
python backend/scripts/host_fetch_proxy.py

# 终端 2
docker compose exec -T `
  -e CRAWL_FETCH_PROXY=http://host.docker.internal:8765 `
  rag-backend python scripts/crawl_requested_sources.py
```

抓取完成后在终端 1 按 `Ctrl+C` 关闭代理。该代理只允许本项目配置的三个域名。

### 身份与数据范围

| 身份 | 自动连接的数据 |
| --- | --- |
| 访客 | `rag_standard`、`rag_faq` |
| 学生、教师 | 公开数据、`rag_knowledge` 校内爬取数据、本人 MySQL/`rag_person_info` 个人数据，以及按院系和受众过滤的通知 |

旧的 `public`、`academic`、`internal`、`personal` 后端路由暂时保留用于兼容，
当前前端统一使用 `type=auto`，不展示模块选择器。

### 个人信息如何识别

1. 登录时，后端根据用户名查询 `users`，验证密码后把该行主键 `users.id` 写入
   JWT 的 `sub` 字段。
2. 前端发送会话和问答请求时只携带 JWT，不传可由用户修改的个人 `user_id`。
3. 后端验签后，以 JWT 的 `sub` 重新查询 MySQL；角色、院系和启用状态均以数据库
   当前记录为准，不直接信任令牌中的旧声明。
4. 自动问答首先使用该 `users.id` 查询 `student_profiles`、`teacher_profiles`、
   `course_schedules` 和 `student_grades`。
5. 兼容的个人向量检索使用同一个 ID 生成 Milvus 过滤条件
   `user_id == '<当前用户ID>'`。
6. 会话同样绑定到当前用户 ID，其他账户不能读取或继续该会话。

因此新增学生或教师时，个人档案、课表和成绩表中的 `user_id` 必须填写该账户在
`users.id` 中的值。访客没有数字用户 ID，不会进入个人数据查询；明确询问其他用户
的绩点、课表等档案时，后端会拒绝返回。

## 技术栈

- 前端：Vue 3、Vite、Tailwind CSS、Nginx
- 后端：FastAPI、LangChain、DashScope
- 数据：Milvus、MySQL、Redis、MinIO、Etcd
- 部署：Docker Compose

## 一键启动

```powershell
Copy-Item .env.example .env
# 编辑 .env，填写 DASHSCOPE_API_KEY 并替换所有 change_me 配置
.\local_deploy_import.ps1
```

也可以直接运行：

```bash
docker compose up -d --build
```

首次启动会自动创建数据库表和 Milvus 集合，向空集合导入
`backend/scripts/milvus_exports` 中的种子数据，并同步去重后的人工 FAQ。后续重启
不会清空业务向量数据。

详细说明见 [DEPLOYMENT.md](./DEPLOYMENT.md)，实现审计见
[IMPLEMENTATION_AUDIT.md](./IMPLEMENTATION_AUDIT.md)，三个网站的抓取内容和演示
问题见 [CRAWLED_WEBSITE_DEMO.md](./CRAWLED_WEBSITE_DEMO.md)。

## 服务地址与登录信息

| 服务 | 默认地址 | 用户名 | 密码/说明 |
| --- | --- | --- | --- |
| SynapseQ 前端 | <http://localhost> | 使用下方演示账户 | 使用下方演示账户 |
| 后端 API | <http://localhost:8000> | 无 | 健康检查：`/health` |
| Swagger | <http://localhost:8000/docs> | 无 | 用于调试 API |
| Attu | <http://localhost:8001> | **留空** | **留空** |
| MinIO 控制台 | <http://localhost:9001> | `minioadmin` | `change_me_minio_secret` |
| MySQL | `localhost:3306` | `rag_user` | 查看 `.env` 的 `MYSQL_PASSWORD` |
| MySQL Root | `localhost:3306` | `root` | 查看 `.env` 的 `MYSQL_ROOT_PASSWORD` |
| Redis | `localhost:6379` | 无 | 查看 `.env` 的 `REDIS_PASSWORD` |
| Milvus SDK | `localhost:19530` | 无 | 当前未启用鉴权 |

### Attu 连接方法

打开 <http://localhost:8001> 后填写：

```text
Milvus Address: milvus-standalone:19530
Milvus Username: 留空
Milvus Password: 留空
Prometheus: 关闭
```

然后点击 `Connect`。Attu 与 Milvus 位于同一个 Docker 网络，因此这里使用容器服务名
`milvus-standalone`，不是 `localhost`。

> 注意：MinIO 的用户名和密码不能用于 Attu。Attu 连接的是 Milvus，不是 MinIO。

## 默认演示账户

| 角色 | 用户名 | 密码 |
| --- | --- | --- |
| 学生 | `zhangsan` | `123456` |
| 教师 | `prof_li` | `123456` |
| 学生 | `Lee` | `123456` |

默认账户仅用于课程演示，公开部署前必须修改或删除。
完整账号、档案和课表清单见 [USER_ACCOUNTS.md](./USER_ACCOUNTS.md)。

## 师生个人信息与通知权限

系统当前只保留学生和教师两种校内身份。访问学者身份、`dr_wang`/王学者账号及其个人数据已移除；学术问答模块仍保留，由学生和教师使用。

### 当前结构化字段

| 表 | 适用身份 | 主要字段 |
| --- | --- | --- |
| `users` | 学生、教师 | 用户名、姓名、角色、院系代码、启用状态 |
| `student_profiles` | 学生 | 学号、学院/系、专业、年级、班级、当前 GPA、专业排名、已获学分、校区 |
| `teacher_profiles` | 教师 | 工号、学院/系、职称、办公室、研究方向、校区 |
| `course_schedules` | 学生、教师 | 学期、课程号、课程名、任课教师、星期、起止节次、起止时间、地点、周次、选课状态 |
| `student_grades` | 学生 | 学期、课程号、课程名、成绩、课程绩点、学分 |
| `campus_notices` | 学生、教师 | 标题、正文、院系、受众、来源、发布时间、失效时间、启用状态 |

登录后可直接调用：

- `GET /api/v1/me/profile`：只根据 JWT 中的用户 ID 返回当前登录者自己的档案、课表和成绩。
- `GET /api/v1/notices`：只返回本院系或全校通知，并按 `audience=all/student/teacher` 再过滤一次。
- 个人问答和内部问答也会读取上述结构化记录。旧 `rag_internal` 数据不再作为通知权限依据，避免仅按院系过滤造成越权。

### 当前演示数据

张三（`users.id=1`）：

- 姓名：张三；身份：学生；院系代码：`CS`
- 学院/系：计算机系；当前 GPA：`3.85`；专业排名：`5`
- 已选课程：高级机器学习、云计算
- 星期、节次、地点、学期、学号、专业、年级、班级、学分等目前为空，等待补充
- “计算机系大模型培训通知”的受众是 `teacher`，因此张三无权查看

李教授（`users.id=2`）：

- 姓名：李教授；身份：教师；院系代码：`SE`
- 学院：软件学院；职称：教授
- 工号、办公室、研究方向、校区和授课安排目前为空，等待补充
- 可查看软件学院受众为 `all` 的“2025届毕业设计答辩公告”
- Milvus 中还保留一条原有演示文本：`李教授本月工资单详情：基本工资...`

Lee：

- 身份：学生；院系代码：`CS`；学院/系：计算机系
- 当前 GPA：`4.50`
- 已录入 `2025-2026-1` 学期周一至周四的 9 条课程安排
- 详细课表见 [USER_ACCOUNTS.md](./USER_ACCOUNTS.md)

Milvus 原有个人文本还包括张三的 GPA/排名和两门已选课程。结构化表是后续新增和维护个人资料的主数据源。

### 在 Navicat 中补充数据

先按下文“使用 Navicat 查看 MySQL”的方式连接，然后根据 `users.id` 写入对应表：

```sql
UPDATE student_profiles
SET student_no = '填写学号',
    major = '填写专业',
    grade_year = 2023,
    class_name = '填写班级',
    earned_credits = 0,
    campus = '填写校区'
WHERE user_id = 1;

UPDATE course_schedules
SET semester = '2025-2026-2',
    instructor = '填写教师',
    weekday = 1,
    start_section = 1,
    end_section = 2,
    location = '填写教室',
    week_range = '1-16周'
WHERE user_id = 1 AND course_name = '高级机器学习';

UPDATE teacher_profiles
SET employee_no = '填写工号',
    office = '填写办公室',
    research_direction = '填写研究方向',
    campus = '填写校区'
WHERE user_id = 2;
```

新增其他学生时，先向 `users` 写入账号和密码哈希，再使用新用户的 `id` 向 `student_profiles`、`course_schedules`、`student_grades` 写入数据。不要直接存储明文密码。

## 常用检查命令

```bash
# 查看全部容器
docker compose ps

# 查看向量数据库记录数
docker compose exec -T rag-backend python scripts/check_milvus_counts.py

# 严格同步 FAQ
docker compose exec -T rag-backend python scripts/sync_manual_faqs.py

# 更新三个指定网站
docker compose exec -T rag-backend python scripts/crawl_requested_sources.py

# 查看后端日志
docker compose logs -f rag-backend

# 查看前端日志
docker compose logs -f frontend
```

端口或密码经过修改时，以项目根目录 `.env` 中的实际配置为准。不要把包含真实
DashScope Key 或生产密码的 `.env` 提交到 Git。

## 使用 Navicat 查看 MySQL

Navicat 只能查看项目的 MySQL 业务数据，不能查看 Milvus 向量集合。新建
`MySQL` 连接时填写：

```text
连接名：SynapseQ MySQL
主机：127.0.0.1
端口：3306
用户名：rag_user
密码：change_me_mysql_user_secret
数据库：tongji_rag_db
```

SSH 和 SSL 均不需要开启。点击“测试连接”，成功后打开数据库
`tongji_rag_db`，可以看到：

- `users`：演示用户与角色
- `student_profiles`：学生结构化档案
- `teacher_profiles`：教师结构化档案
- `course_schedules`：师生课程与课表
- `student_grades`：学生课程成绩
- `campus_notices`：带院系和受众权限的通知
- `crawl_tasks`：爬取或导入任务
- `crawl_blocks`：导入文本块与 Milvus ID 映射

若修改过 `.env`，用户名、密码、数据库名和端口应以其中的
`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`、`MYSQL_HOST_PORT`
为准。

Milvus 向量数据请使用 <http://localhost:8001> 的 Attu 查看。
