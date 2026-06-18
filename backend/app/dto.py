from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any

# --- 认证相关 ---
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_info: Dict[str, Any]

class RefreshRequest(BaseModel):
    refresh_token: str

# --- 上下文对象 (内部使用) ---
class UserContext(BaseModel):
    user_id: str
    user_name: str = "Guest"
    user_role: str # guest, student, teacher
    dept_id: Optional[str] = None
    scopes: List[str] = []

    def is_authenticated(self) -> bool:
        return self.user_role != "guest"

# --- RAG 业务相关 ---
class RequestPayload(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str
    stream: bool = True
    enable_rag: bool = True
    
class Document(BaseModel):
    id: str
    content: str
    score: float
    source: str
    metadata: Dict[str, Any] = {}

class RewrittenQuery(BaseModel):
    original_text: str
    rewritten_text: str


class QueryRoute(BaseModel):
    intent: str
    rewritten_query: str
    personal_field: Optional[str] = None
    personal_action: Optional[str] = None
    personal_filters: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    used_llm: bool = False


# --- 会话管理相关 ---
class SessionSchema(BaseModel):
    session_id: str
    title: str
    type: str = "auto"
    created_at: str

class SessionListResponse(BaseModel):
    data: List[SessionSchema]

class ChatMessage(BaseModel):
    role: str      # user / assistant
    content: str
    timestamp: float = 0.0

class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: List[ChatMessage]

class CreateSessionRequest(BaseModel):
    type: str = "auto"


class StudentProfileSchema(BaseModel):
    student_no: Optional[str] = None
    college_name: Optional[str] = None
    major: Optional[str] = None
    grade_year: Optional[int] = None
    class_name: Optional[str] = None
    gpa: Optional[float] = None
    major_rank: Optional[int] = None
    earned_credits: Optional[float] = None
    campus: Optional[str] = None


class TeacherProfileSchema(BaseModel):
    employee_no: Optional[str] = None
    college_name: Optional[str] = None
    title: Optional[str] = None
    office: Optional[str] = None
    research_direction: Optional[str] = None
    campus: Optional[str] = None


class CourseScheduleSchema(BaseModel):
    id: int
    semester: Optional[str] = None
    course_code: Optional[str] = None
    course_name: str
    instructor: Optional[str] = None
    weekday: Optional[int] = None
    start_section: Optional[int] = None
    end_section: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location: Optional[str] = None
    week_range: Optional[str] = None
    status: str


class StudentGradeSchema(BaseModel):
    id: int
    semester: Optional[str] = None
    course_code: Optional[str] = None
    course_name: str
    score: Optional[float] = None
    grade_point: Optional[float] = None
    credits: Optional[float] = None


class StudentExamSchema(BaseModel):
    id: int
    subject: str
    exam_time: str
    location: Optional[str] = None
    exam_method: Optional[str] = None


class MyProfileResponse(BaseModel):
    user_id: str
    username: str
    full_name: Optional[str] = None
    role: str
    dept_id: Optional[str] = None
    student_profile: Optional[StudentProfileSchema] = None
    teacher_profile: Optional[TeacherProfileSchema] = None
    course_schedules: List[CourseScheduleSchema] = Field(default_factory=list)
    grades: List[StudentGradeSchema] = Field(default_factory=list)
    exams: List[StudentExamSchema] = Field(default_factory=list)


class NoticeSchema(BaseModel):
    id: int
    title: str
    content: str
    dept_id: Optional[str] = None
    audience: str
    source: Optional[str] = None
    published_at: str
    expires_at: Optional[str] = None


class SourceReferenceSchema(BaseModel):
    title: str
    url: Optional[str] = None
    snippet: str
    score: float
    source_type: str = "rag"


class AnswerMetadataSchema(BaseModel):
    answer_origin: str
    confidence: str
    notice: str
    sources: List[SourceReferenceSchema] = Field(default_factory=list)


class AdminFAQSchema(BaseModel):
    id: int
    question: str
    answer: str
    source: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    is_active: bool = True


class AdminFAQUpsertRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1)
    source: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    is_active: bool = True


class AdminKnowledgeBlockSchema(BaseModel):
    id: int
    task_id: int
    title: Optional[str] = None
    section: Optional[str] = None
    url: str
    collection_name: Optional[str] = None
    access_scope: Optional[str] = None
    text_preview: Optional[str] = None
    text_content: Optional[str] = None
    milvus_id: Optional[str] = None
    created_at: str


class AdminKnowledgeBlockUpdateRequest(BaseModel):
    title: Optional[str] = None
    section: Optional[str] = None
    url: Optional[str] = None
    access_scope: Optional[str] = None
    text_content: str = Field(min_length=1)


class AdminCrawlPreviewRequest(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    max_pages: int = Field(default=8, ge=1, le=30)


class AdminCrawlBlockSchema(BaseModel):
    title: str
    section: str
    url: str
    text: str


class AdminCrawlPreviewResponse(BaseModel):
    source_url: str
    blocks: List[AdminCrawlBlockSchema]


class AdminCrawlSaveRequest(BaseModel):
    source_url: str
    access_scope: str = Field(pattern="^(public|campus)$")
    blocks: List[AdminCrawlBlockSchema]

