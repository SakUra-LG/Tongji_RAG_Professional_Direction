import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymilvus import MilvusClient
from app.config import settings


def reset_milvus_collections():
    client = MilvusClient(uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}")

    collections = [
        settings.COLLECTION_FAQ,
        settings.COLLECTION_STANDARD,
        settings.COLLECTION_KNOWLEDGE,
        settings.COLLECTION_INTERNAL,
        settings.COLLECTION_PERSONAL,
    ]

    existing = set(client.list_collections())
    print(f"Existing collections: {sorted(existing)}")

    for collection_name in collections:
        if collection_name in existing:
            client.drop_collection(collection_name)
            print(f"Dropped collection: {collection_name}")

        client.create_collection(
            collection_name=collection_name,
            dimension=1024,
            metric_type="COSINE",
            auto_id=True,
        )
        print(f"Created collection: {collection_name}")

    print("Milvus collections are ready.")


if __name__ == "__main__":
    reset_milvus_collections()
