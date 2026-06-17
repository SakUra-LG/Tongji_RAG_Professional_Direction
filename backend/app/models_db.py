from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(100), nullable=False)
    full_name = Column(String(50))
    role = Column(String(20), default="student")  # student, teacher
    dept_id = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True)


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    student_no = Column(String(30), unique=True, nullable=True)
    college_name = Column(String(100), nullable=True)
    major = Column(String(100), nullable=True)
    grade_year = Column(Integer, nullable=True)
    class_name = Column(String(100), nullable=True)
    gpa = Column(Numeric(4, 2), nullable=True)
    major_rank = Column(Integer, nullable=True)
    earned_credits = Column(Numeric(6, 2), nullable=True)
    campus = Column(String(100), nullable=True)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TeacherProfile(Base):
    __tablename__ = "teacher_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    employee_no = Column(String(30), unique=True, nullable=True)
    college_name = Column(String(100), nullable=True)
    title = Column(String(100), nullable=True)
    office = Column(String(100), nullable=True)
    research_direction = Column(String(255), nullable=True)
    campus = Column(String(100), nullable=True)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CourseSchedule(Base):
    __tablename__ = "course_schedules"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "semester",
            "course_name",
            "weekday",
            "start_time",
            name="uq_user_semester_course_time",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    semester = Column(String(30), nullable=True)
    course_code = Column(String(30), nullable=True)
    course_name = Column(String(150), nullable=False)
    instructor = Column(String(100), nullable=True)
    weekday = Column(Integer, nullable=True)
    start_section = Column(Integer, nullable=True)
    end_section = Column(Integer, nullable=True)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    location = Column(String(150), nullable=True)
    week_range = Column(String(100), nullable=True)
    status = Column(String(20), default="selected", nullable=False)


class StudentGrade(Base):
    __tablename__ = "student_grades"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "semester",
            "course_name",
            name="uq_user_semester_grade",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    semester = Column(String(30), nullable=True)
    course_code = Column(String(30), nullable=True)
    course_name = Column(String(150), nullable=False)
    score = Column(Numeric(5, 2), nullable=True)
    grade_point = Column(Numeric(4, 2), nullable=True)
    credits = Column(Numeric(5, 2), nullable=True)


class CampusNotice(Base):
    __tablename__ = "campus_notices"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False, index=True)
    content = Column(Text, nullable=False)
    dept_id = Column(String(20), nullable=True, index=True)
    audience = Column(String(20), default="all", nullable=False, index=True)
    source = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    published_at = Column(DateTime, server_default=func.now(), nullable=False)
    expires_at = Column(DateTime, nullable=True)


class CrawlTask(Base):
    """爬取任务元数据表"""
    __tablename__ = "crawl_tasks"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(500), nullable=False, index=True)
    collection_name = Column(String(50), nullable=False)  # rag_standard, rag_knowledge等
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    pages_crawled = Column(Integer, default=0)
    blocks_inserted = Column(Integer, default=0)  # 插入的文本块/语义块数量
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class CrawlBlock(Base):
    """爬取的文本块元数据表（可选，用于追踪和管理）"""
    __tablename__ = "crawl_blocks"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, nullable=False, index=True)  # 关联到 crawl_tasks.id
    url = Column(String(500), nullable=False, index=True)
    title = Column(String(200), nullable=True)  # 语义块标题
    section = Column(String(50), nullable=True)  # 语义块分类（时间信息、位置信息等）
    collection_name = Column(String(50), nullable=True, index=True)
    access_scope = Column(String(20), nullable=True, index=True)  # public / campus
    text_preview = Column(String(500), nullable=True)  # 文本预览（前500字符）
    text_content = Column(Text, nullable=True)
    milvus_id = Column(String(100), nullable=True, index=True)  # Milvus中的ID（如果可获取）
    created_at = Column(DateTime, server_default=func.now())


class ManagedFAQ(Base):
    """FAQ entries editable from the admin console."""
    __tablename__ = "managed_faqs"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(String(500), nullable=False, unique=True, index=True)
    answer = Column(Text, nullable=False)
    source = Column(String(200), nullable=True)
    aliases = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
