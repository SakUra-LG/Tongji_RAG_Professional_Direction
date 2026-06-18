import asyncio
import os
import sys
from datetime import datetime, time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from passlib.context import CryptContext
from sqlalchemy import delete, inspect, or_, select, text

from app.database import AsyncSessionLocal, Base, engine
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
from app.manual_faqs import MANUAL_FAQS

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_hash(password: str) -> str:
    return pwd_context.hash(password)


def upgrade_course_schedule_schema(sync_conn) -> None:
    inspector = inspect(sync_conn)
    if "course_schedules" not in inspector.get_table_names():
        return

    columns = {
        column["name"] for column in inspector.get_columns("course_schedules")
    }
    if "start_time" not in columns:
        sync_conn.execute(
            text("ALTER TABLE course_schedules ADD COLUMN start_time TIME NULL")
        )
    if "end_time" not in columns:
        sync_conn.execute(
            text("ALTER TABLE course_schedules ADD COLUMN end_time TIME NULL")
        )

    inspector = inspect(sync_conn)
    unique_constraints = {
        item["name"] for item in inspector.get_unique_constraints("course_schedules")
    }
    if "uq_user_semester_course" in unique_constraints:
        sync_conn.execute(
            text(
                "ALTER TABLE course_schedules "
                "DROP INDEX uq_user_semester_course"
            )
        )
    if "uq_user_semester_course_time" not in unique_constraints:
        sync_conn.execute(
            text(
                "ALTER TABLE course_schedules ADD CONSTRAINT "
                "uq_user_semester_course_time UNIQUE "
                "(user_id, semester, course_name, weekday, start_time)"
            )
        )


def upgrade_crawl_block_schema(sync_conn) -> None:
    inspector = inspect(sync_conn)
    if "crawl_blocks" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("crawl_blocks")}
    additions = {
        "collection_name": "ALTER TABLE crawl_blocks ADD COLUMN collection_name VARCHAR(50) NULL",
        "access_scope": "ALTER TABLE crawl_blocks ADD COLUMN access_scope VARCHAR(20) NULL",
        "text_content": "ALTER TABLE crawl_blocks ADD COLUMN text_content TEXT NULL",
    }
    for column_name, sql in additions.items():
        if column_name not in columns:
            sync_conn.execute(text(sql))


async def ensure_user(
    session,
    *,
    username: str,
    password: str,
    full_name: str,
    role: str,
    dept_id: str,
) -> User:
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    if user is None:
        user = User(
            username=username,
            hashed_password=get_hash(password),
            full_name=full_name,
            role=role,
            dept_id=dept_id,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        return user

    user.full_name = full_name
    user.role = role
    user.dept_id = dept_id
    user.is_active = True
    return user


async def reset_all_passwords(session, password: str) -> int:
    result = await session.execute(select(User))
    users = list(result.scalars().all())
    shared_hash = get_hash(password)
    for user in users:
        user.hashed_password = shared_hash
    return len(users)


async def seed_profiles(
    session,
    student: User,
    teacher: User,
    lee: User,
) -> None:
    result = await session.execute(
        select(StudentProfile).where(StudentProfile.user_id == student.id)
    )
    if result.scalars().first() is None:
        session.add(
            StudentProfile(
                user_id=student.id,
                college_name="计算机系",
                gpa=3.85,
                major_rank=5,
            )
        )

    result = await session.execute(
        select(StudentProfile).where(StudentProfile.user_id == lee.id)
    )
    lee_profile = result.scalars().first()
    if lee_profile is None:
        lee_profile = StudentProfile(user_id=lee.id)
        session.add(lee_profile)
    lee_profile.college_name = "计算机系"
    lee_profile.gpa = 4.50

    result = await session.execute(
        select(TeacherProfile).where(TeacherProfile.user_id == teacher.id)
    )
    if result.scalars().first() is None:
        session.add(
            TeacherProfile(
                user_id=teacher.id,
                college_name="软件学院",
                title="教授",
            )
        )

    for course_name in ("高级机器学习", "云计算"):
        result = await session.execute(
            select(CourseSchedule).where(
                CourseSchedule.user_id == student.id,
                CourseSchedule.course_name == course_name,
            )
        )
        if result.scalars().first() is None:
            session.add(
                CourseSchedule(
                    user_id=student.id,
                    course_name=course_name,
                    status="selected",
                )
            )


async def seed_lee_schedule(session, lee: User) -> None:
    semester = "2025-2026-1"
    schedule_items = [
        ("商务智能案例分析", 1, time(10, 0), time(11, 35)),
        ("软件工程管理与经济", 1, time(13, 30), time(16, 15)),
        ("星期音乐会", 1, time(18, 30), time(20, 5)),
        ("软件测试", 2, time(10, 0), time(11, 35)),
        ("体育（6）", 3, time(10, 0), time(11, 35)),
        ("数据分析与数据挖掘", 3, time(13, 30), time(15, 5)),
        ("软件测试", 4, time(10, 0), time(11, 35)),
        (".NET体系结构与设计开发", 4, time(13, 30), time(16, 15)),
        ("专业方向综合项目", 4, time(18, 30), time(20, 5)),
    ]

    for course_name, weekday, start_time, end_time in schedule_items:
        result = await session.execute(
            select(CourseSchedule).where(
                CourseSchedule.user_id == lee.id,
                CourseSchedule.semester == semester,
                CourseSchedule.course_name == course_name,
                CourseSchedule.weekday == weekday,
                CourseSchedule.start_time == start_time,
            )
        )
        schedule = result.scalars().first()
        if schedule is None:
            schedule = CourseSchedule(
                user_id=lee.id,
                semester=semester,
                course_name=course_name,
                weekday=weekday,
                start_time=start_time,
            )
            session.add(schedule)
        schedule.end_time = end_time
        schedule.status = "selected"


async def seed_lee_exams(session, lee: User) -> None:
    exam_items = [
        ("软件测试", datetime(2026, 6, 18, 10, 0), "安楼A210", "考查"),
        ("数据分析与数据挖掘", datetime(2026, 6, 17, 13, 30), "广楼G107", "考查"),
        ("软件工程管理与经济", datetime(2026, 6, 9, 18, 30), None, "考试"),
    ]

    for subject, exam_time, location, exam_method in exam_items:
        result = await session.execute(
            select(StudentExam).where(
                StudentExam.user_id == lee.id,
                StudentExam.subject == subject,
                StudentExam.exam_time == exam_time,
            )
        )
        exam = result.scalars().first()
        if exam is None:
            exam = StudentExam(
                user_id=lee.id,
                subject=subject,
                exam_time=exam_time,
            )
            session.add(exam)
        exam.location = location
        exam.exam_method = exam_method


async def seed_notices(session) -> None:
    notices = [
        {
            "title": "计算机系大模型培训通知",
            "content": "计算机系通知：本周五下午召开全体教职工大模型培训。",
            "dept_id": "CS",
            "audience": "teacher",
            "source": "院系通知",
        },
        {
            "title": "2025届毕业设计答辩公告",
            "content": "软件学院公告：2025届毕业设计答辩将在嘉定校区举行。",
            "dept_id": "SE",
            "audience": "all",
            "source": "软件学院",
        },
    ]
    for notice_data in notices:
        result = await session.execute(
            select(CampusNotice).where(CampusNotice.title == notice_data["title"])
        )
        if result.scalars().first() is None:
            session.add(CampusNotice(**notice_data))


async def seed_managed_faqs(session) -> None:
    for item in MANUAL_FAQS:
        result = await session.execute(
            select(ManagedFAQ).where(ManagedFAQ.question == item["q"])
        )
        faq = result.scalars().first()
        aliases = "\n".join(item.get("aliases", []))
        if faq is None:
            session.add(
                ManagedFAQ(
                    question=item["q"],
                    answer=item["a"],
                    source=item.get("source", "FAQ"),
                    aliases=aliases,
                    is_active=True,
                )
            )
        else:
            faq.answer = item["a"]
            faq.source = item.get("source", "FAQ")
            faq.aliases = aliases
            faq.is_active = True


async def remove_scholar_data(session) -> list[int]:
    result = await session.execute(
        select(User).where(
            or_(User.role == "scholar", User.username == "dr_wang")
        )
    )
    scholars = list(result.scalars().all())
    scholar_ids = [user.id for user in scholars]

    if scholar_ids:
        await session.execute(
            delete(CourseSchedule).where(CourseSchedule.user_id.in_(scholar_ids))
        )
        await session.execute(
            delete(StudentGrade).where(StudentGrade.user_id.in_(scholar_ids))
        )
        await session.execute(
            delete(StudentExam).where(StudentExam.user_id.in_(scholar_ids))
        )
        await session.execute(
            delete(StudentProfile).where(StudentProfile.user_id.in_(scholar_ids))
        )
        await session.execute(
            delete(TeacherProfile).where(TeacherProfile.user_id.in_(scholar_ids))
        )
        for scholar in scholars:
            await session.delete(scholar)

    return scholar_ids


async def init_db() -> list[int]:
    print("--- Initializing MySQL Database ---")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(upgrade_course_schedule_schema)
            await conn.run_sync(upgrade_crawl_block_schema)
        print("Tables created or already present.")

        async with AsyncSessionLocal() as session:
            removed_scholar_ids = await remove_scholar_data(session)

            student = await ensure_user(
                session,
                username="zhangsan",
                password="123456",
                full_name="张三",
                role="student",
                dept_id="CS",
            )
            teacher = await ensure_user(
                session,
                username="prof_li",
                password="123456",
                full_name="李教授",
                role="teacher",
                dept_id="SE",
            )
            lee = await ensure_user(
                session,
                username="Lee",
                password="123456",
                full_name="Lee",
                role="student",
                dept_id="CS",
            )
            admin = await ensure_user(
                session,
                username="admin",
                password="123456",
                full_name="系统管理员",
                role="admin",
                dept_id="ADMIN",
            )

            await seed_profiles(session, student, teacher, lee)
            await seed_lee_schedule(session, lee)
            await seed_lee_exams(session, lee)
            await seed_notices(session)
            await seed_managed_faqs(session)
            reset_count = await reset_all_passwords(session, "123456")
            await session.commit()

            if removed_scholar_ids:
                print(f"Removed scholar users: {removed_scholar_ids}")
            print(
                "Student, teacher, admin, profile, course, notice, and FAQ data are ready. "
                f"Reset {reset_count} user passwords."
            )
            return removed_scholar_ids
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_db())
