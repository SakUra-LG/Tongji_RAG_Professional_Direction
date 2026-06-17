import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymilvus import MilvusClient
from app.config import settings


def main():
    uri = f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
    client = MilvusClient(uri=uri)
    collections = [
        settings.COLLECTION_FAQ,
        settings.COLLECTION_STANDARD,
        settings.COLLECTION_KNOWLEDGE,
        settings.COLLECTION_INTERNAL,
        settings.COLLECTION_PERSONAL,
    ]

    existing = set(client.list_collections())
    print("Milvus collection counts:")
    for collection_name in collections:
        if collection_name not in existing:
            print(f"- {collection_name}: missing")
            continue
        result = client.query(
            collection_name=collection_name,
            filter="",
            output_fields=["count(*)"],
        )
        row_count = int(result[0]["count(*)"])
        print(f"- {collection_name}: {row_count}")


if __name__ == "__main__":
    main()
