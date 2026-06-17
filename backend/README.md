# Tongji RAG System

这是一个基于检索增强生成 (RAG) 技术的智能问答系统后端。该系统旨在支持多种业务场景（公共咨询、学术研究、内部行政、个人数据），并实现了基于角色的访问控制 (RBAC) 和流式对话生成。

## 项目简介

本项目采用现代化的 Python 异步技术栈构建，核心功能包括：

- **多场景检索管道**：针对不同用户角色和场景定制的检索策略（Public, Academic, Internal, Personal）。
- **智能查询优化**：基于 LLM 的查询重写和关键词重排序（Rerank）。
- **会话管理**：基于 Redis 的分布式会话存储，支持上下文记忆。
- **混合检索**：结合向量检索（Milvus）与关键词匹配，支持元数据过滤。
- **流式响应**：基于 Server-Sent Events (SSE) 的实时打字机效果输出。

## 技术栈

- **Web 框架**: FastAPI
- **语言模型**: Alibaba DashScope (Qwen/通义千问)
- **向量数据库**: Milvus
- **关系型数据库**: MySQL (Async SQLAlchemy)
- **缓存/消息队列**: Redis
- **编排框架**: LangChain

## 目录结构

```text
app/
├── components.py   # 核心组件（历史记录管理、向量检索器、LLM生成器）
├── config.py       # 环境配置与参数设置
├── database.py     # MySQL 数据库连接会话管理
├── dto.py          # Pydantic 数据传输对象定义
├── models_db.py    # SQLAlchemy 数据库模型定义
├── pipelines.py    # 具体的 RAG 业务流程实现 (Pipeline)
└── server.py       # FastAPI 路由入口与应用配置
```

## 功能模块

### 1. 业务管道 (Pipelines)

系统根据 API 请求的类型 (`type`) 路由到不同的处理管道：

- **PublicPipeline (公共场景)**
  - 优先匹配 `app/manual_faqs.py` 中的本地 FAQ，命中后不调用外部模型。
  - 本地未命中时继续检索 Milvus FAQ 库，命中阈值以上直接返回预设答案。
  - 未命中 FAQ 时，进行标准向量检索，使用关键词重排序优化 Top-K 结果。
- **AcademicPipeline (学术场景)**
  - 混合检索标准库与知识库。
  - 使用学术风格的 Prompt 模板，强调引用来源和综述性回答。
- **InternalPipeline (内部场景)**
  - 从 MySQL 通知表按部门 ID (`dept_id`) 和受众 (`audience`) 双重过滤。
  - 结合公共/学术资料，避免学生读取面向教师的院系通知。
- **PersonalPipeline (个人场景)**
  - 严格根据 User ID 读取结构化个人档案，并过滤个人向量集合。
  - 提供精确的数据陈述，不进行过度推断。

### 2. 核心组件

- **HistoryManager**: 管理用户会话历史，存储于 Redis，支持自动过期。
- **VectorRetriever**: 封装 Milvus 客户端，支持多集合检索和元数据过滤。
- **LLMGenerator**: 处理查询重写（Query Rewriting）和流式答案生成。

## 环境依赖

- Python 3.8+
- MySQL 5.7+
- Redis 6+
- Milvus 2.0+

## 安装与配置

### 1. 安装 Python 依赖

建议使用虚拟环境：

```bash
pip install requirements.txt -r
```

### 2. 环境配置

请设置环境变量以覆盖 `config.py` 中的默认值。建议修改 `.env.example` 文件：

### 3. 数据库初始化

确保 MySQL 和 Milvus 服务已启动，且相应的数据库和 Collection 已创建（Milvus Schema 需参考 `pipelines.py` 中的字段定义）。

## 运行服务

使用 Uvicorn 启动服务器：

```bash
python app/server.py
# 或者
uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后，Swagger API 文档地址为：`http://localhost:8000/docs`

## 权限说明

系统内置了基于角色的权限控制：

- **Guest**: 仅可访问 Public 模块，有速率限制。
- **Student/Teacher**: 可访问 Public, Academic, Internal (视部门而定), Personal 模块。
