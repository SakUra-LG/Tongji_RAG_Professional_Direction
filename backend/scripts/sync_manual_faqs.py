"""Synchronize curated local FAQ entries into Milvus without duplicates."""

from typing import Any

from langchain_community.embeddings import DashScopeEmbeddings
from pymilvus import MilvusClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.manual_faqs import MANUAL_FAQS
from app.models_db import ManagedFAQ


VECTOR_DIMENSION = 1024


def _normalize_question(question: str) -> str:
    return "".join(character.lower() for character in question if character.isalnum())


def _load_existing(client: MilvusClient) -> list[dict[str, Any]]:
    return client.query(
        collection_name=settings.COLLECTION_FAQ,
        filter="",
        output_fields=["id", "vector", "question", "answer", "source"],
        limit=1000,
    )


def _load_curated_faqs() -> list[dict[str, Any]]:
    database_url = (
        f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
        f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}"
        "?charset=utf8mb4"
    )
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with Session(engine) as session:
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
                }
                for row in rows
            ]
    except Exception as exc:  # noqa: BLE001
        print(f"Managed FAQ load failed, using local fallback: {exc}")
    return MANUAL_FAQS


def sync_manual_faqs(client: MilvusClient) -> int:
    existing_rows = _load_existing(client)
    existing_by_question: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        question = row.get("question", "").strip()
        if question:
            existing_by_question.setdefault(_normalize_question(question), row)

    curated_by_question = {
        _normalize_question(item["q"]): item for item in _load_curated_faqs()
    }
    already_current = (
        len(existing_rows) == len(curated_by_question)
        and set(existing_by_question) == set(curated_by_question)
        and all(
            key in existing_by_question
            and existing_by_question[key].get("answer") == item["a"]
            and existing_by_question[key].get("source") == item["source"]
            for key, item in curated_by_question.items()
        )
    )
    if already_current:
        print(f"Manual FAQ collection already current: {len(existing_rows)} records")
        return len(existing_rows)

    missing_questions = [
        item["q"]
        for key, item in curated_by_question.items()
        if key not in existing_by_question
    ]
    generated_vectors: dict[str, list[float]] = {}
    if missing_questions:
        try:
            embedder = DashScopeEmbeddings(
                model=settings.EMBEDDING_MODEL,
                dashscope_api_key=settings.DASHSCOPE_API_KEY,
            )
            vectors = embedder.embed_documents(missing_questions)
            generated_vectors = dict(zip(missing_questions, vectors))
            print(f"Generated embeddings for {len(missing_questions)} new FAQ entries")
        except Exception as exc:  # noqa: BLE001
            print(
                "FAQ embedding service unavailable; using placeholder vectors. "
                f"Local FAQ matching remains fully available. Error: {exc}"
            )

    final_rows: list[dict[str, Any]] = []
    for key, item in curated_by_question.items():
        existing = existing_by_question.get(key)
        vector = (
            existing.get("vector")
            if existing and existing.get("vector")
            else generated_vectors.get(item["q"])
        )
        final_rows.append(
            {
                "vector": vector or [0.0] * VECTOR_DIMENSION,
                "question": item["q"],
                "answer": item["a"],
                "source": item["source"],
            }
        )

    if existing_rows:
        client.delete(
            collection_name=settings.COLLECTION_FAQ,
            filter="id >= 0",
        )
    if final_rows:
        client.insert(
            collection_name=settings.COLLECTION_FAQ,
            data=final_rows,
        )
        client.flush(collection_name=settings.COLLECTION_FAQ)

    print(
        f"Synchronized FAQ collection: {len(existing_rows)} -> "
        f"{len(final_rows)} records"
    )
    return len(final_rows)


def main() -> None:
    client = MilvusClient(
        uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
    )
    sync_manual_faqs(client)


if __name__ == "__main__":
    main()
