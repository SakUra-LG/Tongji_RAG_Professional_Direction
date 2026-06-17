"""
查看当前 Milvus 中所有集合的记录数量。

用法示例：

    # 使用配置中的默认地址（一般是 localhost:19530）
    python show_milvus_counts.py

    # 显式指定 Milvus 地址
    python show_milvus_counts.py --host localhost --port 19530
"""

import sys
import argparse
from pathlib import Path

from pymilvus import MilvusClient

# 保证能找到 app 模块（与 crawler.py、import_csv_to_milvus.py 保持一致）
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.config import settings  # noqa: E402


def connect_milvus(host: str, port: str) -> MilvusClient:
    uri = f"http://{host}:{port}"
    print("=" * 80)
    print(f"🔌 正在连接 Milvus: {uri}")
    print("=" * 80)
    client = MilvusClient(uri=uri)
    cols = client.list_collections()
    print(f"✅ 连接成功，当前共有 {len(cols)} 个集合：{cols}")
    print()
    return client


def show_counts(client: MilvusClient) -> None:
    collections = client.list_collections()
    if not collections:
        print("⚠️ 当前没有任何集合")
        return

    print("=" * 80)
    print(" 各集合记录数量统计：")
    print("=" * 80)
    print(f"{'集合名':40} | {'记录数':>10}")
    print("-" * 80)

    total = 0
    for name in collections:
        try:
            result = client.query(
                collection_name=name,
                filter="",
                output_fields=["count(*)"],
            )
            row_count = int(result[0]["count(*)"])
        except Exception as e:  # noqa: BLE001
            print(f"{name:40} | 读取失败: {e}")
            continue

        total += row_count
        print(f"{name:40} | {row_count:10d}")

    print("-" * 80)
    print(f"{'合计':40} | {total:10d}")
    print("=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="查看 Milvus 中每个集合包含的记录数量",
    )

    parser.add_argument(
        "--host",
        type=str,
        default=settings.MILVUS_HOST,
        help=f"Milvus 主机地址（默认: {settings.MILVUS_HOST}）",
    )
    parser.add_argument(
        "--port",
        type=str,
        default=settings.MILVUS_PORT,
        help=f"Milvus 端口（默认: {settings.MILVUS_PORT}）",
    )

    args = parser.parse_args()

    client = connect_milvus(args.host, args.port)
    show_counts(client)


if __name__ == "__main__":
    main()


