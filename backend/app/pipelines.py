from abc import ABC, abstractmethod
from typing import List, Generator
import logging
import re
import jieba
from app.dto import RequestPayload, UserContext, Document
from app.components import (
    HistoryManager,
    LLMGenerator,
    LocalFAQRetriever,
    parse_source_reference,
    QueryIntentRouter,
    StructuredDataRetriever,
    VectorRetriever,
)
from app.config import settings

logger = logging.getLogger(__name__)
SERVICE_UNAVAILABLE_MESSAGE = "问答服务暂时不可用，请稍后重试。"

class BasePipeline(ABC):
    def __init__(self):
        self.history_mgr = HistoryManager()
        self.retriever = VectorRetriever()
        self.local_faq_retriever = LocalFAQRetriever()
        self.structured_retriever = StructuredDataRetriever()
        self.llm_service = LLMGenerator()
        self._last_response_metadata = None

    def _reset_metadata(self) -> None:
        self._last_response_metadata = None

    def consume_response_metadata(self) -> dict:
        metadata = self._last_response_metadata or self._build_response_metadata(
            docs=[],
            answer_origin="llm",
        )
        self._last_response_metadata = None
        return metadata

    @staticmethod
    def _source_type(doc: Document) -> str:
        if doc.metadata.get("is_faq"):
            return "faq"
        if doc.metadata.get("record_type") in {"profile", "schedule", "grades", "exams"}:
            return "personal"
        if doc.metadata.get("record_type") == "notice":
            return "notice"
        return "rag"

    def _build_response_metadata(
        self,
        docs: List[Document],
        answer_origin: str,
    ) -> dict:
        unique_sources = []
        seen = set()
        for doc in docs:
            source_info = parse_source_reference(doc.source)
            key = (source_info["source_title"], source_info["source_url"], doc.content[:80])
            if key in seen:
                continue
            seen.add(key)
            unique_sources.append(
                {
                    "title": source_info["source_title"],
                    "url": source_info["source_url"] or None,
                    "snippet": " ".join(doc.content.split())[:320],
                    "score": round(float(doc.score or 0.0), 4),
                    "source_type": self._source_type(doc),
                }
            )
            if len(unique_sources) >= 5:
                break

        if not docs:
            confidence = "low"
            notice = "当前资料库中未检索到足够相关资料，以下回答由大模型基于通用知识生成，请以学校官方信息为准。"
        else:
            max_score = max(float(doc.score or 0.0) for doc in docs)
            if max_score >= 0.78 or len(docs) >= 3:
                confidence = "high"
            elif max_score >= 0.45:
                confidence = "medium"
            else:
                confidence = "low"
            notice = (
                "已基于当前资料库检索结果生成回答，可展开查看来源片段。"
                if confidence != "low"
                else "检索命中较弱，资料可能不足，回答仅供参考。"
            )

        return {
            "answer_origin": answer_origin,
            "confidence": confidence,
            "notice": notice,
            "sources": unique_sources,
        }

    def _set_direct_metadata(self, doc: Document, answer_origin: str = "faq") -> None:
        self._last_response_metadata = self._build_response_metadata(
            docs=[doc],
            answer_origin=answer_origin,
        )

    @staticmethod
    def _extract_llm_only_query(query: str) -> str | None:
        normalized = re.sub(r"\s+", "", query.lower())
        llm_only_markers = (
            "全部基于llm",
            "全基于llm",
            "只用llm",
            "仅用llm",
            "用llm回答",
            "使用llm回答",
            "全部基于大模型",
            "全基于大模型",
            "只用大模型",
            "仅用大模型",
            "用大模型回答",
            "使用大模型回答",
            "全部基于语言模型",
            "仅用语言模型",
            "只用语言模型",
        )
        no_database_markers = (
            "不使用资料库",
            "不用资料库",
            "不查资料库",
            "不走资料库",
            "不要使用资料库",
            "不要查资料库",
            "不要检索资料库",
            "跳过资料库",
            "不使用知识库",
            "不用知识库",
            "不查知识库",
            "不走知识库",
            "不要使用知识库",
            "不要查知识库",
            "不要检索知识库",
            "跳过知识库",
            "不使用rag",
            "不用rag",
            "不走rag",
            "跳过rag",
        )
        if not any(marker in normalized for marker in llm_only_markers + no_database_markers):
            return None

        stripped = query
        leading_directives = (
            r"^\s*(?:本次|这次|此次|当前|这个问题)?(?:回答|回复)?(?:请|麻烦)?"
            r"(?:全部|完全)?(?:基于|使用|用|只用|仅用)"
            r"(?:llm|LLM|大模型|语言模型)(?:回答|回复)?[，,。:：；;\s]*",
            r"^\s*(?:本次|这次|此次|当前|这个问题)?(?:回答|回复)?(?:请|麻烦)?"
            r"(?:不要|别|无需|不用|不使用|不查|不要查|不要使用|不要检索|跳过|不走)"
            r"(?:当前)?(?:的)?(?:资料库|知识库|数据库|rag|RAG)"
            r"(?:内容|检索|资料)?(?:回答|回复)?[，,。:：；;\s]*",
        )
        for pattern in leading_directives:
            stripped = re.sub(pattern, "", stripped, count=1, flags=re.IGNORECASE)
        stripped = stripped.strip(" \t\r\n，,。:：；;")
        return stripped or query

    @staticmethod
    def _llm_only_prompt() -> str:
        return """你是一个通用大模型助手。本次用户明确要求不使用项目资料库、知识库或 RAG 检索结果。
请直接基于通用知识回答用户问题；如果问题涉及时效性、校内政策、个人信息、教务数据等可能需要权威资料确认的内容，要主动说明未使用资料库，结论仅供参考。

【用户问题】
{question}

【回答】"""

    def _answer_with_llm_only(
        self,
        query: str,
        session_id: str,
    ) -> Generator[str, None, None]:
        full_answer = ""
        try:
            for chunk in self.llm_service.generate_answer(
                query,
                [],
                self._llm_only_prompt(),
            ):
                full_answer += chunk
                yield chunk
        except Exception:
            logger.exception("LLM-only answer generation failed")
            full_answer = SERVICE_UNAVAILABLE_MESSAGE
            yield full_answer
        self.history_mgr.append_ai_message(session_id, full_answer)
        self._last_response_metadata = {
            "answer_origin": "llm",
            "confidence": "low",
            "notice": "已按用户要求跳过资料库检索，本次回答由大模型基于通用知识生成，请以权威来源为准。",
            "sources": [],
        }

    def execute(self, request: RequestPayload, user: UserContext) -> Generator[str, None, None]:
        self._reset_metadata()
        # 获取历史
        history = self.history_mgr.get_recent_turns(request.session_id)
        early_llm_only_query = self._extract_llm_only_query(request.query)
        if early_llm_only_query:
            self.history_mgr.append_user_message(request.session_id, request.query)
            yield from self._answer_with_llm_only(
                early_llm_only_query,
                request.session_id,
            )
            return
        
        # 查询重写
        rewritten_query = self.llm_service.rewrite_query(history, request.query)
        print(f"[Pipeline] User: {request.query} -> Rewritten: {rewritten_query.rewritten_text}")
        
        # 记录用户提问
        self.history_mgr.append_user_message(request.session_id, request.query)
        
        # 执行具体检索策略
        llm_only_query = self._extract_llm_only_query(request.query)
        if llm_only_query:
            yield from self._answer_with_llm_only(
                llm_only_query,
                request.session_id,
            )
            return

        docs = self._retrieve_strategy(rewritten_query.rewritten_text, user)

        # --- 调试日志打印开始 ---
        print(f"\n{'='*30} 检索调试信息 {'='*30}")
        print(f"原始问题: {request.query}")
        print(f"重写问题: {rewritten_query.rewritten_text}")
        print(f"命中数量: {len(docs)}")
        
        for i, doc in enumerate(docs):
            # 将换行符替换为空格，避免日志太乱
            clean_content = doc.content.replace('\n', ' ')
            # 打印分数、来源和前 150 个字符的内容
            print(f" [文档 {i+1}] 得分: {doc.score:.4f} | 来源: {doc.source}")
            print(f"    内容预览: {clean_content[:150]}...")
            print("-" * 50)
        print(f"{'='*76}\n")
        # --- 【调试日志打印结束 ---
        
        # 5. 生成回答 (流式)
        full_answer = ""
        prompt_tmpl = self._get_prompt_template()
        
        try:
            for chunk in self.llm_service.generate_answer(rewritten_query.rewritten_text, docs, prompt_tmpl):
                full_answer += chunk
                yield chunk
        except Exception as e:
            logger.exception("Answer generation failed")
            err = SERVICE_UNAVAILABLE_MESSAGE
            full_answer += err
            yield err
            
        # 记录 AI 回答
        self.history_mgr.append_ai_message(request.session_id, full_answer)
        self._last_response_metadata = self._build_response_metadata(
            docs=docs,
            answer_origin="rag" if docs else "llm",
        )

    def _keyword_rerank(self, query: str, docs: List[Document], final_k: int = 3) -> List[Document]:
        """
        采用 向量分数(60%) + 关键词覆盖率(40%) 进行加权融合。
        """
        if not docs:
            return []
            
        # 1. 提取关键词
        seg_list = jieba.lcut_for_search(query)
        keywords = set([k for k in seg_list if len(k) > 1])
        if not keywords:
            return docs[:final_k]

        # 2. 准备分数列表进行归一化
        scores = [d.score for d in docs]
        min_s, max_s = min(scores), max(scores)
        score_range = max_s - min_s if max_s != min_s else 1.0

        # 权重配置
        WEIGHT_VECTOR = 0.6
        WEIGHT_KEYWORD = 0.4

        for doc in docs:
            # --- 向量分数归一化 ---
            norm_vec_score = (doc.score - min_s) / score_range
            
            # --- 关键词覆盖率计算 ---
            content = doc.content
            hit_count = sum(1 for kw in keywords if kw in content)
            keyword_score = hit_count / len(keywords)
            
            # --- 加权融合 ---
            final_score = (WEIGHT_VECTOR * norm_vec_score) + (WEIGHT_KEYWORD * keyword_score)
            
            doc.score = final_score
            
        # 3. 重新排序
        docs.sort(key=lambda x: x.score, reverse=True)
        
        return docs[:final_k]

    @abstractmethod
    def _retrieve_strategy(self, query: str, user: UserContext) -> List[Document]:
        pass

    @abstractmethod
    def _get_prompt_template(self) -> str:
        pass

# --- 具体业务实现 ---

class AutoPipeline(BasePipeline):
    """Single entry point that applies identity-aware retrieval automatically."""

    FAQ_THRESHOLD = 0.8
    PERSONAL_VECTOR_THRESHOLD = 0.55
    CAMPUS_VECTOR_THRESHOLD = 0.45

    @staticmethod
    def _can_attempt_faq(route_intent: str, query: str) -> bool:
        if route_intent in {"clarification", "general"}:
            return False
        return LocalFAQRetriever.is_specific_faq_query(query)

    @staticmethod
    def _is_relevant(query: str, doc: Document, threshold: float) -> bool:
        if doc.score < threshold:
            return False
        keywords = {
            word
            for word in jieba.lcut_for_search(query)
            if len(word.strip()) > 1
        }
        has_keyword = any(word in doc.content for word in keywords)
        return has_keyword or doc.score >= 0.75

    @staticmethod
    def _filter_notices(query: str, docs: List[Document]) -> List[Document]:
        if any(word in query for word in ("通知", "公告", "校内消息", "学校消息")):
            return docs
        keywords = {
            word
            for word in jieba.lcut_for_search(query)
            if len(word.strip()) > 1
        }
        return [
            doc
            for doc in docs
            if any(word in doc.content for word in keywords)
        ]

    def execute(self, request: RequestPayload, user: UserContext) -> Generator[str, None, None]:
        self._reset_metadata()
        history = self.history_mgr.get_recent_turns(request.session_id)
        self.history_mgr.append_user_message(request.session_id, request.query)
        llm_only_query = self._extract_llm_only_query(request.query)
        if llm_only_query:
            yield from self._answer_with_llm_only(
                llm_only_query,
                request.session_id,
            )
            return

        routing_context = (
            self.structured_retriever.get_routing_context(user)
            if user.is_authenticated()
            else {}
        )
        route = self.llm_service.analyze_query(
            history,
            request.query,
            user,
            routing_context,
        )
        query = route.rewritten_query or request.query

        if route.intent == "clarification":
            if QueryIntentRouter.has_schedule_time_constraint(request.query):
                answer = "请补充一下你想查询这段时间的课程安排，还是想了解某一节课的作用。"
            else:
                answer = "请补充一下你想查询学院的哪方面信息，例如所属学院、学院历史、通知或专业设置。"
            self.history_mgr.append_ai_message(request.session_id, answer)
            self._last_response_metadata = self._build_response_metadata(
                docs=[],
                answer_origin="system",
            )
            yield answer
            return

        if route.intent == "procedure" and self._can_attempt_faq(route.intent, request.query):
            local_faq = self.local_faq_retriever.match(request.query)
            if local_faq:
                answer = f"【FAQ标准回答】\n{local_faq.metadata['answer']}"
                self.history_mgr.append_ai_message(request.session_id, answer)
                self._set_direct_metadata(local_faq, answer_origin="faq")
                yield answer
                return

        if user.is_authenticated() and route.intent == "personal_fact":
            if (
                route.personal_field == "schedule"
                and route.personal_action == "explain_course"
            ):
                matches = self.structured_retriever.resolve_schedule_reference(
                    request.query,
                    user,
                    personal_filters=route.personal_filters,
                    allow_rule_fallback=not route.used_llm,
                )
                if len(matches) == 1:
                    yield from self._explain_resolved_course(
                        request.query,
                        matches[0],
                        request.session_id,
                    )
                    return
                if not matches:
                    answer = "我没有在你的课表中定位到这节课，请补充具体星期和上课时间。"
                else:
                    course_names = "、".join(item.course_name for item in matches[:4])
                    answer = f"这段时间匹配到多节课：{course_names}。请说明你想了解哪一节课。"
                self.history_mgr.append_ai_message(request.session_id, answer)
                self._last_response_metadata = self._build_response_metadata(
                    docs=[],
                    answer_origin="system",
                )
                yield answer
                return

            # Exact personal facts must not depend on query rewriting or the
            # availability of an external LLM service.
            direct_answer = self.structured_retriever.answer_personal_query(
                request.query,
                user,
                personal_field=route.personal_field,
                personal_action=route.personal_action,
                personal_filters=route.personal_filters,
                allow_rule_fallback=not route.used_llm,
            )
            if direct_answer:
                self.history_mgr.append_ai_message(request.session_id, direct_answer)
                self._last_response_metadata = self._build_response_metadata(
                    docs=[
                        Document(
                            id="structured-personal-direct",
                            content=direct_answer,
                            score=1.0,
                            source="MySQL个人档案",
                            metadata={"record_type": route.personal_field or "profile"},
                        )
                    ],
                    answer_origin="personal",
                )
                yield direct_answer
                return

            structured_docs = self._filter_structured_personal_docs(
                request.query,
                self.structured_retriever.get_personal_documents(user),
            )
            vector_docs = self.retriever.search(
                request.query,
                [settings.COLLECTION_PERSONAL],
                top_k=5,
                filters=f"user_id == '{user.user_id}'",
            )
            vector_docs = [
                doc
                for doc in vector_docs
                if self._is_relevant(
                    request.query,
                    doc,
                    self.PERSONAL_VECTOR_THRESHOLD,
                )
            ]
            personal_docs = self._keyword_rerank(
                request.query,
                structured_docs + vector_docs,
                final_k=5,
            )
            if personal_docs:
                yield from self._generate_and_record(
                    request.query,
                    personal_docs,
                    self._personal_prompt(),
                    request.session_id,
                )
                return

        if self._can_attempt_faq(route.intent, request.query):
            local_faq = self.local_faq_retriever.match(request.query)
            if local_faq:
                answer = f"【FAQ标准回答】\n{local_faq.metadata['answer']}"
                self.history_mgr.append_ai_message(request.session_id, answer)
                self._set_direct_metadata(local_faq, answer_origin="faq")
                yield answer
                return

        if route.intent == "procedure" and self._can_attempt_faq(route.intent, query):
            faq_results = self.retriever.search(
                query,
                [settings.COLLECTION_FAQ],
                top_k=1,
            )
            if faq_results and faq_results[0].score >= self.FAQ_THRESHOLD:
                answer = faq_results[0].metadata.get("answer")
                if answer:
                    final_answer = f"【官方回答】\n{answer}"
                    self.history_mgr.append_ai_message(
                        request.session_id,
                        final_answer,
                    )
                    self._set_direct_metadata(
                        faq_results[0],
                        answer_origin="faq",
                    )
                    yield final_answer
                    return

        collections = [settings.COLLECTION_STANDARD]
        candidates: List[Document] = []
        if user.is_authenticated():
            # ACADEMIC_URLS are stored in rag_knowledge and treated as campus
            # internal crawled content in the unified product experience.
            collections.append(settings.COLLECTION_KNOWLEDGE)
            notice_docs = self.structured_retriever.get_notice_documents(user)
            candidates.extend(self._filter_notices(query, notice_docs))

        resolved_identity_entity = (
            user.is_authenticated()
            and route.intent == "campus_knowledge"
            and query != request.query
        )
        search_collections = (
            [settings.COLLECTION_KNOWLEDGE]
            if resolved_identity_entity
            else collections
        )
        campus_docs = self.retriever.search(
            query,
            search_collections,
            top_k=20,
        )
        relevant_campus_docs = [
            doc
            for doc in campus_docs
            if self._is_relevant(query, doc, self.CAMPUS_VECTOR_THRESHOLD)
        ]
        if resolved_identity_entity and not relevant_campus_docs:
            public_docs = self.retriever.search(
                query,
                [settings.COLLECTION_STANDARD],
                top_k=20,
            )
            relevant_campus_docs = [
                doc
                for doc in public_docs
                if self._is_relevant(
                    query,
                    doc,
                    self.CAMPUS_VECTOR_THRESHOLD,
                )
            ]
        candidates.extend(relevant_campus_docs)
        docs = self._keyword_rerank(query, candidates, final_k=6)

        if docs:
            prompt = self._campus_prompt()
        else:
            prompt = self._fallback_prompt()

        yield from self._generate_and_record(
            query,
            docs,
            prompt,
            request.session_id,
        )

    def _explain_resolved_course(
        self,
        original_query: str,
        course,
        session_id: str,
    ) -> Generator[str, None, None]:
        weekday_names = {
            1: "周一",
            2: "周二",
            3: "周三",
            4: "周四",
            5: "周五",
            6: "周六",
            7: "周日",
        }
        schedule_text = weekday_names.get(course.weekday, "日期待补充")
        if course.start_time and course.end_time:
            schedule_text += (
                f" {course.start_time.strftime('%H:%M')}-"
                f"{course.end_time.strftime('%H:%M')}"
            )
        prompt = """你是一个课程概念解释助手。用户先通过自己的课表定位到一门课，随后询问这门课的作用、用途或它是干什么的。
课表只用于确定课程名称，不提供课程解释内容。请基于通用知识解释这门课程通常学习什么、有什么作用、能用来做什么。
如果课程名称过于宽泛，请说明这是一般性解释。

【已定位课程】
课程名称：{context}

【用户问题】
{question}

【回答】"""
        query = (
            f"用户课表中 {schedule_text} 的课程是“{course.course_name}”。"
            f"请回答：{original_query}"
        )
        full_answer = ""
        try:
            for chunk in self.llm_service.generate_answer(
                query,
                [
                    Document(
                        id=f"schedule-course-{course.id}",
                        content=course.course_name,
                        score=1.0,
                        source=f"MySQL个人课表｜{schedule_text}",
                        metadata={"record_type": "schedule"},
                    )
                ],
                prompt,
            ):
                full_answer += chunk
                yield chunk
        except Exception:
            logger.exception("Course explanation generation failed")
            full_answer = SERVICE_UNAVAILABLE_MESSAGE
            yield full_answer
        self.history_mgr.append_ai_message(session_id, full_answer)
        self._last_response_metadata = {
            "answer_origin": "llm",
            "confidence": "low",
            "notice": "已用个人课表定位课程名称，课程作用说明由大模型基于通用知识生成。",
            "sources": [
                {
                    "title": "MySQL个人课表",
                    "url": None,
                    "snippet": f"{schedule_text}，{course.course_name}",
                    "score": 1.0,
                    "source_type": "personal",
                }
            ],
        }

    @staticmethod
    def _filter_structured_personal_docs(
        query: str,
        docs: List[Document],
    ) -> List[Document]:
        keywords = {
            word.strip()
            for word in jieba.lcut_for_search(query)
            if len(word.strip()) > 1
            and word.strip() not in {"我的", "本人", "个人", "当前", "查询", "查看"}
        }
        if any(word in query for word in ("个人信息", "个人档案", "我的信息", "我的档案")):
            return [
                doc
                for doc in docs
                if doc.metadata.get("record_type") == "profile"
            ]
        return [
            doc
            for doc in docs
            if any(keyword in doc.content for keyword in keywords)
        ]

    def _generate_and_record(
        self,
        query: str,
        docs: List[Document],
        prompt: str,
        session_id: str,
    ) -> Generator[str, None, None]:
        full_answer = ""
        try:
            for chunk in self.llm_service.generate_answer(query, docs, prompt):
                full_answer += chunk
                yield chunk
        except Exception:
            logger.exception("Auto answer generation failed")
            if docs:
                full_answer = self._format_document_fallback(query, docs)
            else:
                full_answer = SERVICE_UNAVAILABLE_MESSAGE
            yield full_answer
        self.history_mgr.append_ai_message(session_id, full_answer)
        self._last_response_metadata = self._build_response_metadata(
            docs=docs,
            answer_origin="rag" if docs else "llm",
        )

    @staticmethod
    def _format_document_fallback(
        query: str,
        docs: List[Document],
    ) -> str:
        lines = ["已从现有数据库检索到以下相关资料："]
        keywords = {
            word.strip()
            for word in jieba.lcut_for_search(query)
            if len(word.strip()) > 1
            and word.strip()
            not in {
                "同济大学",
                "哪一年",
                "什么",
                "我的",
                "所在",
                "所属",
                "目前",
                "当前",
            }
        }
        candidates = []
        for doc in docs:
            segments = re.split(r"(?<=[。！？；])|\n+", doc.content)
            for segment in segments:
                content = " ".join(segment.split()).strip()
                if len(content) < 8:
                    continue
                matched = sum(keyword in content for keyword in keywords)
                score = matched
                if "成立" in query and "成立" in content:
                    score += 3
                if re.search(r"(?:19|20)\d{2}年", content):
                    score += 1
                if "计算机科学与技术学院" in query and (
                    "计算机科学与技术学院" in content
                ):
                    score += 4
                candidates.append((score, content, doc.source))

        candidates.sort(key=lambda item: item[0], reverse=True)
        selected = []
        seen = set()
        best_score = candidates[0][0] if candidates else 0
        for score, content, source in candidates:
            signature = (content, source)
            if (
                score <= 0
                or score < best_score - 1
                or signature in seen
            ):
                continue
            seen.add(signature)
            selected.append((content, source))
            if len(selected) == 2:
                break

        if not selected:
            selected = [
                (" ".join(docs[0].content.split())[:500], docs[0].source)
            ]

        for content, source in selected:
            if len(content) > 500:
                content = content[:500] + "..."
            source = source or "校园资料库"
            lines.append(f"- {content}（来源：{source}）")
        lines.append("当前大模型服务不可用，以上为数据库原始资料摘要。")
        return "\n".join(lines)

    def _retrieve_strategy(self, query: str, user: UserContext) -> List[Document]:
        return []

    def _get_prompt_template(self) -> str:
        return self._campus_prompt()

    @staticmethod
    def _personal_prompt() -> str:
        return """你是个人信息查询助手。参考资料仅属于当前登录用户。
请优先、准确、简洁地回答用户问题，数字和时间必须保持原值。
不要泄露无关个人信息，不要使用资料之外的内容。

【当前用户个人资料】
{context}

【问题】
{question}

【回答】
"""

    @staticmethod
    def _campus_prompt() -> str:
        return """你是同济大学校园问答助手。请只依据检索到的校园资料回答。
资料来源优先级为：当前用户可见的校内通知，其次是公开及校内爬取内容。
直接回答问题，保留原始日期、时间、地点和数值；资料不足时明确说明不足，
不要编造学校规定、联系方式或个人信息。

【校园资料】
{context}

【问题】
{question}

【回答】
"""

    @staticmethod
    def _fallback_prompt() -> str:
        return """你是一个通用智能助手。校园个人库、通知库和爬取资料中没有找到
可用于回答当前问题的内容，因此请使用通用知识回答。不得声称答案来自同济大学
数据库或官方文件；涉及校内实时规定、个人数据或不确定事实时，应明确提示用户
需要以学校官方信息为准。

【问题】
{question}

【回答】
"""


class PublicPipeline(BasePipeline):
    # 官方问答命中阈值
    FAQ_THRESHOLD = 0.8

    def execute(self, request: RequestPayload, user: UserContext) -> Generator[str, None, None]:
        self._reset_metadata()
        # 记录上下文 查询重写
        history = self.history_mgr.get_recent_turns(request.session_id)
        self.history_mgr.append_user_message(request.session_id, request.query)

        llm_only_query = self._extract_llm_only_query(request.query)
        if llm_only_query:
            yield from self._answer_with_llm_only(
                llm_only_query,
                request.session_id,
            )
            return

        local_faq = self.local_faq_retriever.match(request.query)
        if local_faq:
            final_output = f"【FAQ标准回答】\n{local_faq.metadata['answer']}"
            yield final_output
            self.history_mgr.append_ai_message(request.session_id, final_output)
            self._set_direct_metadata(local_faq, answer_origin="faq")
            return

        llm_only_query = self._extract_llm_only_query(request.query)
        if llm_only_query:
            self.history_mgr.append_user_message(request.session_id, request.query)
            yield from self._answer_with_llm_only(
                llm_only_query,
                request.session_id,
            )
            return

        rewritten_query = self.llm_service.rewrite_query(history, request.query)
        
        # 第一步：检索官方 FAQ 库
        faq_results = (
            self.retriever.search(
                rewritten_query.rewritten_text,
                [settings.COLLECTION_FAQ],
                top_k=1,
            )
            if LocalFAQRetriever.is_specific_faq_query(rewritten_query.rewritten_text)
            else []
        )
        
        # 判断是否命中
        if faq_results and faq_results[0].score >= self.FAQ_THRESHOLD:
            # 直接从 metadata 里拿出 answer 字段
            direct_answer = faq_results[0].metadata.get("answer")
            
            if direct_answer:
                print(f"[PublicPipeline] FAQ Hit! Score: {faq_results[0].score}")
                
                # 加上前缀
                final_output = f"【官方回答】\n{direct_answer}"
                
                # 直接返回，不调 LLM
                yield final_output
                self.history_mgr.append_ai_message(request.session_id, final_output)
                self._set_direct_metadata(faq_results[0], answer_origin="faq")
                return 

        # RAG 流程
        docs = self._retrieve_strategy(rewritten_query.rewritten_text, user)
        
        full_answer = ""
        prompt_tmpl = self._get_prompt_template()
        try:
            for chunk in self.llm_service.generate_answer(rewritten_query.rewritten_text, docs, prompt_tmpl):
                full_answer += chunk
                yield chunk
        except Exception as e:
            logger.exception("Public answer generation failed")
            err = SERVICE_UNAVAILABLE_MESSAGE
            yield err
            full_answer += err
        self.history_mgr.append_ai_message(request.session_id, full_answer)
        self._last_response_metadata = self._build_response_metadata(
            docs=docs,
            answer_origin="rag" if docs else "llm",
        )

    def _retrieve_strategy(self, query: str, user: UserContext) -> List[Document]:
        # 策略修改：
        # 先从向量库拿出 15 条 (Recall)
        # 通过关键词重排序取前 4 条 (Precision)
        candidates = self.retriever.search(query, [settings.COLLECTION_STANDARD], top_k=15)
        return self._keyword_rerank(query, candidates, final_k=4)

    def _get_prompt_template(self) -> str:
        return """你是一位严谨、细致的同济大学校园信息助手。你的核心目标是完全按照【参考资料】中的信息，为用户提供准确的回答，绝不凭空编造任何内容，禁止质疑用户提问。

        请严格按照以下【思维链】步骤进行思考，然后生成回答：

        1. **需求拆解**：分析用户问题中包含的具体实体（如具体的时间、地点、部门、办事流程）。
        2. **定向提取**：只从【参考资料】中提取所有相关的细节信息（例如：具体的门牌号、电话号码、办公时间、所需材料清单、注意事项等），不要遗漏任何微小的补充说明，但也不要依靠常识进行补充。
        3. **事实核查**：检查提取的信息是否足以回答用户问题。如果资料中的信息相互补充，请进行整合；如果信息存在冲突，禁止在回复中体现资料冲突，请按照日期优先级第一、官方性优先级第二进行筛选，只依靠最权威的内容进行回答。
        4. **结构化输出**：将整合后的信息组织成条理分明的回答，只精准回答用户问题，不显示思考过程，并确保包含所有关键细节。
         
        【参考资料】
        {context}

        【用户问题】
        {question}

        【回答要求】
        - **详尽为先**：不要为了简洁而省略关键步骤或细节。如果资料中有具体的“注意事项”或“温馨提示”，请务必包含在回答中。
        - **真实准确**：只允许使用【参考资料】中的信息进行回答。**严禁**基于常识或推测进行补充。严禁编造资料中不存在的联系方式或规定。如果资料中没有直接答案，请明确告知“根据现有资料暂时无法确认”，并提供资料中已有的最相关信息（如相关部门的总机）供用户参考。
        - **格式规范**：对于流程、清单类信息，必须使用分点（1. 2. 3.）或列表形式展示。
        - **语言风格**：禁止显示出你的内部思考过程，直接给出最终答案。语言应正式、专业，避免口语化表达。

        【你的回答】
        """

class AcademicPipeline(BasePipeline):
    def _retrieve_strategy(self, query: str, user: UserContext) -> List[Document]:
        # 学术场景：召回更多候选，混合排序
        candidates = self.retriever.search(
            query, 
            [settings.COLLECTION_STANDARD, settings.COLLECTION_KNOWLEDGE], 
            top_k=20
        )
        return self._keyword_rerank(query, candidates, final_k=6)
    
    def _get_prompt_template(self) -> str:
        return """你是一位专业的学术助手。请基于检索到的【参考资料】，为用户提供一份逻辑清晰、有据可依的回答。

        注意：你拿到的资料可能是不完整的文本块，请基于现有内容回答，不要编造。

        【参考资料】
        {context}

        【学术问题】
        {question}

        【回答结构】
        1. **直接回答**：开门见山地给出核心结论。
        2. **详细阐述**：
        - 将信息归纳为几个要点（如理论定义、数据支持、相关研究等）。
        - **必须标注来源**：引用观点或数据时，在句末加上 `[来源]`。
        - 对比不同来源的信息，指出它们是相互印证还是存在差异。
        3. **补充说明**：
        - 列出关键的硬性数据（年份、数值、算法名）。
        - 诚实地指出**未检索到**的信息（例如：“资料中未提及具体的实验对比数据”）。

        【要求】
        - 语言客观、严谨，避免口语化。
        - 逻辑连贯，不要简单罗列“片段A说了...片段B说了...”，而是融合观点。

        【回答】
        """

class InternalPipeline(BasePipeline):
    def _retrieve_strategy(self, query: str, user: UserContext) -> List[Document]:
        # 内部场景策略：
        
        # 扩大初筛范围 (Recall Phase)
        docs_public = self.retriever.search(query, [settings.COLLECTION_STANDARD, settings.COLLECTION_KNOWLEDGE], top_k=10)
        docs_internal = self.structured_retriever.get_notice_documents(user)
        
        # 合并
        all_candidates = docs_public + docs_internal
        
        # 2. 统一重排序 (Rerank Phase)
        return self._keyword_rerank(query, all_candidates, final_k=5)

    def _get_prompt_template(self) -> str:
        return """你是一位内部行政助手。请基于检索到的【内部通知/资讯】，快速、准确地回复用户。

        【内部通知/资讯】
        {context}

        【用户问题】
        {question}

        【回复规范】
        1. **直切要点**：直接提炼通知或规定的核心内容（如：时间、地点、对象、具体要求）。
        2. **数据精准**：对于截止日期、联系电话、办公地点、金额等关键信息，**必须保持原文**，严禁修改或四舍五入。
        3. **条理分明**：如果涉及多个步骤或要求，请使用列表（1. 2. 3.）清晰展示。
        4. **严谨兜底**：如果资料中没有提到相关内容，请直接回答“当前内部资料中未找到相关说明”，不要依据常识推测。

        【回复】
        """

class PersonalPipeline(BasePipeline):
    def _retrieve_strategy(self, query: str, user: UserContext) -> List[Document]:
        if not user.user_id:
            raise ValueError("User ID required")
        
        candidates = self.retriever.search(
            query, 
            [settings.COLLECTION_PERSONAL], 
            top_k=10, 
            filters=f"user_id == '{user.user_id}'"
        )
        candidates.extend(self.structured_retriever.get_personal_documents(user))
        return self._keyword_rerank(query, candidates, final_k=5)

    def _get_prompt_template(self) -> str:
        return """你是一位智能、严谨的个人数据助理。请基于【数据片段】，通过逻辑推理回答用户的查询。

        请遵循以下【思维链】步骤进行处理：

        1.  **意图解析**：
            - 分析用户具体想查询哪个字段（如：某一门课的成绩、某笔消费的金额、某天的具体课程）。
            - 确认查询的时间范围或限定条件。

        2.  **数据提取与核对**：
            - 在数据片段中定位精确的记录。
            - **关键校验**：检查数字精度（如分数、金额），严禁四舍五入或修改原始数值（例如：59.9分必须保留为59.9，不能改为60）。

        3.  **隐私边界检查**：
            - 确认提取的数据是否仅限于用户询问的范围。
            - 过滤掉无关的敏感信息（如：查成绩时不要附带身份证号或家庭住址）。

        4.  **回复构建**：
            - 将提取的数据转化为清晰、自然的回答。
            - 如果涉及多条数据（如列表），请使用分点或清单形式展示，确保一目了然。
            - 如果数据为空或未找到，直接说明“未查询到相关记录”。

        【数据片段】
        {context}

        【用户指令】
        {question}

        【回复】
        """
