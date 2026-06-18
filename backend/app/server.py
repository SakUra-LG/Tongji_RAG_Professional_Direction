import uvicorn
import json
import redis
import jwt
import datetime
import uuid
import hashlib
import logging
from jwt.exceptions import PyJWTError, ExpiredSignatureError
from fastapi import FastAPI, Header, HTTPException, Depends, status, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware 
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select
from passlib.context import CryptContext
from typing import Set

from app.config import settings
from app.dto import (
    RequestPayload, UserContext, LoginRequest, LoginResponse, RefreshRequest,
    SessionSchema, SessionListResponse, SessionHistoryResponse, CreateSessionRequest,
    CourseScheduleSchema, MyProfileResponse, NoticeSchema, StudentGradeSchema,
    StudentExamSchema, StudentProfileSchema, TeacherProfileSchema,
    AdminCrawlPreviewRequest, AdminCrawlPreviewResponse, AdminCrawlSaveRequest,
    AdminFAQSchema, AdminFAQUpsertRequest, AdminKnowledgeBlockSchema,
    AdminKnowledgeBlockUpdateRequest,
)
from app.database import get_db
from app.models_db import (
    CampusNotice,
    CourseSchedule,
    CrawlBlock,
    CrawlTask,
    ManagedFAQ,
    StudentExam,
    StudentGrade,
    StudentProfile,
    TeacherProfile,
    User,
)
from app.admin_tools import crawl_preview, extract_insert_ids, save_blocks_to_knowledge_base
from app.pipelines import (
    AcademicPipeline,
    AutoPipeline,
    InternalPipeline,
    PersonalPipeline,
    PublicPipeline,
)
from app.components import HistoryManager
from pymilvus import MilvusClient
from scripts.sync_manual_faqs import sync_manual_faqs

logger = logging.getLogger(__name__)

VALID_SESSION_TYPES = {"auto", "public", "academic", "internal", "personal"}

app = FastAPI(title="Tongji RAG System")

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 基础设施初始化 ---
redis_client = redis.Redis(
    host=settings.REDIS_HOST, 
    port=settings.REDIS_PORT, 
    password=settings.REDIS_PASSWORD,
    decode_responses=True
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
history_manager = HistoryManager()

# --- 权限常量定义 ---
ROLE_GUEST = "guest"
ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLE_ADMIN = "admin"

# 路由权限表
ROUTE_PERMISSIONS = {
    "auto": {ROLE_GUEST, ROLE_STUDENT, ROLE_TEACHER, ROLE_ADMIN},
    "public": {ROLE_GUEST, ROLE_STUDENT, ROLE_TEACHER, ROLE_ADMIN},
    "academic": {ROLE_STUDENT, ROLE_TEACHER},
    "internal": {ROLE_STUDENT, ROLE_TEACHER},
    "personal": {ROLE_STUDENT, ROLE_TEACHER}
}

# Pipeline 工厂
pipelines = {
    "auto": AutoPipeline(),
    "public": PublicPipeline(),
    "academic": AcademicPipeline(),
    "internal": InternalPipeline(),
    "personal": PersonalPipeline()
}

security = HTTPBearer(auto_error=False)

# --- 辅助函数 ---
def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def refresh_token_key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{settings.REDIS_REFRESH_PREFIX}{digest}"

def create_tokens(user_id: str, role: str, dept: str = None):
    access_expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_payload = {
        "sub": user_id,
        "role": role,
        "dept": dept,
        "type": "access",
        "exp": access_expire
    }
    access_token = jwt.encode(access_payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    refresh_expire = datetime.datetime.utcnow() + datetime.timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    refresh_payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "exp": refresh_expire
    }
    refresh_token = jwt.encode(refresh_payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    return access_token, refresh_token, settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

# --- 鉴权依赖 ---
async def get_current_user(
    auth: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> UserContext:
    # 1. 如果没有携带 Authorization 头，auth 会是 None -> 降级为游客
    if not auth:
        return UserContext(user_id="guest", user_role=ROLE_GUEST)
    
    # 2. 提取 Token
    token = auth.credentials 

    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")

        user_id = str(payload.get("sub"))
        token_role = payload.get("role", ROLE_GUEST)
        if token_role == ROLE_GUEST:
            if not user_id.startswith("guest_"):
                raise HTTPException(status_code=401, detail="Invalid guest token")
            return UserContext(user_id=user_id, user_role=ROLE_GUEST)

        if not user_id.isdigit():
            raise HTTPException(status_code=401, detail="Invalid user identity")

        result = await db.execute(select(User).where(User.id == int(user_id)))
        db_user = result.scalars().first()
        if (
            db_user is None
            or not db_user.is_active
            or db_user.role not in {ROLE_STUDENT, ROLE_TEACHER, ROLE_ADMIN}
        ):
            raise HTTPException(status_code=401, detail="User is unavailable")

        # Role and department are always read from MySQL. Token claims are
        # only a signed identity hint and may be stale after account changes.
        return UserContext(
            user_id=str(db_user.id),
            user_name=db_user.full_name or db_user.username,
            user_role=db_user.role,
            dept_id=db_user.dept_id,
        )
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# --- API 接口 ---

@app.post("/api/v1/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    # 兼容处理：init_sql.py 里插入的是 id=1, id=2...
    result = await db.execute(select(User).where(User.username == request.username))
    user = result.scalars().first()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")
    if user.role not in {ROLE_STUDENT, ROLE_TEACHER, ROLE_ADMIN}:
        raise HTTPException(status_code=403, detail="Unsupported user role")

    at, rt, expires_in = create_tokens(str(user.id), user.role, user.dept_id)
    
    redis_key = refresh_token_key(rt)
    redis_client.set(redis_key, str(user.id), ex=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600)
    
    return LoginResponse(
        access_token=at, 
        refresh_token=rt, 
        expires_in=expires_in,
        user_info={
            "id": str(user.id),
            "name": user.full_name,
            "role": user.role,
            "department": user.dept_id,
        }
    )

# 游客登录接口
@app.post("/api/v1/guest-login", response_model=LoginResponse)
async def guest_login():
    guest_id = f"guest_{uuid.uuid4()}"
    at, rt, expires_in = create_tokens(user_id=guest_id, role=ROLE_GUEST, dept=None)
    
    redis_key = refresh_token_key(rt)
    redis_client.set(redis_key, guest_id, ex=86400)
    
    return LoginResponse(
        access_token=at, 
        refresh_token=rt, 
        expires_in=expires_in,
        user_info={
            "name": "访客", 
            "role": ROLE_GUEST,
            "id": guest_id
        }
    )

@app.post("/api/v1/refresh")
async def refresh_token(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    old_rt = request.refresh_token
    redis_key_old = refresh_token_key(old_rt)
    
    user_id = redis_client.get(redis_key_old)
    if not user_id:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    
    user_role = ROLE_GUEST
    user_name = "访客"
    user_dept = None

    if str(user_id).startswith("guest_"):
        user_role = ROLE_GUEST
    else:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalars().first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user_role = user.role
        user_name = user.full_name
        user_dept = user.dept_id

    new_at, new_rt, expires_in = create_tokens(str(user_id), user_role, user_dept)
    
    pipe = redis_client.pipeline()
    try:
        pipe.delete(redis_key_old)
        redis_key_new = refresh_token_key(new_rt)
        ttl = 86400 if user_role == ROLE_GUEST else settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
        pipe.set(redis_key_new, str(user_id), ex=ttl)
        pipe.execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Token rotation failed")
    
    return {
        "access_token": new_at, 
        "refresh_token": new_rt, 
        "expires_in": expires_in, 
        "user_info": {
            "id": str(user_id),
            "name": user_name,
            "role": user_role,
            "department": user_dept,
        }
    }

@app.post("/api/v1/logout")
async def logout(request: RefreshRequest):
    redis_key = refresh_token_key(request.refresh_token)
    redis_client.delete(redis_key)
    return {"message": "Logged out successfully"}

@app.get("/health")
async def health():
    redis_client.ping()
    return {"status": "ok"}


async def require_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if user.user_role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin account required")
    return user


def faq_to_schema(faq: ManagedFAQ) -> AdminFAQSchema:
    return AdminFAQSchema(
        id=faq.id,
        question=faq.question,
        answer=faq.answer,
        source=faq.source,
        aliases=[item.strip() for item in (faq.aliases or "").splitlines() if item.strip()],
        is_active=faq.is_active,
    )


def sync_faq_collection() -> None:
    client = MilvusClient(uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}")
    sync_manual_faqs(client)


def infer_block_collection(block: CrawlBlock, task: CrawlTask | None) -> str:
    if block.collection_name:
        return block.collection_name
    if task and task.collection_name:
        return task.collection_name
    url = block.url or ""
    for collection_name in (
        settings.COLLECTION_STANDARD,
        settings.COLLECTION_KNOWLEDGE,
        settings.COLLECTION_FAQ,
        settings.COLLECTION_INTERNAL,
        settings.COLLECTION_PERSONAL,
    ):
        if collection_name in url:
            return collection_name
    return ""


def infer_access_scope(collection_name: str, block: CrawlBlock) -> str:
    if block.access_scope:
        return block.access_scope
    if collection_name in {settings.COLLECTION_STANDARD, settings.COLLECTION_FAQ}:
        return "public"
    if collection_name in {settings.COLLECTION_KNOWLEDGE, settings.COLLECTION_INTERNAL}:
        return "campus"
    return ""


def collection_for_access_scope(access_scope: str | None, fallback: str) -> str:
    if access_scope == "public":
        return settings.COLLECTION_STANDARD
    if access_scope == "campus":
        return settings.COLLECTION_KNOWLEDGE
    return fallback


def admin_delete_milvus_rows(
    collection_name: str,
    milvus_id: str | None,
    *,
    title: str | None = None,
    url: str | None = None,
) -> int:
    if not collection_name:
        return 0
    ids_to_delete: Set[int] = set()
    if milvus_id:
        ids_to_delete.add(int(milvus_id))

    title = (title or "").strip()
    url = (url or "").strip()
    if title or url:
        client = MilvusClient(uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}")
        try:
            rows = client.query(
                collection_name=collection_name,
                filter="",
                output_fields=["id", "source"],
                limit=10000,
            )
        except Exception as exc:
            logger.warning("Failed to query Milvus sources before delete: %s", exc)
            rows = []
        exact_source = f"{title} | {url}" if title and url else ""
        for row in rows:
            source = str(row.get("source", ""))
            if (
                (exact_source and source == exact_source)
                or (url and url in source)
                or (title and source.startswith(title))
            ):
                ids_to_delete.add(int(row["id"]))

    if not ids_to_delete:
        return 0
    client = MilvusClient(uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}")
    client.delete(collection_name=collection_name, pks=sorted(ids_to_delete))
    client.flush(collection_name=collection_name)
    return len(ids_to_delete)


def admin_insert_milvus_block(
    *,
    collection_name: str,
    text: str,
    title: str,
    url: str,
) -> str | None:
    if not collection_name:
        return None
    client = MilvusClient(uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}")
    result = client.insert(
        collection_name=collection_name,
        data=[
            {
                "vector": [0.0] * 1024,
                "text": text,
                "source": f"{title} | {url}",
                "dept_id": "",
                "user_id": "",
            }
        ],
    )
    client.flush(collection_name=collection_name)
    ids = extract_insert_ids(result)
    return str(ids[0]) if ids else None


def knowledge_block_to_schema(
    item: CrawlBlock,
    task: CrawlTask | None = None,
) -> AdminKnowledgeBlockSchema:
    inferred_collection = infer_block_collection(item, task)
    inferred_scope = infer_access_scope(inferred_collection, item)
    return AdminKnowledgeBlockSchema(
        id=item.id,
        task_id=item.task_id,
        title=item.title,
        section=item.section,
        url=item.url,
        collection_name=inferred_collection,
        access_scope=inferred_scope,
        text_preview=item.text_preview,
        text_content=item.text_content or item.text_preview,
        milvus_id=item.milvus_id,
        created_at=item.created_at.isoformat() if item.created_at else "",
    )


@app.get("/api/v1/admin/faqs", response_model=list[AdminFAQSchema])
async def admin_list_faqs(
    _: UserContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ManagedFAQ).order_by(ManagedFAQ.id.asc()))
    return [faq_to_schema(item) for item in result.scalars().all()]


@app.post("/api/v1/admin/faqs", response_model=AdminFAQSchema)
async def admin_create_faq(
    request: AdminFAQUpsertRequest,
    _: UserContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    faq = ManagedFAQ(
        question=request.question.strip(),
        answer=request.answer.strip(),
        source=(request.source or "后台FAQ").strip(),
        aliases="\n".join(request.aliases),
        is_active=request.is_active,
    )
    db.add(faq)
    await db.commit()
    await db.refresh(faq)
    sync_faq_collection()
    return faq_to_schema(faq)


@app.put("/api/v1/admin/faqs/{faq_id}", response_model=AdminFAQSchema)
async def admin_update_faq(
    faq_id: int,
    request: AdminFAQUpsertRequest,
    _: UserContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ManagedFAQ).where(ManagedFAQ.id == faq_id))
    faq = result.scalars().first()
    if faq is None:
        raise HTTPException(status_code=404, detail="FAQ not found")
    faq.question = request.question.strip()
    faq.answer = request.answer.strip()
    faq.source = (request.source or "后台FAQ").strip()
    faq.aliases = "\n".join(request.aliases)
    faq.is_active = request.is_active
    await db.commit()
    await db.refresh(faq)
    sync_faq_collection()
    return faq_to_schema(faq)


@app.delete("/api/v1/admin/faqs/{faq_id}")
async def admin_delete_faq(
    faq_id: int,
    _: UserContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ManagedFAQ).where(ManagedFAQ.id == faq_id))
    faq = result.scalars().first()
    if faq is None:
        raise HTTPException(status_code=404, detail="FAQ not found")
    await db.delete(faq)
    await db.commit()
    sync_faq_collection()
    return {"deleted": True, "id": faq_id}


@app.get("/api/v1/admin/knowledge", response_model=list[AdminKnowledgeBlockSchema])
async def admin_list_knowledge(
    collection: str | None = None,
    _: UserContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CrawlBlock)
        .order_by(CrawlBlock.created_at.desc(), CrawlBlock.id.desc())
        .limit(1000)
    )
    raw_blocks = list(result.scalars().all())
    task_ids = sorted({item.task_id for item in raw_blocks if item.task_id})
    task_by_id = {}
    if task_ids:
        task_result = await db.execute(select(CrawlTask).where(CrawlTask.id.in_(task_ids)))
        task_by_id = {item.id: item for item in task_result.scalars().all()}

    blocks = []
    for item in raw_blocks:
        task = task_by_id.get(item.task_id)
        inferred_collection = infer_block_collection(item, task)
        if collection and inferred_collection != collection:
            continue
        blocks.append(knowledge_block_to_schema(item, task))
        if len(blocks) >= 200:
            break

    if collection in {None, "", settings.COLLECTION_FAQ}:
        faq_result = await db.execute(
            select(ManagedFAQ).order_by(ManagedFAQ.updated_at.desc(), ManagedFAQ.id.desc())
        )
        for faq in faq_result.scalars().all():
            blocks.append(
                AdminKnowledgeBlockSchema(
                    id=-faq.id,
                    task_id=0,
                    title=faq.question,
                    section="FAQ",
                    url=f"faq://managed/{faq.id}",
                    collection_name=settings.COLLECTION_FAQ,
                    access_scope="public",
                    text_preview=faq.answer[:500],
                    text_content=faq.answer,
                    milvus_id=None,
                    created_at=faq.updated_at.isoformat() if faq.updated_at else "",
                )
            )
            if len(blocks) >= 200:
                break
    return blocks


@app.put("/api/v1/admin/knowledge/{block_id}", response_model=AdminKnowledgeBlockSchema)
async def admin_update_knowledge(
    block_id: int,
    request: AdminKnowledgeBlockUpdateRequest,
    _: UserContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    text_content = request.text_content.strip()
    if not text_content:
        raise HTTPException(status_code=400, detail="Text content is required")

    if block_id < 0:
        faq_id = abs(block_id)
        result = await db.execute(select(ManagedFAQ).where(ManagedFAQ.id == faq_id))
        faq = result.scalars().first()
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ not found")
        faq.question = (request.title or faq.question).strip()
        faq.answer = text_content
        faq.source = faq.source or "后台FAQ"
        await db.commit()
        await db.refresh(faq)
        sync_faq_collection()
        return AdminKnowledgeBlockSchema(
            id=-faq.id,
            task_id=0,
            title=faq.question,
            section="FAQ",
            url=f"faq://managed/{faq.id}",
            collection_name=settings.COLLECTION_FAQ,
            access_scope="public",
            text_preview=faq.answer[:500],
            text_content=faq.answer,
            milvus_id=None,
            created_at=faq.updated_at.isoformat() if faq.updated_at else "",
        )

    result = await db.execute(select(CrawlBlock).where(CrawlBlock.id == block_id))
    block = result.scalars().first()
    if block is None:
        raise HTTPException(status_code=404, detail="Knowledge block not found")

    task = None
    if block.task_id:
        task_result = await db.execute(select(CrawlTask).where(CrawlTask.id == block.task_id))
        task = task_result.scalars().first()

    old_collection = infer_block_collection(block, task)
    new_access_scope = (request.access_scope or block.access_scope or "").strip()
    new_collection = collection_for_access_scope(new_access_scope, old_collection)
    title = (request.title or block.title or "未命名资料").strip()
    url = (request.url or block.url or "").strip()

    try:
        admin_delete_milvus_rows(
            old_collection,
            block.milvus_id,
            title=block.title,
            url=block.url,
        )
        new_milvus_id = admin_insert_milvus_block(
            collection_name=new_collection,
            text=text_content,
            title=title,
            url=url,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Milvus sync failed: {exc}") from exc

    block.title = title[:200]
    block.section = (request.section or block.section or "综合资料").strip()[:50]
    block.url = url
    block.access_scope = new_access_scope or infer_access_scope(new_collection, block)
    block.collection_name = new_collection
    block.text_preview = text_content[:500]
    block.text_content = text_content
    block.milvus_id = new_milvus_id
    await db.commit()
    await db.refresh(block)
    return knowledge_block_to_schema(block, task)


@app.delete("/api/v1/admin/knowledge/{block_id}")
async def admin_delete_knowledge(
    block_id: int,
    _: UserContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if block_id < 0:
        faq_id = abs(block_id)
        result = await db.execute(select(ManagedFAQ).where(ManagedFAQ.id == faq_id))
        faq = result.scalars().first()
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ not found")
        await db.delete(faq)
        await db.commit()
        sync_faq_collection()
        return {"deleted": True, "id": block_id}

    result = await db.execute(select(CrawlBlock).where(CrawlBlock.id == block_id))
    block = result.scalars().first()
    if block is None:
        raise HTTPException(status_code=404, detail="Knowledge block not found")

    task = None
    if block.task_id:
        task_result = await db.execute(select(CrawlTask).where(CrawlTask.id == block.task_id))
        task = task_result.scalars().first()
    collection_name = infer_block_collection(block, task)
    try:
        admin_delete_milvus_rows(
            collection_name,
            block.milvus_id,
            title=block.title,
            url=block.url,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Milvus delete failed: {exc}") from exc

    await db.delete(block)
    await db.commit()
    return {"deleted": True, "id": block_id}


@app.post("/api/v1/admin/crawl/preview", response_model=AdminCrawlPreviewResponse)
async def admin_crawl_preview(
    request: AdminCrawlPreviewRequest,
    _: UserContext = Depends(require_admin),
):
    blocks = crawl_preview(request.url, request.max_pages)
    return AdminCrawlPreviewResponse(source_url=request.url, blocks=blocks)


@app.post("/api/v1/admin/crawl/save")
async def admin_crawl_save(
    request: AdminCrawlSaveRequest,
    _: UserContext = Depends(require_admin),
):
    result = save_blocks_to_knowledge_base(
        start_url=request.source_url,
        access_scope=request.access_scope,
        blocks=[block.model_dump() for block in request.blocks],
    )
    return result

# 会话管理
@app.get("/api/v1/me/profile", response_model=MyProfileResponse)
async def get_my_profile(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.user_role not in {ROLE_STUDENT, ROLE_TEACHER} or not user.user_id.isdigit():
        raise HTTPException(status_code=403, detail="Student or teacher login required")

    result = await db.execute(select(User).where(User.id == int(user.user_id)))
    db_user = result.scalars().first()
    if db_user is None or db_user.role != user.user_role:
        raise HTTPException(status_code=404, detail="User profile not found")

    student_profile = None
    teacher_profile = None
    grades = []
    exams = []

    if db_user.role == ROLE_STUDENT:
        result = await db.execute(
            select(StudentProfile).where(StudentProfile.user_id == db_user.id)
        )
        profile = result.scalars().first()
        if profile:
            student_profile = StudentProfileSchema(
                student_no=profile.student_no,
                college_name=profile.college_name,
                major=profile.major,
                grade_year=profile.grade_year,
                class_name=profile.class_name,
                gpa=float(profile.gpa) if profile.gpa is not None else None,
                major_rank=profile.major_rank,
                earned_credits=(
                    float(profile.earned_credits)
                    if profile.earned_credits is not None
                    else None
                ),
                campus=profile.campus,
            )

        result = await db.execute(
            select(StudentGrade)
            .where(StudentGrade.user_id == db_user.id)
            .order_by(StudentGrade.semester.desc())
        )
        grades = [
            StudentGradeSchema(
                id=item.id,
                semester=item.semester,
                course_code=item.course_code,
                course_name=item.course_name,
                score=float(item.score) if item.score is not None else None,
                grade_point=(
                    float(item.grade_point) if item.grade_point is not None else None
                ),
                credits=float(item.credits) if item.credits is not None else None,
            )
            for item in result.scalars().all()
        ]

        result = await db.execute(
            select(StudentExam)
            .where(StudentExam.user_id == db_user.id)
            .order_by(StudentExam.id.asc())
        )
        exams = [
            StudentExamSchema(
                id=item.id,
                subject=item.subject,
                exam_time=item.exam_time.isoformat(timespec="minutes"),
                location=item.location,
                exam_method=item.exam_method,
            )
            for item in result.scalars().all()
        ]
    else:
        result = await db.execute(
            select(TeacherProfile).where(TeacherProfile.user_id == db_user.id)
        )
        profile = result.scalars().first()
        if profile:
            teacher_profile = TeacherProfileSchema(
                employee_no=profile.employee_no,
                college_name=profile.college_name,
                title=profile.title,
                office=profile.office,
                research_direction=profile.research_direction,
                campus=profile.campus,
            )

    result = await db.execute(
        select(CourseSchedule)
        .where(CourseSchedule.user_id == db_user.id)
        .order_by(
            CourseSchedule.semester.desc(),
            CourseSchedule.weekday.asc(),
            CourseSchedule.start_time.asc(),
            CourseSchedule.start_section.asc(),
        )
    )
    schedules = [
        CourseScheduleSchema(
            id=item.id,
            semester=item.semester,
            course_code=item.course_code,
            course_name=item.course_name,
            instructor=item.instructor,
            weekday=item.weekday,
            start_section=item.start_section,
            end_section=item.end_section,
            start_time=(
                item.start_time.isoformat(timespec="minutes")
                if item.start_time
                else None
            ),
            end_time=(
                item.end_time.isoformat(timespec="minutes")
                if item.end_time
                else None
            ),
            location=item.location,
            week_range=item.week_range,
            status=item.status,
        )
        for item in result.scalars().all()
    ]

    return MyProfileResponse(
        user_id=str(db_user.id),
        username=db_user.username,
        full_name=db_user.full_name,
        role=db_user.role,
        dept_id=db_user.dept_id,
        student_profile=student_profile,
        teacher_profile=teacher_profile,
        course_schedules=schedules,
        grades=grades,
        exams=exams,
    )


@app.get("/api/v1/notices", response_model=list[NoticeSchema])
async def get_notices(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.user_role not in {ROLE_STUDENT, ROLE_TEACHER}:
        raise HTTPException(status_code=403, detail="Student or teacher login required")

    now = datetime.datetime.now()
    result = await db.execute(
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
    )

    return [
        NoticeSchema(
            id=notice.id,
            title=notice.title,
            content=notice.content,
            dept_id=notice.dept_id,
            audience=notice.audience,
            source=notice.source,
            published_at=notice.published_at.isoformat(),
            expires_at=notice.expires_at.isoformat() if notice.expires_at else None,
        )
        for notice in result.scalars().all()
    ]


@app.post("/api/v1/session/new", response_model=SessionSchema)
async def create_new_session(
    request: CreateSessionRequest,  # 改为接收 JSON Body
    user: UserContext = Depends(get_current_user)
):
    # 权限与类型校验
    if request.type not in VALID_SESSION_TYPES:
        raise HTTPException(status_code=400, detail="Invalid session type")

    # 权限检查：比如 guest 不能创建 internal
    allowed_roles = ROUTE_PERMISSIONS.get(request.type, set())
    if user.user_role not in allowed_roles:
         raise HTTPException(status_code=403, detail=f"Permission denied for {request.type}")

    if user.user_role == ROLE_GUEST:
        pass 
    elif not user.is_authenticated():
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # 传入 type
    session_id = history_manager.create_session(
        user.user_id, 
        session_type=request.type, 
        title="新对话"
    )
    
    return SessionSchema(
        session_id=session_id,
        title="新对话",
        type=request.type,
        created_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

@app.delete("/api/v1/session/{session_id}")
async def delete_session_endpoint(
    session_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    删除指定会话
    """
    # 1. 权限校验：访客或登录用户均可删除自己的会话
    if not user.user_id:
        raise HTTPException(status_code=401, detail="User identity missing")

    # 2. 调用管理器执行删除
    success = history_manager.delete_session(user.user_id, session_id)

    if not success:
        # 如果删除失败，通常意味着 session_id 不存在或者不属于该用户
        raise HTTPException(
            status_code=404, 
            detail="Session not found or permission denied"
        )
    
    return {"message": "Session deleted successfully", "session_id": session_id}

@app.get("/api/v1/session/list", response_model=SessionListResponse)
async def get_session_list(
    type: str = "public",  # 接收 Query 参数 ?type=xxx
    user: UserContext = Depends(get_current_user)
):
    if not user.is_authenticated() and user.user_role != ROLE_GUEST:
        return SessionListResponse(data=[])
    
    # 传入 type 进行筛选
    sessions = history_manager.get_user_sessions(user.user_id, type_filter=type)
    return SessionListResponse(data=sessions)

@app.get("/api/v1/session/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_detail(session_id: str, user: UserContext = Depends(get_current_user)):
    if not user.is_authenticated() and user.user_role != ROLE_GUEST:
        raise HTTPException(status_code=401, detail="Login required")

    if not history_manager.owns_session(user.user_id, session_id):
        raise HTTPException(status_code=404, detail="Session not found or permission denied")
    
    messages = history_manager.get_session_history_detail(session_id)
    return SessionHistoryResponse(session_id=session_id, messages=messages)

# --- 聊天接口 ---
@app.post("/api/v1/chat/{type}")
async def chat_endpoint(
    type: str, 
    payload: RequestPayload, 
    user: UserContext = Depends(get_current_user),
):
    if type not in pipelines:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    
    # 1. 角色权限校验
    allowed_roles = ROUTE_PERMISSIONS.get(type, set())
    if user.user_role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail=f"Access Denied for role: {user.user_role}"
        )

    # 2. 会话绑定校验
    is_valid_session = history_manager.check_session_type(
        user.user_id, 
        payload.session_id, 
        required_type=type
    )
    
    if not is_valid_session:
        # 如果校验失败，可能是 session_id 错误，或者是跨模块调用
        raise HTTPException(
            status_code=400, 
            detail=f"Session {payload.session_id} does not belong to module '{type}' or does not exist."
        )

    # 游客限流
    if user.user_role == ROLE_GUEST:
        limit_key = f"rate_limit:{user.user_id}"
        request_count = redis_client.incr(limit_key)
        if request_count == 1:
            redis_client.expire(limit_key, 60)
        if request_count > 10:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Guest rate limit exceeded. Please wait a moment."
            )

    # 生成标题
    if user.user_id:
        current_history = history_manager.get_recent_turns(payload.session_id)
        if not current_history:
            new_title = payload.query[:15]
            history_manager.update_session_title(user.user_id, payload.session_id, new_title)

    pipeline = pipelines[type]

    async def event_generator():
        try:
            for chunk in pipeline.execute(payload, user):
                data = json.dumps({"chunk": chunk}, ensure_ascii=False)
                yield f"data: {data}\n\n"
            metadata = pipeline.consume_response_metadata()
            meta_data = json.dumps({"metadata": metadata}, ensure_ascii=False)
            yield f"data: {meta_data}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.exception("Chat pipeline failed")
            err_msg = json.dumps(
                {"error": "问答服务暂时不可用，请稍后重试。"},
                ensure_ascii=False,
            )
            yield f"data: {err_msg}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
