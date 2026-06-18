import json
import redis
import time
import uuid
import datetime
import jieba
import re
from typing import List, Dict, Any, Generator
from pymilvus import MilvusClient
from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import Session
from langchain_community.chat_models import ChatTongyi
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser

from app.config import settings
from app.dto import (
    ChatMessage,
    Document,
    QueryRoute,
    RewrittenQuery,
    SessionSchema,
    UserContext,
)
from app.manual_faqs import MANUAL_FAQS
from app.models_db import (
    CampusNotice,
    CourseSchedule,
    ManagedFAQ,
    StudentExam,
    StudentGrade,
    StudentProfile,
    TeacherProfile,
    User,
)

# --- 历史记录管理器 (Redis) ---
class HistoryManager:
    """
    基于 Redis 的会话历史管理器。
    负责处理会话的创建、列表获取、消息存储以及上下文检索。
    """
    def __init__(self):
        # 初始化 Redis 连接
        self.redis = redis.Redis(
            host=settings.REDIS_HOST, 
            port=settings.REDIS_PORT, 
            password=settings.REDIS_PASSWORD,
            decode_responses=True  # 自动解码为字符串
        )
        self.max_turns = 6       # LLM 上下文保留最近 3 轮对话
        self.ttl = 3600 * 24 * 7 # 会话过期时间设置为 7 天

    def create_session(self, user_id: str, session_type: str, title: str = "新对话") -> str:
        """
        创建会话时记录 session_type
        """
        session_id = str(uuid.uuid4())
        timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        meta = {
            "session_id": session_id,
            "title": title,
            "type": session_type,  # 存储类型
            "created_at": timestamp_str
        }
        
        user_key = f"user_sessions:{user_id}"
        self.redis.hset(user_key, session_id, json.dumps(meta, ensure_ascii=False))
        self.redis.expire(user_key, self.ttl)
        
        return session_id

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """
        删除用户的特定会话。
        1. 从 user_sessions:{user_id} 哈希表中移除元数据
        2. 删除 chat_history:{session_id} 列表数据
        """
        user_key = f"user_sessions:{user_id}"
        
        # 1. 尝试从用户的会话列表中删除该 session_id
        # hdel 返回删除的个数，如果为 0 说明该用户没有这个会话，或者会话不存在
        deleted_count = self.redis.hdel(user_key, session_id)
        
        if deleted_count > 0:
            # 2. 如果归属关系确认，删除实际的聊天记录 key
            history_key = f"chat_history:{session_id}"
            self.redis.delete(history_key)
            return True
            
        return False

    def get_user_sessions(self, user_id: str, type_filter: str = None) -> List[SessionSchema]:
        """
        只返回对应类型的会话
        """
        user_key = f"user_sessions:{user_id}"
        if not self.redis.exists(user_key):
            return []
        
        raw_data = self.redis.hgetall(user_key)
        sessions = []
        for sid, meta_json in raw_data.items():
            try:
                meta = json.loads(meta_json)
                # 兼容旧数据（如果没有 type 字段，默认归为 public）
                s_type = meta.get("type", "public")
                
                # 核心过滤逻辑
                if type_filter and s_type != type_filter:
                    continue
                    
                # 构造对象时补全 type
                meta['type'] = s_type
                sessions.append(SessionSchema(**meta))
            except:
                continue
        
        sessions.sort(key=lambda x: x.created_at, reverse=True)
        return sessions

    def check_session_type(self, user_id: str, session_id: str, required_type: str) -> bool:
        """
        校验会话类型是否匹配
        """
        user_key = f"user_sessions:{user_id}"
        meta_json = self.redis.hget(user_key, session_id)
        if not meta_json:
            return False # 会话不存在
            
        try:
            meta = json.loads(meta_json)
            # 兼容旧数据，默认 public
            current_type = meta.get("type", "public")
            return current_type == required_type
        except:
            return False

    def owns_session(self, user_id: str, session_id: str) -> bool:
        """Return whether the session belongs to the current user."""
        return bool(self.redis.hexists(f"user_sessions:{user_id}", session_id))

    def get_session_history_detail(self, session_id: str) -> List[ChatMessage]:
        """
        获取指定会话的完整历史消息记录。
        通常用于前端页面回显聊天记录。
        """
        key = f"chat_history:{session_id}"
        if not self.redis.exists(key):
            return []
            
        # 获取列表中的所有消息
        history_json = self.redis.lrange(key, 0, -1)
        messages = []
        for item in history_json:
            try:
                msg_obj = json.loads(item)
                messages.append(ChatMessage(
                    role=msg_obj.get('role'),
                    content=msg_obj.get('content'),
                    timestamp=msg_obj.get('timestamp', 0.0)
                ))
            except:
                continue
        return messages

    def update_session_title(self, user_id: str, session_id: str, title: str):
        """
        更新指定会话的标题。
        """
        user_key = f"user_sessions:{user_id}"
        meta_json = self.redis.hget(user_key, session_id)
        if meta_json:
            meta = json.loads(meta_json)
            meta['title'] = title
            self.redis.hset(user_key, session_id, json.dumps(meta, ensure_ascii=False))

    def get_recent_turns(self, session_id: str) -> List[Any]:
        """
        获取最近 N 轮对话记录，并转换为 LangChain 消息对象格式。
        用于构建 LLM 的 Prompt 上下文。
        """
        key = f"chat_history:{session_id}"
        if not self.redis.exists(key):
            return []
            
        # 仅获取最近 max_turns 条记录
        history_json = self.redis.lrange(key, -self.max_turns, -1) 
        messages = []
        for item in history_json:
            try:
                msg_obj = json.loads(item)
                if msg_obj['role'] == 'user':
                    messages.append(HumanMessage(content=msg_obj['content']))
                else:
                    messages.append(AIMessage(content=msg_obj['content']))
            except:
                continue
        return messages

    def append_user_message(self, session_id: str, content: str):
        """
        将用户消息追加到会话历史中。
        """
        key = f"chat_history:{session_id}"
        msg = json.dumps({
            "role": "user", 
            "content": content, 
            "timestamp": time.time()
        }, ensure_ascii=False)
        self.redis.rpush(key, msg)
        self.redis.expire(key, self.ttl) # 每次交互时刷新会话有效期

    def append_ai_message(self, session_id: str, content: str):
        """
        将 AI 回复追加到会话历史中。
        """
        key = f"chat_history:{session_id}"
        msg = json.dumps({
            "role": "assistant", 
            "content": content, 
            "timestamp": time.time()
        }, ensure_ascii=False)
        self.redis.rpush(key, msg)
        self.redis.expire(key, self.ttl)

# --- 向量检索器 (Milvus) ---
def parse_source_reference(source: str) -> Dict[str, str]:
    source = source or ""
    match = re.search(r"(https?://\S+)", source)
    url = match.group(1).rstrip("，。；;)") if match else ""
    title = source.replace(url, "").strip(" |｜-/")
    return {"source_title": title or source or "资料库", "source_url": url}


class VectorRetriever:
    KEYWORD_STOP_WORDS = {
        "一下",
        "什么",
        "介绍",
        "关于",
        "同济",
        "同济大学",
        "哪个",
        "哪些",
        "哪一",
        "哪年",
        "多少",
        "如何",
        "怎么",
        "哪里",
        "当前",
        "现在",
        "目前",
        "相关",
    }

    def __init__(self):
        self.embedder = DashScopeEmbeddings(
            model=settings.EMBEDDING_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY
        )
        self.client = MilvusClient(
            uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
        )

    def search(self, query_text: str, collections: List[str], top_k: int = 3, filters: str = "") -> List[Document]:
            """
            通用检索方法：支持检索普通库(text)和FAQ库(answer)
            """
            try:
                query_vector = self.embedder.embed_query(query_text)
            except Exception as e:
                print(f"Embedding error: {e}")
                return self.keyword_search(query_text, collections, top_k, filters)

            all_results = []
            
            try:
                existing_cols = self.client.list_collections()
            except Exception as e:
                print(f"List collections error: {e}")
                return []

            for col_name in collections:
                try:
                    if col_name not in existing_cols:
                        continue
                    
                    # 【修正点】严格对照 fix_milvus.py 定义的 Schema
                    if "faq" in col_name.lower():
                        # FAQ 只有 question, answer, source
                        target_fields = ["question", "answer", "source"]
                    else:
                        # RAG 库有 text, source, dept_id, user_id
                        target_fields = ["text", "source", "dept_id", "user_id"]

                    res = self.client.search(
                        collection_name=col_name,
                        data=[query_vector],
                        limit=top_k,
                        filter=filters, 
                        output_fields=target_fields 
                    )
                    
                    for hit in res[0]:
                        entity = hit['entity']
                        
                        is_faq = "answer" in entity and entity["answer"]
                        
                        # 构造内容：
                        content = entity.get("question") if is_faq else entity.get("text", "")
                        
                        doc = Document(
                            id=str(hit['id']),
                            content=content,
                            score=hit['distance'], 
                            source=entity.get('source', col_name),
                            metadata={
                                "is_faq": is_faq,
                                "answer": entity.get("answer", ""), 
                                "dept_id": entity.get("dept_id", ""),
                                "user_id": entity.get("user_id", ""),
                                **parse_source_reference(entity.get("source", col_name)),
                                **entity
                            }
                        )
                        all_results.append(doc)
                except Exception as e:
                    print(f"Search error in {col_name}: {e}")

            keyword_results = self.keyword_search(
                query_text,
                collections,
                top_k,
                filters,
            )
            merged: Dict[tuple[str, str], Document] = {}
            for doc in all_results + keyword_results:
                key = (doc.id, doc.source)
                current = merged.get(key)
                if current is None or doc.score > current.score:
                    merged[key] = doc

            ranked_results = sorted(
                merged.values(),
                key=lambda item: item.score,
                reverse=True,
            )
            return ranked_results[:top_k]

    def keyword_search(
        self,
        query_text: str,
        collections: List[str],
        top_k: int = 3,
        filters: str = "",
    ) -> List[Document]:
        """Search Milvus text fields when the remote embedding service is unavailable."""
        keywords = {
            word.strip().lower()
            for word in jieba.lcut_for_search(query_text)
            if len(word.strip()) > 1
            and word.strip().lower() not in self.KEYWORD_STOP_WORDS
        }
        if not keywords:
            return []

        try:
            existing_cols = set(self.client.list_collections())
        except Exception as exc:
            print(f"List collections error: {exc}")
            return []

        results: List[Document] = []
        for col_name in collections:
            if col_name not in existing_cols:
                continue

            is_faq_collection = "faq" in col_name.lower()
            target_fields = (
                ["id", "question", "answer", "source"]
                if is_faq_collection
                else ["id", "text", "source", "dept_id", "user_id"]
            )
            try:
                rows = self.client.query(
                    collection_name=col_name,
                    filter=filters,
                    output_fields=target_fields,
                    limit=1000,
                )
            except Exception as exc:
                print(f"Keyword query error in {col_name}: {exc}")
                continue

            for row in rows:
                question = row.get("question", "")
                answer = row.get("answer", "")
                text = row.get("text", "")
                searchable = f"{question}\n{answer}\n{text}".lower()
                matched = {word for word in keywords if word in searchable}
                if not matched:
                    continue

                score = len(matched) / len(keywords)
                is_faq = bool(answer)
                results.append(
                    Document(
                        id=str(row.get("id", "")),
                        content=question if is_faq else text,
                        score=score,
                        source=row.get("source", col_name),
                        metadata={
                            "is_faq": is_faq,
                            "answer": answer,
                            "dept_id": row.get("dept_id", ""),
                            "user_id": row.get("user_id", ""),
                            **parse_source_reference(row.get("source", col_name)),
                            **row,
                        },
                    )
                )

        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

class LocalFAQRetriever:
    """Match curated FAQ entries without embeddings or an external model."""

    STOP_WORDS = {
        "一下",
        "什么",
        "怎么",
        "如何",
        "哪里",
        "哪个",
        "哪些",
        "是否",
        "可以",
        "同济",
        "同济大学",
        "学校",
        "请问",
    }

    def __init__(self):
        database_url = (
            f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
            f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}"
            "?charset=utf8mb4"
        )
        self.engine = create_engine(database_url, pool_pre_ping=True)

    def _load_entries(self) -> List[Dict[str, Any]]:
        try:
            with Session(self.engine) as session:
                rows = list(
                    session.scalars(
                        select(ManagedFAQ)
                        .where(ManagedFAQ.is_active.is_(True))
                        .order_by(ManagedFAQ.id.asc())
                    ).all()
                )
            if rows:
                return [
                    {
                        "q": row.question,
                        "a": row.answer,
                        "source": row.source or "FAQ",
                        "aliases": [
                            item.strip()
                            for item in (row.aliases or "").splitlines()
                            if item.strip()
                        ],
                    }
                    for row in rows
                ]
        except Exception as exc:
            print(f"Managed FAQ load failed, using local fallback: {exc}")
        return MANUAL_FAQS

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[\W_]+", "", text.lower(), flags=re.UNICODE)

    @classmethod
    def _tokens(cls, text: str) -> set[str]:
        return {
            word.strip().lower()
            for word in jieba.lcut_for_search(text)
            if len(word.strip()) > 1
            and word.strip().lower() not in cls.STOP_WORDS
        }

    @classmethod
    def is_specific_faq_query(cls, query: str) -> bool:
        normalized = cls._normalize(query)
        query_tokens = cls._tokens(query)
        if not query_tokens:
            return False
        if len(normalized) <= 4 and not any(
            marker in query
            for marker in ("校训", "地址", "校区", "邮箱", "canvas", "入校", "充值")
        ):
            return False
        return True

    def match(self, query: str) -> Document | None:
        normalized_query = self._normalize(query)
        query_tokens = self._tokens(query)
        if not self.is_specific_faq_query(query):
            return None
        best_entry = None
        best_score = 0.0

        for index, entry in enumerate(self._load_entries()):
            variants = [entry["q"], *entry.get("aliases", [])]
            for variant in variants:
                normalized_variant = self._normalize(variant)
                if (
                    normalized_query == normalized_variant
                    or (
                        len(normalized_query) >= 4
                        and len(normalized_variant) >= 4
                        and normalized_variant in normalized_query
                    )
                ):
                    return self._to_document(index, entry, 1.0)

                variant_tokens = self._tokens(variant)
                if not query_tokens or not variant_tokens:
                    continue
                overlap = len(query_tokens & variant_tokens)
                score = (2 * overlap) / (len(query_tokens) + len(variant_tokens))
                if overlap >= 2 and score > best_score:
                    best_entry = (index, entry)
                    best_score = score

        if best_entry and best_score >= 0.62:
            return self._to_document(*best_entry, best_score)
        return None

    @staticmethod
    def _to_document(index: int, entry: Dict[str, Any], score: float) -> Document:
        return Document(
            id=f"manual-faq-{index + 1}",
            content=entry["q"],
            score=score,
            source=entry["source"],
            metadata={
                "is_faq": True,
                "answer": entry["a"],
                "local_faq": True,
            },
        )


# --- LLM 生成器 (LangChain) ---
class QueryIntentRouter:
    """Fallback router used only when the LLM intent analyzer is unavailable."""

    PROCEDURE_MARKERS = (
        "如何",
        "怎么",
        "怎样",
        "怎么办",
        "在哪里",
        "哪里查",
        "哪儿查",
        "去哪查",
        "查询方式",
        "查询方法",
        "查看方式",
        "查看方法",
        "办理流程",
        "操作流程",
        "入口",
    )
    PERSONAL_FIELDS = (
        "绩点",
        "gpa",
        "排名",
        "姓名",
        "名字",
        "身份",
        "角色",
        "学号",
        "工号",
        "专业",
        "年级",
        "班级",
        "学分",
        "成绩",
        "校区",
        "学院",
        "院系",
        "职称",
        "办公室",
        "研究方向",
        "研究领域",
        "课表",
        "课程表",
        "上课",
        "课程安排",
        "个人信息",
        "个人档案",
    )
    SELF_MARKERS = ("我", "本人", "自己", "个人", "当前登录")
    VALUE_MARKERS = (
        "多少",
        "是什么",
        "是几",
        "当前",
        "现在",
        "查看我的",
        "告诉我",
        "列出",
        "显示",
    )
    ENTITY_ATTRIBUTE_MARKERS = (
        "成立",
        "创办",
        "建立",
        "历史",
        "沿革",
        "介绍",
        "地址",
        "位置",
        "院长",
        "领导",
        "师资",
        "专业设置",
        "有哪些专业",
        "就业",
        "培养方案",
        "课程设置",
        "科研",
        "新闻",
        "通知",
        "排名怎么样",
        "什么时候成立",
        "哪一年成立",
        "成立于哪一年",
    )
    # Fallback only: the normal auto pipeline asks the LLM router to choose
    # personal_field and personal_filters first. These patterns are used only
    # when that router is unavailable or by legacy compatibility helpers.
    PERSONAL_FIELD_PATTERNS = (
        ("gpa", ("绩点", "gpa")),
        ("major_rank", ("专业排名", "我的排名")),
        ("name", ("姓名", "名字")),
        ("role", ("身份", "角色")),
        ("student_no", ("学号",)),
        ("employee_no", ("工号",)),
        ("major", ("我的专业", "所学专业")),
        ("grade_year", ("年级",)),
        ("class_name", ("班级",)),
        ("earned_credits", ("已获学分", "总学分")),
        ("campus", ("我的校区", "所在校区")),
        ("college", ("我的学院", "所属学院", "所在学院", "哪个学院", "哪个系")),
        ("title", ("职称",)),
        ("office", ("办公室",)),
        ("research_direction", ("研究方向", "研究领域")),
        (
            "schedule",
            (
                "课表",
                "课程表",
                "课程安排",
                "有什么课",
                "有什么课程",
                "今天有什么课",
                "明天有什么课",
                "什么时候上课",
            ),
        ),
        ("grades", ("课程成绩", "我的成绩")),
        ("exams", ("考试信息", "期末考试", "考试安排", "考试科目", "考试时间", "考试地点", "考试方式", "考查")),
        ("profile", ("个人信息", "个人档案", "我的信息", "我的档案")),
    )
    UNDER_SPECIFIED_ENTITY_QUERIES = {
        "\u540c\u6d4e",
        "\u540c\u6d4e\u5927\u5b66",
        "\u5b66\u6821",
        "\u6821\u56ed",
    }

    @staticmethod
    def _normalize_short_query(query: str) -> str:
        return re.sub(r"[\W_]+", "", query.lower(), flags=re.UNICODE)

    @classmethod
    def is_under_specified_query(cls, query: str) -> bool:
        normalized = cls._normalize_short_query(query)
        return normalized in cls.UNDER_SPECIFIED_ENTITY_QUERIES

    @classmethod
    def has_schedule_time_constraint(cls, query: str) -> bool:
        normalized = query.lower()
        return bool(
            re.search(
                r"(周[一二三四五六日天1-7]|星期[一二三四五六日天1-7]|礼拜[一二三四五六日天]|"
                r"今天|明天|后天|上午|下午|晚上|晚间|中午|早上|"
                r"\d{1,2}[:：]\d{2}|\d{1,2}点)",
                normalized,
            )
        )

    @classmethod
    def has_schedule_lookup_intent(cls, query: str) -> bool:
        normalized = query.lower()
        return any(
            marker in normalized
            for marker in (
                "课表",
                "课程表",
                "课程安排",
                "课程",
                "有什么课",
                "哪节课",
                "上什么课",
                "什么课",
            )
        )

    @classmethod
    def has_course_explanation_intent(cls, query: str) -> bool:
        normalized = query.lower()
        return any(
            marker in normalized
            for marker in (
                "作用",
                "用来干什么",
                "用来做什么",
                "干什么的",
                "做什么的",
                "是干什么",
                "是什么",
                "介绍",
                "意义",
                "用途",
            )
        ) and any(marker in normalized for marker in ("课", "课程", "那节", "这节"))

    @classmethod
    def is_entity_attribute_query(cls, query: str) -> bool:
        normalized = query.lower().strip()
        owned_entity = any(
            marker in normalized
            for marker in (
                "我的学院",
                "我所在的学院",
                "我所属的学院",
                "我的院系",
                "我的专业",
                "我的校区",
            )
        )
        return owned_entity and any(
            marker in normalized for marker in cls.ENTITY_ATTRIBUTE_MARKERS
        )

    @classmethod
    def detect_personal_field(cls, query: str) -> str | None:
        normalized = query.lower().strip()
        if cls.is_entity_attribute_query(normalized):
            return None
        for field, markers in cls.PERSONAL_FIELD_PATTERNS:
            if any(marker in normalized for marker in markers):
                return field
        return None

    @staticmethod
    def _canonical_college(context: Dict[str, Any]) -> str:
        dept_id = str(context.get("dept_id") or "").upper()
        college = str(context.get("college_name") or "").strip()
        if dept_id == "CS" or "计算机" in college:
            return "同济大学计算机科学与技术学院"
        if dept_id == "SE" or "软件" in college:
            return "同济大学软件学院"
        return college

    @classmethod
    def _resolve_owned_entity(
        cls,
        query: str,
        context: Dict[str, Any],
    ) -> str:
        college = cls._canonical_college(context)
        if not college:
            return query
        rewritten = query
        for marker in (
            "我所在的学院",
            "我所属的学院",
            "我的学院",
            "我的院系",
        ):
            rewritten = rewritten.replace(marker, college)
        return rewritten

    @classmethod
    def analyze_fallback(
        cls,
        query: str,
        context: Dict[str, Any] | None = None,
    ) -> QueryRoute:
        context = context or {}
        normalized = query.lower().strip()
        if cls.is_under_specified_query(query):
            return QueryRoute(
                intent="clarification",
                rewritten_query=query,
                confidence=0.95,
            )

        if len(normalized) <= 3 and normalized in {"学院", "专业", "课程", "成绩"}:
            return QueryRoute(
                intent="clarification",
                rewritten_query=query,
                confidence=0.9,
            )

        if (
            cls.has_schedule_time_constraint(query)
            and not cls.has_schedule_lookup_intent(query)
            and not cls.has_course_explanation_intent(query)
        ):
            return QueryRoute(
                intent="clarification",
                rewritten_query=query,
                confidence=0.92,
            )

        if cls.is_entity_attribute_query(normalized):
            return QueryRoute(
                intent="campus_knowledge",
                rewritten_query=cls._resolve_owned_entity(query, context),
                confidence=0.95,
            )

        if any(marker in normalized for marker in cls.PROCEDURE_MARKERS):
            return QueryRoute(
                intent="procedure",
                rewritten_query=query,
                confidence=0.95,
            )

        if cls.has_course_explanation_intent(query):
            return QueryRoute(
                intent="personal_fact",
                rewritten_query=query,
                personal_field="schedule",
                personal_action="explain_course",
                confidence=0.9,
            )

        if cls.has_schedule_time_constraint(query) and cls.has_schedule_lookup_intent(query):
            return QueryRoute(
                intent="personal_fact",
                rewritten_query=query,
                personal_field="schedule",
                personal_action="return_schedule",
                confidence=0.9,
            )

        personal_field = cls.detect_personal_field(normalized)
        if personal_field:
            return QueryRoute(
                intent="personal_fact",
                rewritten_query=query,
                personal_field=personal_field,
                personal_action=(
                    "return_schedule" if personal_field == "schedule" else None
                ),
                confidence=0.9,
            )

        if any(word in normalized for word in ("通知", "公告", "校内消息")):
            return QueryRoute(
                intent="campus_notice",
                rewritten_query=cls._resolve_owned_entity(query, context),
                confidence=0.85,
            )

        return QueryRoute(
            intent="campus_knowledge",
            rewritten_query=cls._resolve_owned_entity(query, context),
            confidence=0.6,
        )

    @classmethod
    def classify_by_rules(cls, query: str) -> str:
        """Compatibility wrapper for older callers."""
        route = cls.analyze_fallback(query)
        return {
            "personal_fact": "personal_value",
            "campus_knowledge": "general",
            "campus_notice": "general",
            "clarification": "general",
        }.get(route.intent, route.intent)


class StructuredDataRetriever:
    """Read role-scoped profile and notice records from MySQL."""

    def __init__(self):
        database_url = (
            f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
            f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}"
            "?charset=utf8mb4"
        )
        self.engine = create_engine(database_url, pool_pre_ping=True)

    @staticmethod
    def _add(lines: list[str], label: str, value: Any) -> None:
        if value is not None and value != "":
            lines.append(f"{label}：{value}")

    def get_personal_documents(self, user: UserContext) -> List[Document]:
        if not user.user_id.isdigit():
            return []

        user_id = int(user.user_id)
        documents: List[Document] = []
        with Session(self.engine) as session:
            db_user = session.scalar(select(User).where(User.id == user_id))
            if db_user is None or db_user.role not in {"student", "teacher"}:
                return []

            profile_lines = [
                f"姓名：{db_user.full_name}",
                f"身份：{'学生' if db_user.role == 'student' else '教师'}",
            ]
            self._add(profile_lines, "院系代码", db_user.dept_id)

            if db_user.role == "student":
                profile = session.scalar(
                    select(StudentProfile).where(StudentProfile.user_id == user_id)
                )
                if profile:
                    self._add(profile_lines, "学号", profile.student_no)
                    self._add(profile_lines, "所属学院或系", profile.college_name)
                    self._add(profile_lines, "专业", profile.major)
                    self._add(profile_lines, "年级", profile.grade_year)
                    self._add(profile_lines, "班级", profile.class_name)
                    self._add(profile_lines, "当前绩点", profile.gpa)
                    self._add(profile_lines, "专业排名", profile.major_rank)
                    self._add(profile_lines, "已获学分", profile.earned_credits)
                    self._add(profile_lines, "校区", profile.campus)
            else:
                profile = session.scalar(
                    select(TeacherProfile).where(TeacherProfile.user_id == user_id)
                )
                if profile:
                    self._add(profile_lines, "工号", profile.employee_no)
                    self._add(profile_lines, "所属学院或系", profile.college_name)
                    self._add(profile_lines, "职称", profile.title)
                    self._add(profile_lines, "办公室", profile.office)
                    self._add(profile_lines, "研究方向", profile.research_direction)
                    self._add(profile_lines, "校区", profile.campus)

            documents.append(
                Document(
                    id=f"mysql-profile-{user_id}",
                    content="\n".join(profile_lines),
                    score=1.0,
                    source="MySQL个人档案",
                    metadata={"user_id": str(user_id), "record_type": "profile"},
                )
            )

            schedules = list(
                session.scalars(
                    select(CourseSchedule)
                    .where(CourseSchedule.user_id == user_id)
                    .order_by(
                        CourseSchedule.semester.desc(),
                        CourseSchedule.weekday.asc(),
                        CourseSchedule.start_time.asc(),
                        CourseSchedule.start_section.asc(),
                    )
                ).all()
            )
            if schedules:
                schedule_lines = ["课程与课表："]
                for item in schedules:
                    details = [item.course_name]
                    if item.semester:
                        details.append(item.semester)
                    if item.instructor:
                        details.append(f"教师 {item.instructor}")
                    if item.weekday:
                        details.append(f"星期{item.weekday}")
                    if item.start_section:
                        end_section = item.end_section or item.start_section
                        details.append(f"第{item.start_section}-{end_section}节")
                    if item.start_time and item.end_time:
                        details.append(
                            f"{item.start_time.strftime('%H:%M')}-"
                            f"{item.end_time.strftime('%H:%M')}"
                        )
                    if item.location:
                        details.append(item.location)
                    if item.week_range:
                        details.append(item.week_range)
                    if item.status:
                        details.append(f"状态 {item.status}")
                    schedule_lines.append("- " + "；".join(details))

                documents.append(
                    Document(
                        id=f"mysql-schedule-{user_id}",
                        content="\n".join(schedule_lines),
                        score=1.0,
                        source="MySQL课程安排",
                        metadata={"user_id": str(user_id), "record_type": "schedule"},
                    )
                )

            if db_user.role == "student":
                grades = list(
                    session.scalars(
                        select(StudentGrade)
                        .where(StudentGrade.user_id == user_id)
                        .order_by(StudentGrade.semester.desc())
                    ).all()
                )
                if grades:
                    grade_lines = ["课程成绩："]
                    for grade in grades:
                        details = [grade.course_name]
                        if grade.semester:
                            details.append(grade.semester)
                        if grade.score is not None:
                            details.append(f"成绩 {grade.score}")
                        if grade.grade_point is not None:
                            details.append(f"绩点 {grade.grade_point}")
                        if grade.credits is not None:
                            details.append(f"学分 {grade.credits}")
                        grade_lines.append("- " + "；".join(details))

                    documents.append(
                        Document(
                            id=f"mysql-grades-{user_id}",
                            content="\n".join(grade_lines),
                            score=1.0,
                            source="MySQL学生成绩",
                            metadata={"user_id": str(user_id), "record_type": "grades"},
                        )
                    )

                exams = list(
                    session.scalars(
                        select(StudentExam)
                        .where(StudentExam.user_id == user_id)
                        .order_by(StudentExam.id.asc())
                    ).all()
                )
                if exams:
                    exam_lines = ["期末考试信息："]
                    for exam in exams:
                        details = [
                            exam.subject,
                            (
                                f"{exam.exam_time.month}月{exam.exam_time.day}日 "
                                f"{exam.exam_time.strftime('%H:%M')}"
                            ),
                        ]
                        if exam.location:
                            details.append(exam.location)
                        if exam.exam_method:
                            details.append(exam.exam_method)
                        exam_lines.append("- " + "；".join(details))

                    documents.append(
                        Document(
                            id=f"mysql-exams-{user_id}",
                            content="\n".join(exam_lines),
                            score=1.0,
                            source="MySQL学生考试信息",
                            metadata={"user_id": str(user_id), "record_type": "exams"},
                        )
                    )

        return documents

    def get_routing_context(self, user: UserContext) -> Dict[str, Any]:
        """Return only the identity fields needed to resolve phrases like 我的学院."""
        if not user.user_id.isdigit():
            return {"role": user.user_role, "dept_id": user.dept_id}

        user_id = int(user.user_id)
        context: Dict[str, Any] = {
            "role": user.user_role,
            "dept_id": user.dept_id,
        }
        with Session(self.engine) as session:
            db_user = session.scalar(select(User).where(User.id == user_id))
            if db_user is None:
                return context
            context["dept_id"] = db_user.dept_id
            if db_user.role == "student":
                profile = session.scalar(
                    select(StudentProfile).where(StudentProfile.user_id == user_id)
                )
                if profile:
                    context["college_name"] = profile.college_name
                    context["major"] = profile.major
            elif db_user.role == "teacher":
                profile = session.scalar(
                    select(TeacherProfile).where(TeacherProfile.user_id == user_id)
                )
                if profile:
                    context["college_name"] = profile.college_name
        return context

    @staticmethod
    def _parse_requested_weekday(query: str) -> int | None:
        normalized = query.lower()
        weekday_patterns = (
            (1, ("周一", "星期一", "礼拜一", "周1", "星期1")),
            (2, ("周二", "星期二", "礼拜二", "周2", "星期2")),
            (3, ("周三", "星期三", "礼拜三", "周3", "星期3")),
            (4, ("周四", "星期四", "礼拜四", "周4", "星期4")),
            (5, ("周五", "星期五", "礼拜五", "周5", "星期5")),
            (6, ("周六", "星期六", "礼拜六", "周6", "星期6")),
            (
                7,
                (
                    "周日",
                    "周天",
                    "星期日",
                    "星期天",
                    "礼拜日",
                    "礼拜天",
                    "周7",
                    "星期7",
                ),
            ),
        )
        for weekday, markers in weekday_patterns:
            if any(marker in normalized for marker in markers):
                return weekday
        if "今天" in normalized:
            return datetime.date.today().isoweekday()
        if "明天" in normalized:
            return (datetime.date.today().isoweekday() % 7) + 1
        if "后天" in normalized:
            return ((datetime.date.today().isoweekday() + 1) % 7) + 1
        return None

    @staticmethod
    def _parse_time_period(query: str) -> str | None:
        normalized = query.lower()
        if any(marker in normalized for marker in ("上午", "早上", "早晨")):
            return "morning"
        if any(marker in normalized for marker in ("下午", "午后")):
            return "afternoon"
        if any(marker in normalized for marker in ("晚上", "晚间", "夜间")):
            return "evening"
        return None

    @staticmethod
    def _parse_clock_time(query: str, period: str | None = None) -> datetime.time | None:
        normalized = query.lower()
        match = re.search(r"(\d{1,2})(?:[:：](\d{1,2}))?\s*点?", normalized)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
        else:
            chinese_hours = {
                "零": 0,
                "一": 1,
                "二": 2,
                "两": 2,
                "三": 3,
                "四": 4,
                "五": 5,
                "六": 6,
                "七": 7,
                "八": 8,
                "九": 9,
                "十": 10,
                "十一": 11,
                "十二": 12,
            }
            match = re.search(r"(十二|十一|十|[一二两三四五六七八九])点", normalized)
            if not match:
                return None
            hour = chinese_hours[match.group(1)]
            minute = 0
        if period in {"afternoon", "evening"} and 1 <= hour < 12:
            hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return datetime.time(hour, minute)
        return None

    @classmethod
    def _complete_schedule_filters(
        cls,
        query: str,
        personal_filters: Dict[str, Any] | None,
        allow_rule_fallback: bool,
    ) -> Dict[str, Any]:
        filters = dict(personal_filters or {})
        if allow_rule_fallback:
            filters.setdefault("weekday", cls._parse_requested_weekday(query))
            filters.setdefault("period", cls._parse_time_period(query))
            parsed_time = cls._parse_clock_time(query, filters.get("period"))
            if parsed_time:
                filters.setdefault("time", parsed_time.strftime("%H:%M"))
        return {key: value for key, value in filters.items() if value not in {None, ""}}

    @staticmethod
    def _is_temporal_target(text: str) -> bool:
        return bool(
            re.search(
                r"(周[一二三四五六日天1-7]|星期[一二三四五六日天1-7]|礼拜[一二三四五六日天]|"
                r"今天|明天|后天|上午|下午|晚上|早上|中午|\d{1,2}点)",
                text,
            )
        )

    @staticmethod
    def _schedule_matches_filters(item: CourseSchedule, filters: Dict[str, Any]) -> bool:
        period = filters.get("period")
        if period and item.start_time:
            if period == "morning" and item.start_time >= datetime.time(12, 0):
                return False
            if period == "afternoon" and not (
                datetime.time(12, 0) <= item.start_time < datetime.time(18, 0)
            ):
                return False
            if period == "evening" and item.start_time < datetime.time(18, 0):
                return False
        raw_time = filters.get("time")
        if raw_time and item.start_time and item.end_time:
            try:
                target_time = datetime.time.fromisoformat(str(raw_time))
            except ValueError:
                return False
            if not (item.start_time <= target_time <= item.end_time):
                return False
        return True

    def resolve_schedule_reference(
        self,
        query: str,
        user: UserContext,
        personal_filters: Dict[str, Any] | None = None,
        allow_rule_fallback: bool = True,
    ) -> List[CourseSchedule]:
        if not user.user_id.isdigit() or user.user_role not in {"student", "teacher"}:
            return []
        user_id = int(user.user_id)
        filters = self._complete_schedule_filters(
            query,
            personal_filters,
            allow_rule_fallback,
        )
        stmt = select(CourseSchedule).where(CourseSchedule.user_id == user_id)
        raw_weekday = filters.get("weekday")
        if isinstance(raw_weekday, int) and 1 <= raw_weekday <= 7:
            stmt = stmt.where(CourseSchedule.weekday == raw_weekday)
        with Session(self.engine) as session:
            schedules = list(
                session.scalars(
                    stmt.order_by(
                        CourseSchedule.weekday.asc(),
                        CourseSchedule.start_time.asc(),
                        CourseSchedule.start_section.asc(),
                    )
                ).all()
            )
        return [
            item
            for item in schedules
            if self._schedule_matches_filters(item, filters)
        ]

    def answer_personal_query(
        self,
        query: str,
        user: UserContext,
        personal_field: str | None = None,
        personal_action: str | None = None,
        personal_filters: Dict[str, Any] | None = None,
        allow_rule_fallback: bool = True,
    ) -> str | None:
        """Return exact answers for common structured personal-data questions."""
        if not user.user_id.isdigit() or user.user_role not in {"student", "teacher"}:
            return None

        normalized = query.lower()
        if QueryIntentRouter.is_entity_attribute_query(normalized):
            return None
        requested_field = personal_field
        if not requested_field and allow_rule_fallback:
            requested_field = QueryIntentRouter.detect_personal_field(normalized)
        if not requested_field:
            return None

        is_self_query = any(
            keyword in normalized
            for keyword in ("我", "本人", "个人", "自己的", "我的", "所属")
        )
        personal_keywords = (
            "绩点",
            "gpa",
            "排名",
            "姓名",
            "名字",
            "身份",
            "角色",
            "学号",
            "工号",
            "专业",
            "年级",
            "班级",
            "学分",
            "成绩",
            "考试",
            "考查",
            "期末",
            "校区",
            "学院",
            "院系",
            "职称",
            "办公室",
            "研究方向",
            "研究领域",
            "课表",
            "课程",
            "上课",
            "选课",
        )
        user_id = int(user.user_id)
        with Session(self.engine) as session:
            db_user = session.scalar(select(User).where(User.id == user_id))
            if db_user is None:
                return None

            explicit_target = re.search(
                r"([^，。？！?\s]{1,30})的"
                r"(?:绩点|gpa|排名|姓名|名字|身份|角色|学号|工号|专业|年级|"
                r"班级|学分|成绩|校区|学院|院系|职称|办公室|研究方向|研究领域|"
                r"课表|课程|考试|考查|期末)",
                normalized,
            )
            if explicit_target:
                target = explicit_target.group(1)
                # Do not treat every "X 的课程/成绩" as another user's data.
                # X may be a time range ("周三上午的课程") or a course name
                # ("软件测试的成绩"). Known other-user names are handled below.

            other_users = session.scalars(
                select(User).where(User.id != user_id)
            ).all()
            references_other_user = any(
                identifier
                and len(identifier.strip()) > 1
                and identifier.strip().lower() in normalized
                for other_user in other_users
                for identifier in (other_user.username, other_user.full_name)
            )
            if references_other_user and any(
                keyword in normalized for keyword in personal_keywords
            ):
                return "个人信息只能查询当前登录账户，不能查询其他用户的档案。"

            if requested_field == "name":
                return f"当前登录账户姓名为{db_user.full_name or db_user.username}。"

            if requested_field == "role":
                role_name = "学生" if db_user.role == "student" else "教师"
                return f"当前登录账户身份为{role_name}。"

            if user.user_role == "student":
                profile = session.scalar(
                    select(StudentProfile).where(StudentProfile.user_id == user_id)
                )
                if requested_field == "gpa":
                    if profile and profile.gpa is not None:
                        return f"你当前的绩点是 {profile.gpa}。"
                    return "当前个人档案中尚未录入绩点。"

                if requested_field == "major_rank":
                    if profile and profile.major_rank is not None:
                        return f"你当前的专业排名是第 {profile.major_rank} 名。"
                    return "当前个人档案中尚未录入专业排名。"

                if requested_field == "college":
                    college = profile.college_name if profile else None
                    if college:
                        return f"你所属的学院或系是{college}（院系代码：{db_user.dept_id}）。"
                    return f"当前仅记录了院系代码：{db_user.dept_id or '未录入'}。"

                if requested_field == "student_no":
                    if profile and profile.student_no:
                        return f"你的学号是{profile.student_no}。"
                    return "当前个人档案中尚未录入学号。"

                if requested_field == "major":
                    if profile and profile.major:
                        return f"你的专业是{profile.major}。"
                    return "当前个人档案中尚未录入专业。"

                if requested_field == "grade_year":
                    if profile and profile.grade_year is not None:
                        return f"你的年级是{profile.grade_year}级。"
                    return "当前个人档案中尚未录入年级。"

                if requested_field == "class_name":
                    if profile and profile.class_name:
                        return f"你的班级是{profile.class_name}。"
                    return "当前个人档案中尚未录入班级。"

                if requested_field == "earned_credits":
                    if profile and profile.earned_credits is not None:
                        return f"你当前已获学分为{profile.earned_credits}。"
                    return "当前个人档案中尚未录入已获学分。"

                if requested_field == "campus":
                    if profile and profile.campus:
                        return f"你的个人档案校区是{profile.campus}。"
                    return "当前个人档案中尚未录入校区。"

                if requested_field == "grades":
                    grades = list(
                        session.scalars(
                            select(StudentGrade)
                            .where(StudentGrade.user_id == user_id)
                            .order_by(StudentGrade.semester.desc())
                        ).all()
                    )
                    if not grades:
                        return "当前个人档案中尚未录入课程成绩。"
                    lines = ["你的课程成绩如下："]
                    for grade in grades:
                        details = [grade.course_name]
                        if grade.score is not None:
                            details.append(f"成绩 {grade.score}")
                        if grade.grade_point is not None:
                            details.append(f"绩点 {grade.grade_point}")
                        if grade.credits is not None:
                            details.append(f"学分 {grade.credits}")
                        lines.append("- " + "，".join(details))
                    return "\n".join(lines)

                if requested_field == "exams":
                    exams = list(
                        session.scalars(
                            select(StudentExam)
                            .where(StudentExam.user_id == user_id)
                            .order_by(StudentExam.id.asc())
                        ).all()
                    )
                    if not exams:
                        return "暂无该生考试信息"
                    lines = ["你的期末考试信息如下："]
                    for exam in exams:
                        exam_time = exam.exam_time
                        time_text = (
                            f"{exam_time.month}月{exam_time.day}日 "
                            f"{exam_time.strftime('%H:%M')}"
                            if hasattr(exam_time, "strftime")
                            else str(exam_time)
                        )
                        details = [
                            exam.subject,
                            time_text,
                        ]
                        if exam.location:
                            details.append(exam.location)
                        if exam.exam_method:
                            details.append(exam.exam_method)
                        lines.append("- " + "，".join(details))
                    return "\n".join(lines)

            if user.user_role == "teacher":
                profile = session.scalar(
                    select(TeacherProfile).where(TeacherProfile.user_id == user_id)
                )
                if requested_field == "college":
                    college = profile.college_name if profile else None
                    if college:
                        return f"你所属的学院或系是{college}（院系代码：{db_user.dept_id}）。"
                    return f"当前仅记录了院系代码：{db_user.dept_id or '未录入'}。"
                if requested_field == "title":
                    if profile and profile.title:
                        return f"你当前的职称是{profile.title}。"
                    return "当前教师档案中尚未录入职称。"
                if requested_field == "employee_no":
                    if profile and profile.employee_no:
                        return f"你的工号是{profile.employee_no}。"
                    return "当前教师档案中尚未录入工号。"
                if requested_field == "office":
                    if profile and profile.office:
                        return f"你的办公室是{profile.office}。"
                    return "当前教师档案中尚未录入办公室信息。"
                if requested_field == "research_direction":
                    if profile and profile.research_direction:
                        return f"你的研究方向是{profile.research_direction}。"
                    return "当前教师档案中尚未录入研究方向。"
                if requested_field == "campus":
                    if profile and profile.campus:
                        return f"你的个人档案校区是{profile.campus}。"
                    return "当前教师档案中尚未录入校区。"

            if requested_field == "schedule":
                action = personal_action
                if action is None and allow_rule_fallback:
                    if QueryIntentRouter.has_course_explanation_intent(query):
                        action = "explain_course"
                    elif QueryIntentRouter.has_schedule_lookup_intent(query):
                        action = "return_schedule"
                if action == "explain_course":
                    return None
                if action != "return_schedule":
                    return "请补充一下你想查询这段时间的课程安排，还是想了解某节课的作用。"
                if (
                    QueryIntentRouter.has_schedule_time_constraint(query)
                    and not QueryIntentRouter.has_schedule_lookup_intent(query)
                ):
                    return "请补充一下你想查询这段时间的课程安排，还是想了解某节课的作用。"

                filters = self._complete_schedule_filters(
                    query,
                    personal_filters,
                    allow_rule_fallback,
                )
                requested_weekday = filters.get("weekday")
                has_day_constraint = self._parse_requested_weekday(query) is not None
                if (
                    requested_weekday is None
                    and has_day_constraint
                    and not allow_rule_fallback
                ):
                    return "我识别到你在询问某一天的课表，但路由结果缺少具体星期条件，请重新提问或稍后再试。"
                schedules = self.resolve_schedule_reference(
                    query,
                    user,
                    filters,
                    allow_rule_fallback=False,
                )
                if not schedules:
                    return "当前个人档案中尚未录入课程或课表。"

                weekday_names = {
                    1: "周一",
                    2: "周二",
                    3: "周三",
                    4: "周四",
                    5: "周五",
                    6: "周六",
                    7: "周日",
                }
                if requested_weekday is not None and not schedules:
                    weekday_name = weekday_names.get(requested_weekday, "该日")
                    return f"当前个人档案中没有录入你在{weekday_name}的课程。"

                if requested_weekday is not None:
                    weekday_name = weekday_names.get(requested_weekday, "该日")
                    lines = [f"你在{weekday_name}的课程安排如下："]
                else:
                    lines = ["你的课程安排如下："]
                for item in schedules:
                    parts = [
                        weekday_names.get(item.weekday, "日期待补充"),
                        item.course_name,
                    ]
                    if item.start_time and item.end_time:
                        parts.append(
                            f"{item.start_time.strftime('%H:%M')}-"
                            f"{item.end_time.strftime('%H:%M')}"
                        )
                    elif item.start_section:
                        parts.append(
                            f"第{item.start_section}-"
                            f"{item.end_section or item.start_section}节"
                        )
                    lines.append("- " + "，".join(parts))
                return "\n".join(lines)

        return None

    def get_notice_documents(self, user: UserContext) -> List[Document]:
        if user.user_role not in {"student", "teacher"}:
            return []

        now = datetime.datetime.now()
        with Session(self.engine) as session:
            notices = list(
                session.scalars(
                    select(CampusNotice)
                    .where(
                        CampusNotice.is_active.is_(True),
                        or_(
                            CampusNotice.dept_id.is_(None),
                            CampusNotice.dept_id == user.dept_id,
                        ),
                        CampusNotice.audience.in_(["all", user.user_role]),
                        or_(
                            CampusNotice.expires_at.is_(None),
                            CampusNotice.expires_at >= now,
                        ),
                    )
                    .order_by(CampusNotice.published_at.desc())
                ).all()
            )

        return [
            Document(
                id=f"mysql-notice-{notice.id}",
                content=f"{notice.title}\n{notice.content}",
                score=1.0,
                source=notice.source or "校园通知",
                metadata={
                    "dept_id": notice.dept_id or "",
                    "audience": notice.audience,
                    "record_type": "notice",
                },
            )
            for notice in notices
        ]


class LLMGenerator:
    """
    LLM 交互管理类。
    负责查询重写（Query Rewriting）和最终答案生成（RAG Generation）。
    """
    def __init__(self):
        # 初始化重写模型 
        self.rewrite_llm = ChatTongyi(
            model=settings.REWRITE_MODEL_NAME, 
            api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.2,# 重写时允许一定创造性
            seed=settings.GLOBAL_SEED # 固定种子
        )
        # 初始化生成模型 (开启流式输出)
        self.gen_llm = ChatTongyi(
            model=settings.GENERATE_MODEL_NAME, 
            api_key=settings.DASHSCOPE_API_KEY,
            streaming=True,
            temperature=0,#回答稳定性优先
            seed=settings.GLOBAL_SEED
        )

    def analyze_query(
        self,
        history: List[Any],
        query: str,
        user: UserContext,
        routing_context: Dict[str, Any],
    ) -> QueryRoute:
        """Analyze every query with the LLM before selecting a data route."""
        history_text = "\n".join(
            f"{'用户' if isinstance(message, HumanMessage) else '助手'}："
            f"{message.content}"
            for message in history[-6:]
        ) or "无"
        context_text = json.dumps(routing_context, ensure_ascii=False)
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                """你是校园问答系统的检索路由器。你必须先理解用户真正询问的
谓语和目标，再决定数据来源。只输出一个 JSON 对象，不要回答问题。

intent 只能是：
- personal_fact：直接询问当前用户档案中的字段值；
- procedure：询问查询方法、入口、办理步骤或规则；
- campus_knowledge：询问学校、学院、专业等校园实体的属性、历史或介绍；
- campus_notice：询问新闻、通知或公告；
- general：与校园数据库无关的通用问题；
- clarification：问题过短或无法确定目标，需要追问。

JSON 字段：
{{"intent":"...", "rewritten_query":"...", "personal_field":null,
 "personal_action":null, "personal_filters":{{}}, "confidence":0.0}}

personal_field 仅在 personal_fact 时填写，可选值：
gpa, major_rank, name, role, student_no, employee_no, major,
grade_year, class_name, earned_credits, campus, college, title,
office, research_direction, schedule, grades, exams, profile。
personal_filters 用于保存个人数据查询中的条件。查询课表 schedule 时，如果用户限定了周几，
必须输出 weekday：周一=1，周二=2，周三=3，周四=4，周五=5，周六=6，周日/周天=7；
如果限定上午/下午/晚上，输出 period=morning/afternoon/evening；如果限定具体时间，
输出 time=HH:MM。
personal_action 仅在 personal_field=schedule 时填写：
- return_schedule：用户询问某天/某时间有哪些课、课程安排、课表；
- explain_course：用户先用个人课表中的时间定位某节课，再问“作用是什么、用来干什么、是什么、介绍一下”等课程含义/用途。此时后端只用个人课表定位课程名，课程解释由大模型回答。
如果用户只说“周三上午”这类时间片段，没有提出要查课表或解释课程，intent 必须为 clarification。
例如“我在周三有什么课程”必须输出：
{{"intent":"personal_fact","rewritten_query":"我在周三有什么课程","personal_field":"schedule","personal_action":"return_schedule","personal_filters":{{"weekday":3}},"confidence":0.95}}
例如“我周二上午十点的那节课的作用是什么”必须输出：
{{"intent":"personal_fact","rewritten_query":"我周二上午十点的那节课的作用是什么","personal_field":"schedule","personal_action":"explain_course","personal_filters":{{"weekday":2,"period":"morning","time":"10:00"}},"confidence":0.95}}

关键判定：
1. “我的学院是什么”是在读个人字段 college。
2. “我的学院成立于哪一年”不是查询个人字段，而是以“我的学院”指代一个校园
实体，必须使用身份上下文把它改写成具体学院名称并路由 campus_knowledge。
3. “我的专业就业情况如何”询问专业这一校园实体，不是返回专业名称。
4. “我的绩点是多少”才是 personal_fact；“如何查询绩点”是 procedure。
5. “我的考试信息/期末考试安排/考试时间地点”是 personal_fact，personal_field 必须为 exams。
6. 不得因为句子含有“学院、专业、绩点”等词就直接选择 personal_fact。
7. 只有 personal_fact 路由允许读取私有档案。""",
            ),
            (
                "human",
                """当前身份：{role}
用于指代消解的身份上下文：{routing_context}
最近对话：
{history}

当前问题：{question}

JSON：""",
            ),
        ])
        chain = prompt | self.rewrite_llm | StrOutputParser()
        try:
            raw_result = chain.invoke(
                {
                    "role": user.user_role,
                    "routing_context": context_text,
                    "history": history_text,
                    "question": query,
                }
            ).strip()
            json_match = re.search(r"\{.*\}", raw_result, flags=re.DOTALL)
            if not json_match:
                raise ValueError(f"No JSON object in model output: {raw_result}")
            payload = json.loads(json_match.group(0))
            intent = str(payload.get("intent", "")).strip()
            allowed_intents = {
                "personal_fact",
                "procedure",
                "campus_knowledge",
                "campus_notice",
                "general",
                "clarification",
            }
            if intent not in allowed_intents:
                raise ValueError(f"Unsupported intent: {intent}")
            route = QueryRoute(
                intent=intent,
                rewritten_query=(
                    str(payload.get("rewritten_query") or query).strip()
                ),
                personal_field=payload.get("personal_field"),
                personal_action=payload.get("personal_action"),
                personal_filters=(
                    payload.get("personal_filters")
                    if isinstance(payload.get("personal_filters"), dict)
                    else {}
                ),
                confidence=float(payload.get("confidence") or 0.0),
                used_llm=True,
            )
            if (
                route.intent == "personal_fact"
                and QueryIntentRouter.is_entity_attribute_query(query)
            ):
                return QueryIntentRouter.analyze_fallback(
                    query,
                    routing_context,
                )
            if QueryIntentRouter.is_under_specified_query(query):
                return QueryRoute(
                    intent="clarification",
                    rewritten_query=query,
                    confidence=max(route.confidence, 0.95),
                    used_llm=route.used_llm,
                )
            return route
        except Exception as exc:
            print(f"LLM route analysis failed, using fallback: {exc}")
            return QueryIntentRouter.analyze_fallback(query, routing_context)

    def classify_query_intent(self, query: str) -> str:
        """Compatibility wrapper for older callers."""
        route = self.analyze_query(
            [],
            query,
            UserContext(user_id="guest", user_role="guest"),
            {},
        )
        return {
            "personal_fact": "personal_value",
            "campus_knowledge": "general",
            "campus_notice": "general",
            "clarification": "general",
        }.get(route.intent, route.intent)

    def rewrite_query(self, history: List[Any], current: str) -> RewrittenQuery:
        """
        结合历史对话上下文，重写用户的当前问题。
        使其成为包含完整语义的独立查询语句。
        """
        if not history:
            return RewrittenQuery(original_text=current, rewritten_text=current)

        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一个专门负责“指代消解”和“省略补全”的工具。
            
            【请结合对话历史，仅针对用户的最新问题（被 <<< >>> 包裹的内容）做以下两件事：
            1. **指代替换**：将“它”、“这个”、“那里”等代词替换为历史对话中的具体实体。
            2. **成分补全**：如果问题缺失主语或宾语，请根据上下文补齐。

            **严格约束（Strict Constraints）**：
            - 如果用户的最新问题已经是主谓宾完整、语义清晰的独立句子，**请直接输出原句，严禁修改任何字词**。
            - 不要尝试优化问题的表达方式、语序或修辞。
            - 禁止使用历史对话中未出现过的词语或表达，不能引入新信息。
            - **严禁回答问题**：你的任务仅是重写，不要生成任何回答内容.
            """),

            MessagesPlaceholder(variable_name="history"),

            ("human", """请忽略你需要回答这个问题的冲动，仅根据上述历史，在不改变原意的前提下重写以下被包裹的最新提问:

            <<< {question} >>>
            
            重写结果：""")
        ])
        
        chain = prompt | self.rewrite_llm | StrOutputParser()
        try:
            rewritten_text = chain.invoke({"history": history, "question": current})
        except Exception as e:
            print(f"查询重写失败: {e}")
            rewritten_text = current
            
        return RewrittenQuery(original_text=current, rewritten_text=rewritten_text)

    def generate_answer(self, query: str, docs: List[Document], prompt_template: str) -> Generator[str, None, None]:
        """
        根据检索到的文档和用户问题生成回答。
        
        Args:
            query: 重写后的查询语句
            docs: 检索到的相关文档列表
            prompt_template: 用于生成的 Prompt 模板
            
        Returns:
            流式生成的字符串生成器
        """
        context_str = "\n\n".join([f"[来源:{d.source}] {d.content}" for d in docs])
        
        prompt = ChatPromptTemplate.from_template(prompt_template)
        chain = prompt | self.gen_llm | StrOutputParser()
        
        return chain.stream({"context": context_str, "question": query})
