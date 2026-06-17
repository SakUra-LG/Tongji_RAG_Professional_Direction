"""Idempotently initialize SQL tables, Milvus collections, and seed exports."""

import asyncio
import time
from pathlib import Path

import redis
from pymilvus import MilvusClient

from app.config import settings
from scripts.import_csv_to_milvus import MilvusImporter
from scripts.init_sql import init_db
from scripts.sync_manual_faqs import sync_manual_faqs


COLLECTIONS = [
    settings.COLLECTION_FAQ,
    settings.COLLECTION_STANDARD,
    settings.COLLECTION_KNOWLEDGE,
    settings.COLLECTION_INTERNAL,
    settings.COLLECTION_PERSONAL,
]


def connect_with_retry(attempts: int = 30, delay_seconds: int = 2) -> MilvusClient:
    uri = f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            client = MilvusClient(uri=uri)
            client.list_collections()
            return client
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"Milvus not ready ({attempt}/{attempts}): {exc}")
            time.sleep(delay_seconds)
    raise RuntimeError(f"Could not connect to Milvus at {uri}") from last_error


def collection_count(client: MilvusClient, collection_name: str) -> int:
    result = client.query(
        collection_name=collection_name,
        filter="",
        output_fields=["count(*)"],
    )
    return int(result[0]["count(*)"])


def ensure_collections(client: MilvusClient) -> None:
    existing = set(client.list_collections())
    for collection_name in COLLECTIONS:
        if collection_name in existing:
            print(f"Milvus collection exists: {collection_name}")
            continue
        client.create_collection(
            collection_name=collection_name,
            dimension=1024,
            metric_type="COSINE",
            auto_id=True,
        )
        print(f"Created Milvus collection: {collection_name}")


def import_empty_exports(client: MilvusClient) -> None:
    export_dir = Path(__file__).parent / "milvus_exports"
    importer = MilvusImporter()
    importer.connect()

    for csv_path in sorted(export_dir.glob("*.csv")):
        collection_name = csv_path.stem
        if collection_name not in COLLECTIONS:
            print(f"Skipping unknown export: {csv_path.name}")
            continue

        count = collection_count(client, collection_name)
        if count > 0:
            print(f"Skipping {collection_name}: already contains {count} records")
            continue

        importer.import_csv_to_collection(csv_path, collection_name)


def print_counts(client: MilvusClient) -> None:
    print("Milvus collection counts:")
    total = 0
    for collection_name in COLLECTIONS:
        count = collection_count(client, collection_name)
        total += count
        print(f"- {collection_name}: {count}")
    print(f"- total: {total}")


def remove_scholar_artifacts(client: MilvusClient, scholar_ids: list[int]) -> None:
    for scholar_id in scholar_ids:
        try:
            client.delete(
                collection_name=settings.COLLECTION_PERSONAL,
                filter=f"user_id == '{scholar_id}'",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Could not clean Milvus personal data for user {scholar_id}: {exc}")

    redis_client = redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )
    for scholar_id in scholar_ids:
        redis_client.delete(f"user_sessions:{scholar_id}")
        for key in redis_client.scan_iter(f"{settings.REDIS_REFRESH_PREFIX}*"):
            if redis_client.get(key) == str(scholar_id):
                redis_client.delete(key)


def main() -> None:
    scholar_ids = sorted(set(asyncio.run(init_db()) + [3]))
    client = connect_with_retry()
    ensure_collections(client)
    import_empty_exports(client)
    sync_manual_faqs(client)
    remove_scholar_artifacts(client, scholar_ids)
    print_counts(client)


if __name__ == "__main__":
    main()
