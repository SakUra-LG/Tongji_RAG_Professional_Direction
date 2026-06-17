"""Crawl the requested Tongji sources and synchronize them into Milvus."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

import chardet
from bs4 import BeautifulSoup
from pymilvus import MilvusClient

from app.config import settings


VECTOR_DIMENSION = 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class CrawlSource:
    name: str
    start_url: str
    collection: str
    max_pages: int
    source_prefix: str


SOURCES = (
    CrawlSource(
        name="同济大学百度百科",
        start_url=(
            "https://baike.baidu.com/item/"
            "%E5%90%8C%E6%B5%8E%E5%A4%A7%E5%AD%A6/133590"
            "?fromModule=lemma_search-box"
        ),
        collection=settings.COLLECTION_STANDARD,
        max_pages=1,
        source_prefix="百度百科-同济大学",
    ),
    CrawlSource(
        name="同济大学官网新闻",
        start_url="https://www.tongji.edu.cn/",
        collection=settings.COLLECTION_STANDARD,
        max_pages=25,
        source_prefix="同济大学官网",
    ),
    CrawlSource(
        name="同济大学计算机学院",
        start_url="https://cs.tongji.edu.cn/index.htm",
        collection=settings.COLLECTION_KNOWLEDGE,
        max_pages=25,
        source_prefix="计算机学院官网",
    ),
)

EXTRA_START_URLS = {
    "同济大学计算机学院": [
        "https://cs.tongji.edu.cn/xygk/lsyg.htm",
    ],
}


def fetch_with_curl(url: str) -> tuple[bytes, str]:
    command = [
        "curl",
        "-L",
        "--http1.1",
        "-k",
        "-A",
        USER_AGENT,
        "--connect-timeout",
        "15",
        "--max-time",
        "45",
        "-sS",
        "-w",
        "\n__FINAL_URL__:%{url_effective}",
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=55,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        proxy = os.getenv("CRAWL_FETCH_PROXY", "").rstrip("/")
        if not proxy:
            raise
        request = Request(
            f"{proxy}/fetch?url={url}",
            headers={"User-Agent": USER_AGENT},
        )
        with urlopen(request, timeout=60) as response:
            return response.read(), response.headers.get("X-Final-Url", url)

    marker = b"\n__FINAL_URL__:"
    body, separator, final_url = completed.stdout.rpartition(marker)
    if not separator:
        return completed.stdout, url
    return body, final_url.decode("utf-8", errors="replace").strip()


def decode_html(content: bytes) -> str:
    detected = chardet.detect(content).get("encoding") or "utf-8"
    return content.decode(detected, errors="replace")


def clean_text(html: str) -> tuple[str, str, list[str]]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(
        ["script", "style", "noscript", "svg", "nav", "footer", "form"]
    ):
        tag.decompose()

    main = (
        soup.select_one("main")
        or soup.select_one("article")
        or soup.select_one(".content")
        or soup.select_one(".article")
        or soup.body
        or soup
    )
    text = main.get_text("\n", strip=True)
    lines = []
    for line in text.splitlines():
        normalized = re.sub(r"\s+", " ", line).strip()
        if len(normalized) >= 2 and normalized not in lines[-3:]:
            lines.append(normalized)
    links = [anchor.get("href", "") for anchor in soup.find_all("a", href=True)]
    return title, "\n".join(lines), links


def split_text(title: str, text: str, chunk_size: int = 850) -> list[str]:
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    chunks: list[str] = []
    current = title.strip()
    for paragraph in paragraphs:
        candidate = f"{current}\n{paragraph}".strip()
        if current and len(candidate) > chunk_size:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if len(chunk) >= 40]


def normalize_link(base_url: str, href: str, allowed_host: str) -> str | None:
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    absolute = urldefrag(urljoin(base_url, href))[0]
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != allowed_host:
        return None
    if re.search(r"\.(?:jpg|jpeg|png|gif|svg|pdf|docx?|xlsx?|zip|rar)$", parsed.path, re.I):
        return None
    return absolute


def link_priority(url: str) -> tuple[int, str]:
    lowered = url.lower()
    news_tokens = ("news", "xw", "info", "notice", "tzgg", "zhxw", "xyxw")
    return (0 if any(token in lowered for token in news_tokens) else 1, url)


def looks_like_article_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return bool(
        re.search(r"/info/\d+/\d+\.htm", path)
        or re.search(r"\d{4,}.*\.htm$", path)
        or any(token in path for token in ("/news/", "/xw", "/tzgg", "/notice"))
    )


def crawl(source: CrawlSource) -> list[dict[str, str]]:
    queue = deque(
        [source.start_url, *EXTRA_START_URLS.get(source.name, [])]
    )
    visited: set[str] = set()
    pages: list[dict[str, str]] = []
    allowed_host = urlparse(source.start_url).netloc

    while queue and len(pages) < source.max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        try:
            content, final_url = fetch_with_curl(url)
            title, text, links = clean_text(decode_html(content))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {source.name} fetch failed: {url}: {exc}")
            continue

        candidates = {
            normalized
            for href in links
            if (normalized := normalize_link(final_url, href, allowed_host))
            and normalized not in visited
        }
        is_start_page = url == source.start_url
        if (
            len(text) < 80
            or "error.html" in final_url
            or (is_start_page and candidates and not looks_like_article_url(final_url))
        ):
            print(f"[SKIP] {source.name}: using article links from: {final_url}")
        else:
            pages.append({"url": final_url, "title": title, "text": text})
            print(f"[OK] {source.name}: {len(text)} chars: {final_url}")

        queue.extend(sorted(candidates, key=link_priority))
        time.sleep(0.2)

    return pages


def placeholder_vector(text: str) -> list[float]:
    """Return a neutral placeholder; keyword retrieval remains authoritative."""
    return [0.0] * VECTOR_DIMENSION


def remove_previous_rows(
    client: MilvusClient,
    collection: str,
    source_prefix: str,
) -> int:
    rows = client.query(
        collection_name=collection,
        filter="",
        output_fields=["id", "source"],
        limit=10000,
    )
    ids = [
        int(row["id"])
        for row in rows
        if str(row.get("source", "")).startswith(source_prefix)
    ]
    if ids:
        client.delete(
            collection_name=collection,
            filter=f"id in {ids}",
        )
    return len(ids)


def synchronize_source(
    client: MilvusClient,
    source: CrawlSource,
    pages: list[dict[str, str]],
) -> int:
    rows = []
    seen_chunks: set[str] = set()
    for page in pages:
        for chunk in split_text(page["title"], page["text"]):
            signature = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            if signature in seen_chunks:
                continue
            seen_chunks.add(signature)
            rows.append(
                {
                    "vector": placeholder_vector(chunk),
                    "text": chunk,
                    "source": f"{source.source_prefix} | {page['url']}",
                    "dept_id": "",
                    "user_id": "",
                }
            )

    removed = remove_previous_rows(
        client,
        source.collection,
        source.source_prefix,
    )
    if rows:
        client.insert(collection_name=source.collection, data=rows)
        client.flush(collection_name=source.collection)
    print(
        f"[SYNC] {source.name}: removed {removed}, inserted {len(rows)} "
        f"into {source.collection}"
    )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--milvus-host", default=settings.MILVUS_HOST)
    parser.add_argument("--milvus-port", default=settings.MILVUS_PORT)
    parser.add_argument(
        "--source",
        action="append",
        help="Only crawl a named source; may be provided more than once.",
    )
    args = parser.parse_args()

    client = MilvusClient(
        uri=f"http://{args.milvus_host}:{args.milvus_port}"
    )
    total = 0
    selected_sources = (
        [source for source in SOURCES if source.name in set(args.source)]
        if args.source
        else SOURCES
    )
    for source in selected_sources:
        pages = crawl(source)
        total += synchronize_source(client, source, pages)
    print(f"Requested source crawl complete: {total} chunks inserted")


if __name__ == "__main__":
    main()
