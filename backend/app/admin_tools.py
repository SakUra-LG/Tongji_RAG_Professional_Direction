from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import chardet
import requests
from bs4 import BeautifulSoup
from pymilvus import MilvusClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models_db import CrawlBlock, CrawlTask


VECTOR_DIMENSION = 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class PreviewBlock:
    title: str
    section: str
    url: str
    text: str


def placeholder_vector(_: str) -> list[float]:
    return [0.0] * VECTOR_DIMENSION


def sync_session_factory():
    database_url = (
        f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
        f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}"
        "?charset=utf8mb4"
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def fetch_html(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=(12, 35),
        verify=False,
    )
    response.raise_for_status()
    encoding = response.encoding
    if not encoding or encoding.lower() in {"iso-8859-1", "windows-1252"}:
        encoding = chardet.detect(response.content).get("encoding") or "utf-8"
    return response.content.decode(encoding, errors="replace")


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


def looks_like_article(url: str, text: str = "") -> bool:
    lowered = url.lower()
    path = urlparse(url).path.lower()
    article_patterns = (
        r"/info/\d+/\d+\.htm",
        r"/\d{4,}/.*\.htm",
        r"/(?:xw|news|tzgg|notice|zhxw|xyxw|jxky)/",
    )
    if any(re.search(pattern, path) for pattern in article_patterns):
        return True
    if re.search(r"\d{4,}.*\.htm$", path):
        return True
    return bool(text and len(text.strip()) >= 8 and any(token in lowered for token in ("info", "news", "xw", "tz", "gg")))


def extract_article_links(index_url: str, html: str, max_links: int) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    allowed_host = urlparse(index_url).netloc
    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        absolute = normalize_link(index_url, anchor.get("href", ""), allowed_host)
        if not absolute or absolute in seen:
            continue
        anchor_text = anchor.get_text(" ", strip=True)
        if looks_like_article(absolute, anchor_text):
            links.append(absolute)
            seen.add(absolute)
        if len(links) >= max_links:
            break
    return links


def clean_article(html: str, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "form", "iframe"]):
        tag.decompose()
    title = ""
    for selector in ("h1", ".title", ".article-title", "title"):
        node = soup.select_one(selector)
        if node:
            title = node.get_text(" ", strip=True)
            break
    main = (
        soup.select_one("article")
        or soup.select_one("main")
        or soup.select_one(".v_news_content")
        or soup.select_one(".article")
        or soup.select_one(".content")
        or soup.select_one(".main")
        or soup.body
        or soup
    )
    raw_lines = main.get_text("\n", strip=True).splitlines()
    lines: list[str] = []
    for line in raw_lines:
        normalized = re.sub(r"\s+", " ", line).strip()
        if len(normalized) >= 2 and normalized not in lines[-5:]:
            lines.append(normalized)
    text = "\n".join(lines)
    if not title:
        title = urlparse(url).path.rsplit("/", 1)[-1] or "未命名文章"
    return title[:200], text


def split_text(title: str, text: str, chunk_size: int = 900) -> list[str]:
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


def classify_section(title: str, url: str) -> str:
    combined = f"{title} {url}".lower()
    if any(word in combined for word in ("通知", "公告", "notice", "tzgg")):
        return "通知公告"
    if any(word in combined for word in ("新闻", "news", "xw")):
        return "新闻动态"
    if any(word in combined for word in ("科研", "学术", "research")):
        return "学术科研"
    if any(word in combined for word in ("学院", "专业", "培养")):
        return "学院专业"
    return "综合资料"


def crawl_preview(start_url: str, max_pages: int = 8) -> list[dict[str, Any]]:
    index_html = fetch_html(start_url)
    article_urls = extract_article_links(start_url, index_html, max_pages)
    urls = article_urls or [start_url]
    blocks: list[PreviewBlock] = []
    seen_chunks: set[str] = set()

    for url in urls[:max_pages]:
        try:
            html = fetch_html(url)
            title, text = clean_article(html, url)
        except Exception:
            continue
        if len(text) < 80:
            continue
        section = classify_section(title, url)
        for chunk in split_text(title, text):
            signature = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            if signature in seen_chunks:
                continue
            seen_chunks.add(signature)
            blocks.append(PreviewBlock(title=title, section=section, url=url, text=chunk))
        time.sleep(0.2)

    return [
        {"title": item.title, "section": item.section, "url": item.url, "text": item.text}
        for item in blocks
    ]


def save_blocks_to_knowledge_base(
    *,
    start_url: str,
    access_scope: str,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    collection = (
        settings.COLLECTION_STANDARD
        if access_scope == "public"
        else settings.COLLECTION_KNOWLEDGE
    )
    task_url = f"admin-crawl://{start_url}"
    session_factory = sync_session_factory()
    client = MilvusClient(uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}")
    rows: list[dict[str, Any]] = []
    crawl_blocks: list[CrawlBlock] = []

    with session_factory() as db:
        task = CrawlTask(
            url=task_url,
            collection_name=collection,
            status="running",
            pages_crawled=len({block.get("url") for block in blocks}),
            blocks_inserted=0,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        for block in blocks:
            text = (block.get("text") or "").strip()
            if not text:
                continue
            title = (block.get("title") or "未命名文章").strip()
            url = (block.get("url") or start_url).strip()
            section = (block.get("section") or classify_section(title, url)).strip()
            source = f"{title} | {url}"
            rows.append(
                {
                    "vector": placeholder_vector(text),
                    "text": text,
                    "source": source,
                    "dept_id": "",
                    "user_id": "",
                }
            )
            crawl_block = CrawlBlock(
                task_id=task.id,
                url=url,
                title=title[:200],
                section=section[:50],
                collection_name=collection,
                access_scope=access_scope,
                text_preview=text[:500],
                text_content=text,
            )
            db.add(crawl_block)
            crawl_blocks.append(crawl_block)

        db.commit()
        if rows:
            result = client.insert(collection_name=collection, data=rows)
            ids = result.get("ids") if isinstance(result, dict) else []
            for crawl_block, milvus_id in zip(crawl_blocks, ids or []):
                crawl_block.milvus_id = str(milvus_id)
            client.flush(collection_name=collection)

        task.status = "completed"
        task.blocks_inserted = len(rows)
        db.commit()

        return {
            "task_id": task.id,
            "collection_name": collection,
            "inserted": len(rows),
        }
