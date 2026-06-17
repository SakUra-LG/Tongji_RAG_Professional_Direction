"""
同济大学官网数据爬虫脚本
功能：爬取学校官网等公开信息，处理后存储到 Milvus 向量数据库
"""

import sys
import os
import re
import time
import ssl
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import urllib3

# 确保能找到 app 模块（从 crawler 子文件夹向上两级到 backend）
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pymilvus import MilvusClient
from langchain_community.embeddings import DashScopeEmbeddings
from app.config import settings
from app.models_db import CrawlTask, CrawlBlock


# 创建同步数据库引擎和会话（用于脚本）
def get_sync_db_session() -> Session:
    """获取同步数据库会话（用于爬虫脚本）"""
    sync_db_url = (
        f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
        f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}"
    )
    sync_engine = create_engine(sync_db_url, echo=False, pool_pre_ping=True)
    SyncSessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)
    return SyncSessionLocal()


class CustomHTTPAdapter(HTTPAdapter):
    """自定义HTTP适配器，配置SSL上下文"""
    
    def init_poolmanager(self, *args, **kwargs):
        # 创建SSL上下文，支持TLS 1.2和1.3
        try:
            ctx = ssl.create_default_context()
            # 降低安全级别以兼容更多服务器
            try:
                ctx.set_ciphers('DEFAULT@SECLEVEL=1')
            except ssl.SSLError:
                # 如果设置失败，使用默认配置
                pass
            
            # 从kwargs中获取cert_reqs，如果没有则使用默认值
            cert_reqs = kwargs.pop('cert_reqs', ssl.CERT_REQUIRED)
            ctx.check_hostname = (cert_reqs != ssl.CERT_NONE)
            ctx.verify_mode = cert_reqs
            
            kwargs['ssl_context'] = ctx
        except Exception as e:
            # 如果SSL上下文创建失败，使用默认配置
            print(f"Warning: Failed to create custom SSL context: {e}")
        
        return super().init_poolmanager(*args, **kwargs)


class WebCrawler:
    """网页爬虫类"""
    
    def __init__(self, base_url: str, headers: Optional[Dict] = None, verify_ssl: bool = True):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            **(headers or {})
        })
        self.visited_urls = set()
        self.verify_ssl = verify_ssl
        
        # 配置重试策略
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False
        )
        
        # 使用自定义适配器配置SSL
        adapter = CustomHTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # 如果禁用SSL验证，禁用警告
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    def fetch_page(self, url: str, max_retries: int = 3) -> Optional[str]:
        """获取网页内容，带增强的SSL处理"""
        if url in self.visited_urls:
            return None
        
        # 首先尝试正常请求
        for attempt in range(max_retries):
            try:
                response = self.session.get(
                    url, 
                    timeout=(10, 30),  # (连接超时, 读取超时)
                    verify=self.verify_ssl,
                    allow_redirects=True
                )
                # 改进编码检测（参考测试脚本 c.py 的成功经验）
                if response.encoding is None or response.encoding.lower() in ['iso-8859-1', 'windows-1252']:
                    # 尝试检测编码
                    try:
                        import chardet
                        detected = chardet.detect(response.content)
                        if detected and detected['encoding']:
                            response.encoding = detected['encoding']
                    except ImportError:
                        pass
                    
                    # 对于中文网站，优先尝试 UTF-8 和 GBK
                    if response.encoding is None or response.encoding.lower() in ['iso-8859-1', 'windows-1252']:
                        for encoding in ['utf-8', 'gbk', 'gb2312']:
                            try:
                                response.content.decode(encoding)
                                response.encoding = encoding
                                break
                            except:
                                continue
                        if response.encoding is None or response.encoding.lower() in ['iso-8859-1', 'windows-1252']:
                            response.encoding = 'utf-8'
                
                if response.status_code == 200:
                    self.visited_urls.add(url)
                    return response.text
                else:
                    print(f"Warning: {url} returned status code {response.status_code}")
            except requests.exceptions.SSLError as e:
                error_str = str(e)
                if attempt < max_retries - 1:
                    print(f"SSL error (attempt {attempt + 1}/{max_retries}): {error_str[:150]}...")
                    time.sleep(2 ** attempt)  # 指数退避
                    continue
                else:
                    print(f"Error fetching {url}: SSL connection failed after {max_retries} attempts")
                    # 如果SSL验证失败，尝试多种降级策略
                    if self.verify_ssl:
                        # 策略1: 禁用SSL验证
                        try:
                            print(f"尝试策略1: 禁用SSL验证重试 {url}...")
                            response = self.session.get(url, timeout=(10, 30), verify=False)
                            # 使用相同的编码检测逻辑（参考测试脚本 c.py）
                            if response.encoding is None or response.encoding.lower() in ['iso-8859-1', 'windows-1252']:
                                try:
                                    import chardet
                                    detected = chardet.detect(response.content)
                                    if detected and detected['encoding']:
                                        response.encoding = detected['encoding']
                                except ImportError:
                                    pass
                                if response.encoding is None or response.encoding.lower() in ['iso-8859-1', 'windows-1252']:
                                    for encoding in ['utf-8', 'gbk', 'gb2312']:
                                        try:
                                            response.content.decode(encoding)
                                            response.encoding = encoding
                                            break
                                        except:
                                            continue
                                    if response.encoding is None or response.encoding.lower() in ['iso-8859-1', 'windows-1252']:
                                        response.encoding = 'utf-8'
                            if response.status_code == 200:
                                self.visited_urls.add(url)
                                return response.text
                        except Exception as e2:
                            print(f"策略1失败: {str(e2)[:150]}...")
                        
                        # 策略2: 使用urllib3直接请求（绕过requests的SSL处理）
                        try:
                            print(f"尝试策略2: 使用urllib3直接请求 {url}...")
                            import urllib3
                            http = urllib3.PoolManager(
                                cert_reqs='CERT_NONE',
                                assert_hostname=False
                            )
                            http_response = http.request('GET', url, timeout=30, retries=3)
                            if http_response.status == 200:
                                # 尝试多种编码（优先 UTF-8，参考测试脚本 c.py）
                                for encoding in ['utf-8', 'gbk', 'gb2312']:
                                    try:
                                        content = http_response.data.decode(encoding)
                                        self.visited_urls.add(url)
                                        return content
                                    except:
                                        continue
                                # 如果都失败，使用utf-8并忽略错误
                                content = http_response.data.decode('utf-8', errors='ignore')
                                self.visited_urls.add(url)
                                return content
                        except Exception as e3:
                            print(f"策略2失败: {str(e3)[:150]}...")
            except requests.exceptions.Timeout as e:
                if attempt < max_retries - 1:
                    print(f"Timeout (attempt {attempt + 1}/{max_retries}): {e}")
                    time.sleep(2 ** attempt)
                    continue
                else:
                    print(f"Error fetching {url}: Timeout after {max_retries} attempts")
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Error (attempt {attempt + 1}/{max_retries}): {str(e)[:150]}...")
                    time.sleep(2 ** attempt)
                    continue
                else:
                    print(f"Error fetching {url}: {str(e)[:150]}...")
        return None
    
    def extract_text(self, html: str) -> str:
        """从HTML中提取纯文本（旧方法：整个页面作为一个文本）"""
        soup = BeautifulSoup(html, 'html.parser')
        
        # 移除脚本和样式
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # 提取文本
        text = soup.get_text()
        
        # 清理文本
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)
        
        return text
    
    def extract_semantic_blocks(self, html: str, url: str) -> List[Dict[str, str]]:
        """
        按语义结构提取文本块（改进方法：区分不同的信息块）
        
        返回格式: [
            {
                "text": "文本内容",
                "title": "块标题（如：学校公开时间）",
                "section": "所属区域（如：基本信息、联系方式等）",
                "url": "来源URL"
            },
            ...
        ]
        """
        soup = BeautifulSoup(html, 'html.parser')
        
        # 移除脚本、样式和不需要的标签（更彻底）
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "iframe", "embed", "object"]):
            tag.decompose()
        
        # 移除所有带有特定class或id的标签（通常是广告、导航等）
        for tag in soup.find_all(class_=re.compile(r'ad|advertisement|sidebar|menu|navigation|nav|footer|header|comment|share', re.I)):
            tag.decompose()
        for tag in soup.find_all(id=re.compile(r'ad|advertisement|sidebar|menu|navigation|nav|footer|header|comment|share', re.I)):
            tag.decompose()
        
        blocks = []
        
        # 策略1: 按语义标签提取（section, article, main, div.content等）
        semantic_tags = soup.find_all(['section', 'article', 'main', 'div'], 
                                     class_=re.compile(r'content|main|info|detail|intro', re.I))
        
        for tag in semantic_tags:
            # 尝试提取标题
            title_tag = tag.find(['h1', 'h2', 'h3', 'h4', 'title'])
            title = title_tag.get_text(strip=True) if title_tag else ""
            
            # 提取文本内容
            text = tag.get_text(separator=' ', strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            
            # 清理文本：移除乱码和无效内容
            text = self._clean_extracted_text(text)
            
            if text and len(text) >= 50 and self._is_valid_text(text):  # 过滤太短和无效的内容
                blocks.append({
                    "text": text,
                    "title": title or "未命名区块",
                    "section": self._classify_section(title, text),
                    "url": url
                })
        
        # 策略2: 如果没有找到语义标签，按标题（h1-h4）分割
        if not blocks:
            current_block = {"text": "", "title": "", "section": "其他", "url": url}
            
            for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'div']):
                if element.name in ['h1', 'h2', 'h3', 'h4']:
                    # 遇到新标题，保存上一个块
                    if current_block["text"] and len(current_block["text"]) >= 50:
                        blocks.append(current_block.copy())
                    
                    # 开始新块
                    current_block = {
                        "text": "",
                        "title": element.get_text(strip=True),
                        "section": self._classify_section(current_block["title"], ""),
                        "url": url
                    }
                else:
                    # 累积文本
                    text = element.get_text(separator=' ', strip=True)
                    # 清理文本
                    text = self._clean_extracted_text(text)
                    if text and self._is_valid_text(text):
                        current_block["text"] += " " + text
            
            # 保存最后一个块
            if current_block["text"] and len(current_block["text"]) >= 50:
                blocks.append(current_block)
        
        # 策略3: 针对特定网站的特殊处理
        if not blocks:
            # 针对同济大学学校简介页面的特殊处理（p > span > span 结构）
            if 'tongji.edu.cn' in url and 'xxjj' in url:
                blocks = self._extract_tongji_intro_page(soup, url)
            
            # 针对同济大学官网常见布局的处理：
            # body > div.content.container.clearfix > div.section-right.fr > p
            if not blocks and 'tongji.edu.cn' in url:
                tongji_common_blocks = self._extract_tongji_common_layout(soup, url)
                if tongji_common_blocks:
                    blocks = tongji_common_blocks
            
            # 针对百度百科的特殊处理
            if not blocks and 'baike.baidu.com' in url:
                # 尝试提取百度百科的主要内容区域
                main_content = soup.find('div', class_=re.compile(r'main-content|lemma-summary|lemmaWgt-lemmaSummary', re.I))
                if main_content:
                    text = main_content.get_text(separator=' ', strip=True)
                    text = self._clean_extracted_text(text)
                    if text and len(text) >= 50 and self._is_valid_text(text):
                        blocks.append({
                            "text": text,
                            "title": "页面内容",
                            "section": "基本信息",
                            "url": url
                        })
            
            # 如果还是没找到，使用通用方法
            if not blocks:
                text = soup.get_text()
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = ' '.join(chunk for chunk in chunks if chunk)
                text = self._clean_extracted_text(text)
                
                if text and len(text) >= 50 and self._is_valid_text(text):
                    blocks.append({
                        "text": text,
                        "title": "页面内容",
                        "section": "其他",
                        "url": url
                    })
        
        return blocks
    
    def _extract_tongji_intro_page(self, soup: BeautifulSoup, url: str) -> List[Dict[str, str]]:
        """
        专门提取同济大学学校简介页面的内容（p > span > span 结构）
        
        Args:
            soup: BeautifulSoup 解析对象
            url: 页面URL
            
        Returns:
            文本块列表
        """
        blocks = []
        
        # 提取页面标题
        page_title = ""
        title_tag = soup.find('title')
        if title_tag:
            page_title = title_tag.get_text(strip=True)
        else:
            h1_tag = soup.find('h1')
            if h1_tag:
                page_title = h1_tag.get_text(strip=True)
        
        # 查找所有 p 标签
        paragraphs = soup.find_all('p')
        text_parts = []
        
        for p in paragraphs:
            # 跳过导航、页脚等不需要的内容
            p_classes = p.get('class', [])
            p_id = p.get('id', '')
            if any(keyword in str(p_classes).lower() or keyword in str(p_id).lower() 
                   for keyword in ['nav', 'menu', 'footer', 'header', 'sidebar']):
                continue
            
            # 方法1: 查找 p > span > span 结构（最精确）
            # 查找 p 的直接子元素中的 span（使用 recursive=False 只查找直接子元素）
            direct_spans = p.find_all('span', recursive=False, limit=10)  # limit 防止过多
            
            found_span_text = False
            for outer_span in direct_spans:
                # 查找外层 span 的直接子元素中的 span
                inner_spans = outer_span.find_all('span', recursive=False, limit=10)
                
                if inner_spans:
                    # 找到内层 span，提取其文本
                    found_span_text = True
                    for inner_span in inner_spans:
                        text = inner_span.get_text(separator=' ', strip=True)
                        # 清理文本
                        text = re.sub(r'\s+', ' ', text).strip()
                        if text and len(text) > 10:
                            text_parts.append(text)
                else:
                    # 没有内层 span，提取外层 span 的文本（但只提取有意义的文本）
                    text = outer_span.get_text(separator=' ', strip=True)
                    text = re.sub(r'\s+', ' ', text).strip()
                    # 检查是否包含中文或足够长的英文
                    has_chinese = bool(re.search(r'[\u4e00-\u9fa5]', text))
                    if text and len(text) > 10 and (has_chinese or len(text) > 30):
                        found_span_text = True
                        text_parts.append(text)
            
            # 方法2: 如果 p 标签下没有 span > span 结构，但有文本内容，也提取
            if not found_span_text:
                text = p.get_text(separator=' ', strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                # 过滤掉太短或明显是导航的文本
                if text and len(text) > 20 and not any(keyword in text for keyword in ['首页', '导航', '菜单', '返回顶部']):
                    text_parts.append(text)
        
        # 合并所有文本部分
        if text_parts:
            # 清理和合并文本
            full_text = '\n\n'.join(text_parts)
            full_text = self._clean_extracted_text(full_text)
            
            if full_text and len(full_text) >= 50 and self._is_valid_text(full_text):
                blocks.append({
                    "text": full_text,
                    "title": page_title or "学校简介",
                    "section": "基本信息",
                    "url": url
                })
        
        return blocks

    def _extract_tongji_common_layout(self, soup: BeautifulSoup, url: str) -> List[Dict[str, str]]:
        """
        提取同济官网常见模板中的正文：
        一般结构为：
        body
          └─ div.content.container.clearfix
               └─ div.section-right.fr
                    └─ p（正文）

        你的描述「body-div class=content container clearfix-div clas=section-right fr-里面的p标签」
        对应的就是这一结构。
        """
        blocks: List[Dict[str, str]] = []

        # 页面标题
        page_title = ""
        title_tag = soup.find('h1') or soup.find('title')
        if title_tag:
            page_title = title_tag.get_text(strip=True)

        # 找到外层 content 容器
        def has_classes(tag, required: List[str]) -> bool:
            classes = tag.get('class', [])
            if not classes:
                return False
            class_set = set(classes)
            return all(c in class_set for c in required)

        content_div = None
        for div in soup.find_all('div'):
            if has_classes(div, ['content', 'container', 'clearfix']):
                content_div = div
                break

        if not content_div:
            return blocks

        # 内层右侧正文区域 section-right fr
        right_div = None
        for div in content_div.find_all('div', recursive=False):
            # 有的页面 class 顺序不同，这里只要求同时包含两个 class
            classes = div.get('class', [])
            if not classes:
                continue
            class_set = set(classes)
            if 'section-right' in class_set and 'fr' in class_set:
                right_div = div
                break

        if not right_div:
            # 有些页面 right 区域可能不是直接子元素，放宽为在 content_div 下任意层级查找
            for div in content_div.find_all('div'):
                classes = div.get('class', [])
                if not classes:
                    continue
                class_set = set(classes)
                if 'section-right' in class_set and 'fr' in class_set:
                    right_div = div
                    break

        if not right_div:
            return blocks

        # 收集正文 p 标签文本
        text_parts: List[str] = []
        paragraphs = right_div.find_all('p')

        for p in paragraphs:
            text = p.get_text(separator=' ', strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            if not text:
                continue

            # 过滤明显的导航类短文本
            if len(text) < 10:
                continue
            if any(keyword in text for keyword in ['首页', '导航', '返回顶部']):
                continue

            # 使用统一的清理和有效性判断
            cleaned = self._clean_extracted_text(text)
            if cleaned and self._is_valid_text(cleaned):
                text_parts.append(cleaned)

        if not text_parts:
            return blocks

        full_text = '\n\n'.join(text_parts)
        full_text = self._clean_extracted_text(full_text)

        if full_text and len(full_text) >= 50 and self._is_valid_text(full_text):
            blocks.append({
                "text": full_text,
                "title": page_title or "页面内容",
                "section": self._classify_section(page_title, full_text),
                "url": url,
            })

        return blocks
    
    def _clean_extracted_text(self, text: str) -> str:
        """清理提取的文本，移除乱码和无效字符"""
        if not text:
            return ""
        
        # 移除多余空白
        text = re.sub(r'\s+', ' ', text).strip()
        
        # 移除明显的乱码模式（连续的大写字母和数字混合，没有中文）
        # 如果文本中超过70%是乱码字符，则认为是无效文本
        chinese_chars = len(re.findall(r'[\u4e00-\u9fa5]', text))
        total_chars = len(re.findall(r'[^\s]', text))
        if total_chars > 0:
            chinese_ratio = chinese_chars / total_chars
            # 如果中文比例太低（<10%），且包含大量乱码模式，则过滤
            if chinese_ratio < 0.1:
                # 检查是否包含大量连续大写字母和数字（可能是混淆代码）
                garbage_pattern = re.compile(r'[A-Z0-9]{10,}')
                garbage_matches = len(garbage_pattern.findall(text))
                if garbage_matches > 3:  # 如果有很多乱码模式，认为是无效文本
                    return ""
        
        # 移除特殊字符，但保留中文、英文、数字和基本标点
        text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9，。！？；：、\s]', '', text)
        
        return text.strip()
    
    def _is_valid_text(self, text: str) -> bool:
        """判断文本是否有效（不是乱码）"""
        if not text or len(text) < 10:
            return False
        
        # 检查是否包含至少一些中文或英文单词
        has_chinese = bool(re.search(r'[\u4e00-\u9fa5]', text))
        has_english_words = bool(re.search(r'\b[a-zA-Z]{3,}\b', text))
        
        if not (has_chinese or has_english_words):
            return False
        
        # 检查乱码比例
        # 如果文本主要由单个字符和数字组成，没有有意义的单词，可能是乱码
        words = re.findall(r'\b[\u4e00-\u9fa5]{2,}|\b[a-zA-Z]{3,}\b', text)
        if len(words) < 3:  # 至少要有3个有意义的词
            return False
        
        return True
    
    def _classify_section(self, title: str, text: str) -> str:
        """根据标题和文本内容分类信息块"""
        content = (title + " " + text).lower()
        
        # 关键词匹配分类
        if any(kw in content for kw in ['时间', '开放', '营业', '办公时间', '开放时间']):
            return "时间信息"
        elif any(kw in content for kw in ['地址', '位置', '地图', '校区', '交通']):
            return "位置信息"
        elif any(kw in content for kw in ['电话', '联系', '邮箱', 'email', '联系方式']):
            return "联系方式"
        elif any(kw in content for kw in ['简介', '介绍', '历史', '沿革', '概况']):
            return "基本信息"
        elif any(kw in content for kw in ['新闻', '公告', '通知', '动态']):
            return "新闻公告"
        elif any(kw in content for kw in ['招生', '专业', '课程', '培养']):
            return "招生信息"
        elif any(kw in content for kw in ['学术', '科研', '研究', '论文', '项目']):
            return "学术信息"
        else:
            return "其他"
    
    def extract_links(self, html: str, base_url: str) -> List[str]:
        """提取页面中的所有链接"""
        soup = BeautifulSoup(html, 'html.parser')
        links = []
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            full_url = urljoin(base_url, href)
            
            # 只保留同域名的链接
            if urlparse(full_url).netloc == urlparse(base_url).netloc:
                links.append(full_url)
        
        return links


class TextProcessor:
    """文本处理类：分块、清理"""
    
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
        )
    
    def clean_text(self, text: str) -> str:
        """清理文本"""
        # 移除多余空白
        text = re.sub(r'\s+', ' ', text)
        # 移除特殊字符（保留中文、英文、数字、基本标点）
        text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9，。！？；：、\s]', '', text)
        return text.strip()
    
    def split_text(self, text: str) -> List[str]:
        """将文本分块"""
        cleaned = self.clean_text(text)
        if not cleaned or len(cleaned) < 50:  # 太短的文本不处理
            return []
        
        chunks = self.splitter.split_text(cleaned)
        return [chunk for chunk in chunks if len(chunk.strip()) >= 50]


class DataIngester:
    """数据入库类：向量化并存储到 Milvus"""
    
    def __init__(self):
        self.client = MilvusClient(
            uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
        )
        
        # 配置SSL环境变量（用于dashscope SDK的httpx客户端）
        # 降低SSL安全级别以兼容更多服务器
        os.environ.setdefault('SSL_CERT_FILE', '/etc/ssl/certs/ca-certificates.crt')
        os.environ.setdefault('REQUESTS_CA_BUNDLE', '/etc/ssl/certs/ca-certificates.crt')
        
        self.embedder = DashScopeEmbeddings(
            model=settings.EMBEDDING_MODEL,
            dashscope_api_key=settings.DASHSCOPE_API_KEY
        )
    
    def _ensure_collection_exists(self, collection_name: str, is_faq: bool = False):
        """确保集合存在，如果不存在则创建"""
        try:
            existing_cols = self.client.list_collections()
            if collection_name not in existing_cols:
                print(f"  集合 {collection_name} 不存在，正在创建...")
                self.client.create_collection(
                    collection_name=collection_name,
                    dimension=1024,  # text-embedding-v4 的维度是 1024
                    metric_type="COSINE",
                    auto_id=True
                )
                print(f" 已创建集合 {collection_name}")
            else:
                print(f" 集合 {collection_name} 已存在")
        except Exception as e:
            print(f" 检查/创建集合失败: {e}")
            raise
    
    def _embed_with_retry(self, text: str, max_retries: int = 5) -> Optional[List[float]]:
        """带重试的向量化方法，支持多种降级策略"""
        # 策略1: 正常重试（带指数退避）
        for attempt in range(max_retries):
            try:
                return self.embedder.embed_query(text)
            except Exception as e:
                error_msg = str(e)
                is_ssl_error = any(keyword in error_msg for keyword in [
                    "SSL", "SSLError", "SSL: UNEXPECTED_EOF", 
                    "SSL: EOF", "SSL: WRONG_VERSION_NUMBER",
                    "Connection broken", "HTTPSConnectionPool"
                ])
                
                if is_ssl_error:
                    if attempt < max_retries - 1:
                        wait_time = min(2 ** attempt, 10)  # 最多等待10秒
                        print(f"SSL错误 (尝试 {attempt + 1}/{max_retries}): {error_msg[:100]}...")
                        print(f"等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        # 策略2: 尝试禁用SSL验证（通过环境变量）
                        print(f"尝试降级策略: 禁用SSL验证...")
                        try:
                            # 临时设置环境变量禁用SSL验证
                            original_verify = os.environ.get('PYTHONHTTPSVERIFY', '1')
                            os.environ['PYTHONHTTPSVERIFY'] = '0'
                            
                            # 重新创建embedder（可能会使用新的SSL设置）
                            temp_embedder = DashScopeEmbeddings(
                                model=settings.EMBEDDING_MODEL,
                                dashscope_api_key=settings.DASHSCOPE_API_KEY
                            )
                            result = temp_embedder.embed_query(text)
                            
                            # 恢复环境变量
                            os.environ['PYTHONHTTPSVERIFY'] = original_verify
                            return result
                        except Exception as e2:
                            # 恢复环境变量
                            if 'original_verify' in locals():
                                os.environ['PYTHONHTTPSVERIFY'] = original_verify
                            print(f"降级策略也失败: {str(e2)[:150]}...")
                            print(f"向量化失败 (SSL错误，已尝试所有策略): {error_msg[:200]}")
                            return None
                else:
                    # 非SSL错误，直接返回
                    if attempt < max_retries - 1:
                        wait_time = min(2 ** attempt, 5)
                        print(f"向量化错误 (尝试 {attempt + 1}/{max_retries}): {error_msg[:100]}...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"向量化失败: {error_msg[:200]}")
                        return None
        return None
    
    def ingest_texts(
        self, 
        texts: List[str], 
        collection_name: str,
        source: str = "官网",
        dept_id: str = "",
        user_id: str = "",
        batch_size: int = 10
    ):
        """批量插入文本到 Milvus（旧方法：只接受文本列表）"""
        if not texts:
            return
        
        # 确保集合存在
        self._ensure_collection_exists(collection_name, is_faq=False)
        
        print(f"开始插入 {len(texts)} 条文本到 {collection_name}...")
        
        # 批量处理
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            rows = []
            
            for text in batch:
                # 使用带重试的向量化方法
                vector = self._embed_with_retry(text)
                if vector is None:
                    print(f"  跳过向量化失败的文本: {text[:50]}...")
                    continue
                
                # 确保向量维度正确
                if len(vector) != 1024:
                    print(f"  向量维度错误: 期望 1024，实际 {len(vector)}")
                    continue
                
                row = {
                    "vector": vector,
                    "text": text,
                    "source": source,
                    "dept_id": dept_id if dept_id else "",
                    "user_id": user_id if user_id else ""
                }
                rows.append(row)
            
            if rows:
                try:
                    result = self.client.insert(collection_name=collection_name, data=rows)
                    # 兼容两种返回格式
                    if isinstance(result, dict):
                        milvus_ids = result.get("ids", [])
                    elif isinstance(result, list):
                        milvus_ids = result
                    else:
                        milvus_ids = getattr(result, "ids", [])
                    inserted_count = len(milvus_ids)
                    print(f" 已插入 {inserted_count}/{len(rows)} 条数据到 {collection_name}")
                except Exception as e:
                    error_msg = str(e)
                    print(f" 插入失败: {error_msg}")
                    # 打印更详细的错误信息
                    if "field" in error_msg.lower() or "schema" in error_msg.lower():
                        print(f"   提示: 可能是字段结构不匹配，请检查集合 schema")
                    elif "dimension" in error_msg.lower():
                        print(f"   提示: 向量维度不匹配，期望 1024 维")
                    elif "collection" in error_msg.lower() and "not exist" in error_msg.lower():
                        print(f"   提示: 集合不存在，尝试重新创建...")
                        try:
                            self._ensure_collection_exists(collection_name, is_faq=False)
                            # 重试插入
                            result = self.client.insert(collection_name=collection_name, data=rows)
                            # 兼容两种返回格式
                            if isinstance(result, dict):
                                milvus_ids = result.get("ids", [])
                            elif isinstance(result, list):
                                milvus_ids = result
                            else:
                                milvus_ids = getattr(result, "ids", [])
                            print(f" 重试成功，已插入 {len(milvus_ids)} 条数据")
                        except Exception as e2:
                            print(f" 重试也失败: {e2}")
            
            # 避免API限流
            time.sleep(0.5)
    
    def ingest_blocks(
        self,
        blocks: List[Dict[str, str]],  # [{"text": "...", "title": "...", "section": "...", "url": "..."}]
        collection_name: str,
        dept_id: str = "",
        user_id: str = "",
        batch_size: int = 10
    ) -> List[str]:
        """
        批量插入语义块到 Milvus（改进方法：保留更多元数据）
        
        Args:
            blocks: 语义块列表，每个块包含 text, title, section, url
        
        Returns:
            milvus_ids: 插入的 Milvus ID 列表，与 blocks 顺序对应
        """
        if not blocks:
            return []
        
        # 确保集合存在
        self._ensure_collection_exists(collection_name, is_faq=False)
        
        print(f"开始插入 {len(blocks)} 个语义块到 {collection_name}...")
        
        all_milvus_ids = [None] * len(blocks)  # 预先分配，确保长度一致
        
        # 批量处理
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i:i + batch_size]
            rows = []
            row_to_block_idx = []  # 记录每个 row 对应的原始块索引
            
            for idx, block in enumerate(batch):
                original_idx = i + idx
                # 使用带重试的向量化方法
                vector = self._embed_with_retry(block["text"])
                if vector is None:
                    print(f"  跳过向量化失败的块: {block.get('title', '未命名')[:50]}...")
                    # all_milvus_ids[original_idx] 保持为 None
                    continue
                
                # 确保向量维度正确
                if len(vector) != 1024:
                    print(f"  向量维度错误: 期望 1024，实际 {len(vector)}")
                    continue
                
                # 构建更丰富的source信息（限制长度避免过长）
                source = f"{block.get('section', '其他')}-{block.get('title', '')}"
                if block.get('url'):
                    source += f"|{block['url']}"
                # 限制 source 长度（Milvus 可能有字段长度限制）
                if len(source) > 500:
                    source = source[:500]
                
                row = {
                    "vector": vector,
                    "text": block["text"],
                    "source": source,
                    "dept_id": dept_id if dept_id else "",
                    "user_id": user_id if user_id else ""
                }
                rows.append(row)
                row_to_block_idx.append(original_idx)  # 记录对应关系
            
            if rows:
                try:
                    # Milvus insert 返回插入的 IDs
                    # 注意：不同版本的 Milvus 客户端可能返回不同格式
                    # - 字典格式：{"ids": [1, 2, 3]}
                    # - 列表格式：[1, 2, 3]
                    result = self.client.insert(collection_name=collection_name, data=rows)
                    
                    # 兼容两种返回格式
                    if isinstance(result, dict):
                        milvus_ids = result.get("ids", [])
                    elif isinstance(result, list):
                        milvus_ids = result
                    else:
                        # 如果返回其他格式，尝试获取 ids 属性
                        milvus_ids = getattr(result, "ids", [])
                    
                    # 将 IDs 按对应关系填入结果列表
                    for j, milvus_id in enumerate(milvus_ids):
                        if j < len(row_to_block_idx):
                            block_idx = row_to_block_idx[j]
                            all_milvus_ids[block_idx] = str(milvus_id)
                    
                    print(f" 已插入 {len(milvus_ids)}/{len(rows)} 个语义块到 {collection_name}")
                except Exception as e:
                    error_msg = str(e)
                    print(f" 插入失败: {error_msg}")
                    # 打印更详细的错误信息
                    if "field" in error_msg.lower() or "schema" in error_msg.lower():
                        print(f"   提示: 可能是字段结构不匹配，请检查集合 schema")
                    elif "dimension" in error_msg.lower():
                        print(f"   提示: 向量维度不匹配，期望 1024 维")
                    elif "collection" in error_msg.lower() and "not exist" in error_msg.lower():
                        print(f"   提示: 集合不存在，尝试重新创建...")
                        try:
                            self._ensure_collection_exists(collection_name, is_faq=False)
                            # 重试插入
                            result = self.client.insert(collection_name=collection_name, data=rows)
                            # 兼容两种返回格式
                            if isinstance(result, dict):
                                milvus_ids = result.get("ids", [])
                            elif isinstance(result, list):
                                milvus_ids = result
                            else:
                                milvus_ids = getattr(result, "ids", [])
                            for j, milvus_id in enumerate(milvus_ids):
                                if j < len(row_to_block_idx):
                                    block_idx = row_to_block_idx[j]
                                    all_milvus_ids[block_idx] = str(milvus_id)
                            print(f" 重试成功，已插入 {len(milvus_ids)} 个语义块")
                        except Exception as e2:
                            print(f" 重试也失败: {e2}")
                    # 失败时保持为 None（已预先分配）
            
            # 避免API限流
            time.sleep(0.5)
        
        return all_milvus_ids
    
    def ingest_faqs(
        self,
        faqs: List[Dict[str, str]],  # [{"q": "问题", "a": "答案", "source": "来源"}]
        batch_size: int = 10
    ):
        """批量插入FAQ到 Milvus"""
        if not faqs:
            return
        
        # 确保集合存在
        self._ensure_collection_exists(settings.COLLECTION_FAQ, is_faq=True)
        
        print(f"开始插入 {len(faqs)} 条FAQ...")
        
        for i in range(0, len(faqs), batch_size):
            batch = faqs[i:i + batch_size]
            rows = []
            
            for faq in batch:
                # 使用带重试的向量化方法
                vector = self._embed_with_retry(faq["q"])
                if vector is None:
                    print(f"  跳过向量化失败的FAQ: {faq['q'][:50]}...")
                    continue
                
                # 确保向量维度正确
                if len(vector) != 1024:
                    print(f"  向量维度错误: 期望 1024，实际 {len(vector)}")
                    continue
                
                row = {
                    "vector": vector,
                    "question": faq["q"],
                    "answer": faq["a"],
                    "source": faq.get("source", "faq")
                }
                rows.append(row)
            
            if rows:
                try:
                    result = self.client.insert(
                        collection_name=settings.COLLECTION_FAQ, 
                        data=rows
                    )
                    # 兼容两种返回格式
                    if isinstance(result, dict):
                        milvus_ids = result.get("ids", [])
                    elif isinstance(result, list):
                        milvus_ids = result
                    else:
                        milvus_ids = getattr(result, "ids", [])
                    inserted_count = len(milvus_ids)
                    print(f" 已插入 {inserted_count}/{len(rows)} 条FAQ")
                except Exception as e:
                    error_msg = str(e)
                    print(f" FAQ插入失败: {error_msg}")
                    # 打印更详细的错误信息
                    if "field" in error_msg.lower() or "schema" in error_msg.lower():
                        print(f"   提示: 可能是字段结构不匹配，FAQ集合应包含 question, answer, source 字段")
                    elif "dimension" in error_msg.lower():
                        print(f"   提示: 向量维度不匹配，期望 1024 维")
                    elif "collection" in error_msg.lower() and "not exist" in error_msg.lower():
                        print(f"   提示: 集合不存在，尝试重新创建...")
                        try:
                            self._ensure_collection_exists(settings.COLLECTION_FAQ, is_faq=True)
                            result = self.client.insert(collection_name=settings.COLLECTION_FAQ, data=rows)
                            # 兼容两种返回格式
                            if isinstance(result, dict):
                                milvus_ids = result.get("ids", [])
                            elif isinstance(result, list):
                                milvus_ids = result
                            else:
                                milvus_ids = getattr(result, "ids", [])
                            print(f" 重试成功，已插入 {len(milvus_ids)} 条FAQ")
                        except Exception as e2:
                            print(f" 重试也失败: {e2}")
            
            time.sleep(0.5)


def crawl_standard_info(urls: List[str], max_pages: int = 50, use_semantic_blocks: bool = True):
    """
    爬取公开标准信息（先存 MySQL，再存 Milvus）
    
    Args:
        urls: 要爬取的URL列表
        max_pages: 最大爬取页面数
        use_semantic_blocks: 是否使用语义块提取（True=按语义区分，False=旧方法按字符数分割）
    """
    # 创建数据库会话
    db_session = get_sync_db_session()
    
    try:
        # 1. 创建爬取任务记录
        task = CrawlTask(
            url=urls[0] if urls else "",
            collection_name=settings.COLLECTION_STANDARD,
            status="running",
            started_at=datetime.now()
        )
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)
        task_id = task.id
        print(f" 创建爬取任务 #{task_id}: {task.url}")
        
        crawler = WebCrawler(base_url=urls[0] if urls else "")
        processor = TextProcessor(chunk_size=500, chunk_overlap=50)
        ingester = DataIngester()
        
        all_blocks = []  # 存储语义块
        all_texts = []   # 存储旧方法的文本块
        pages_crawled = 0
        crawl_blocks = []  # 存储 CrawlBlock 对象（用于批量插入）
        
        # 广度优先爬取
        queue = urls[:]
        
        while queue and pages_crawled < max_pages:
            url = queue.pop(0)
            print(f"正在爬取: {url}")
            
            html = crawler.fetch_page(url)
            if not html:
                continue
            
            if use_semantic_blocks:
                # 新方法：按语义块提取
                blocks = crawler.extract_semantic_blocks(html, url)
                for block in blocks:
                    # 如果块太大，进一步分割
                    if len(block["text"]) > 1000:
                        chunks = processor.split_text(block["text"])
                        for chunk in chunks:
                            all_blocks.append({
                                "text": chunk,
                                "title": block["title"],
                                "section": block["section"],
                                "url": block["url"]
                            })
                            # 创建 CrawlBlock 记录（先存 MySQL）
                            text_preview = chunk[:500] if len(chunk) > 500 else chunk
                            crawl_block = CrawlBlock(
                                task_id=task_id,
                                url=block["url"],
                                title=block["title"],
                                section=block["section"],
                                collection_name=settings.COLLECTION_STANDARD,
                                access_scope="public",
                                text_preview=text_preview,
                                text_content=chunk,
                            )
                            crawl_blocks.append(crawl_block)
                            db_session.add(crawl_block)
                    else:
                        all_blocks.append(block)
                        # 创建 CrawlBlock 记录（先存 MySQL）
                        text_preview = block["text"][:500] if len(block["text"]) > 500 else block["text"]
                        crawl_block = CrawlBlock(
                            task_id=task_id,
                            url=block["url"],
                            title=block["title"],
                            section=block["section"],
                            collection_name=settings.COLLECTION_STANDARD,
                            access_scope="public",
                            text_preview=text_preview,
                            text_content=block["text"],
                        )
                        crawl_blocks.append(crawl_block)
                        db_session.add(crawl_block)
            else:
                # 旧方法：整个页面提取后按字符数分割
                text = crawler.extract_text(html)
                if text:
                    chunks = processor.split_text(text)
                    for chunk in chunks:
                        all_texts.append({
                            "text": chunk,
                            "source": f"官网-{urlparse(url).path}"
                        })
                        # 创建 CrawlBlock 记录（先存 MySQL）
                        text_preview = chunk[:500] if len(chunk) > 500 else chunk
                        crawl_block = CrawlBlock(
                            task_id=task_id,
                            url=url,
                            title="页面内容",
                            section="其他",
                            collection_name=settings.COLLECTION_STANDARD,
                            access_scope="public",
                            text_preview=text_preview,
                            text_content=chunk,
                        )
                        crawl_blocks.append(crawl_block)
                        db_session.add(crawl_block)
            
            # 每10个块提交一次数据库（避免内存占用过大）
            if len(crawl_blocks) % 10 == 0:
                db_session.commit()
            
            # 提取链接（可选：继续爬取）
            # links = crawler.extract_links(html, url)
            # for link in links[:5]:  # 限制每个页面最多5个链接
            #     if link not in crawler.visited_urls and link not in queue:
            #         queue.append(link)
            
            pages_crawled += 1
            time.sleep(1)  # 礼貌延迟
        
        # 提交剩余的 CrawlBlock 记录
        db_session.commit()
        print(f" 已保存 {len(crawl_blocks)} 个文本块到 MySQL")
        
        # 2. 插入到 Milvus 并获取 IDs
        if use_semantic_blocks and all_blocks:
            milvus_ids = ingester.ingest_blocks(
                all_blocks,
                collection_name=settings.COLLECTION_STANDARD
            )
            
            # 3. 更新 CrawlBlock 的 milvus_id
            for i, (crawl_block, milvus_id) in enumerate(zip(crawl_blocks, milvus_ids)):
                if milvus_id:
                    crawl_block.milvus_id = milvus_id
            
            db_session.commit()
            
            # 4. 更新任务状态
            task.status = "completed"
            task.pages_crawled = pages_crawled
            task.blocks_inserted = len(all_blocks)
            task.completed_at = datetime.now()
            db_session.commit()
            
            print(f" 完成！共爬取 {pages_crawled} 页，插入 {len(all_blocks)} 个语义块")
        elif all_texts:
            # 旧方法：使用 ingest_texts（不返回 IDs，暂时不关联）
            ingester.ingest_texts(
                [item["text"] for item in all_texts],
                collection_name=settings.COLLECTION_STANDARD,
                source="官网"
            )
            
            # 更新任务状态
            task.status = "completed"
            task.pages_crawled = pages_crawled
            task.blocks_inserted = len(all_texts)
            task.completed_at = datetime.now()
            db_session.commit()
            
            print(f" 完成！共爬取 {pages_crawled} 页，插入 {len(all_texts)} 条文本块")
        else:
            # 没有爬取到数据
            task.status = "failed"
            task.error_message = "未爬取到任何数据"
            task.completed_at = datetime.now()
            db_session.commit()
            print(" 未爬取到任何数据")
            
    except Exception as e:
        # 错误处理
        print(f" 爬取失败: {e}")
        try:
            task.status = "failed"
            task.error_message = str(e)
            task.completed_at = datetime.now()
            db_session.commit()
        except:
            pass
        raise
    finally:
        db_session.close()


def crawl_academic_info(urls: List[str], max_pages: int = 30):
    """
    爬取学术科研信息（先存 MySQL，再存 Milvus）
    """
    # 创建数据库会话
    db_session = get_sync_db_session()
    
    try:
        # 1. 创建爬取任务记录
        task = CrawlTask(
            url=urls[0] if urls else "",
            collection_name=settings.COLLECTION_KNOWLEDGE,
            status="running",
            started_at=datetime.now()
        )
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)
        task_id = task.id
        print(f" 创建爬取任务 #{task_id}: {task.url}")
        
        crawler = WebCrawler(base_url=urls[0] if urls else "")
        processor = TextProcessor(chunk_size=600, chunk_overlap=50)
        ingester = DataIngester()
        
        all_texts = []
        pages_crawled = 0
        crawl_blocks = []  # 存储 CrawlBlock 对象
        
        for url in urls[:max_pages]:
            print(f"正在爬取学术信息: {url}")
            
            html = crawler.fetch_page(url)
            if not html:
                continue
            
            text = crawler.extract_text(html)
            if text:
                chunks = processor.split_text(text)
                for chunk in chunks:
                    all_texts.append({
                        "text": chunk,
                        "source": f"学术-{urlparse(url).path}"
                    })
                    # 创建 CrawlBlock 记录（先存 MySQL）
                    text_preview = chunk[:500] if len(chunk) > 500 else chunk
                    crawl_block = CrawlBlock(
                        task_id=task_id,
                        url=url,
                        title="学术内容",
                        section="学术信息",
                        collection_name=settings.COLLECTION_KNOWLEDGE,
                        access_scope="campus",
                        text_preview=text_preview,
                        text_content=chunk,
                    )
                    crawl_blocks.append(crawl_block)
                    db_session.add(crawl_block)
            
            # 每10个块提交一次数据库
            if len(crawl_blocks) % 10 == 0:
                db_session.commit()
            
            pages_crawled += 1
            time.sleep(1)
        
        # 提交剩余的 CrawlBlock 记录
        db_session.commit()
        print(f" 已保存 {len(crawl_blocks)} 个文本块到 MySQL")
        
        # 2. 插入到 Milvus
        if all_texts:
            ingester.ingest_texts(
                [item["text"] for item in all_texts],
                collection_name=settings.COLLECTION_KNOWLEDGE,
                source="学术网站"
            )
            
            # 3. 更新任务状态
            task.status = "completed"
            task.pages_crawled = pages_crawled
            task.blocks_inserted = len(all_texts)
            task.completed_at = datetime.now()
            db_session.commit()
            
            print(f" 完成！共爬取 {pages_crawled} 页，插入 {len(all_texts)} 条文本块")
        else:
            # 没有爬取到数据
            task.status = "failed"
            task.error_message = "未爬取到任何数据"
            task.completed_at = datetime.now()
            db_session.commit()
            print(" 未爬取到任何数据")
            
    except Exception as e:
        # 错误处理
        print(f" 爬取失败: {e}")
        try:
            task.status = "failed"
            task.error_message = str(e)
            task.completed_at = datetime.now()
            db_session.commit()
        except:
            pass
        raise
    finally:
        db_session.close()


def add_faqs_manually(faqs: List[Dict[str, str]]):
    """
    手动添加FAQ（问答对）
    
    Args:
        faqs: [{"q": "问题", "a": "答案", "source": "来源"}]
    """
    ingester = DataIngester()
    ingester.ingest_faqs(faqs)
    print(f" 完成！共插入 {len(faqs)} 条FAQ")


if __name__ == "__main__":
    # ==========================================
    # 使用说明：
    # 1. 复制 crawl_config_example.py 为 crawl_config.py
    # 2. 在 crawl_config.py 中修改 URL 列表和配置参数
    # 3. 运行此脚本：python crawler.py
    # ==========================================
    
    # 尝试从配置文件导入
    try:
        from crawl_config import STANDARD_URLS, ACADEMIC_URLS, MANUAL_FAQS, CRAWL_CONFIG
        print(" 已加载配置文件 crawl_config.py")
    except ImportError:
        print("  未找到 crawl_config.py 文件")
        print(" 请先复制 crawl_config_example.py 为 crawl_config.py，并修改其中的URL列表")
        print("\n使用默认配置（仅作演示，不会实际运行）...")
        STANDARD_URLS = [
            "https://www.tongji.edu.cn/",
        ]
        ACADEMIC_URLS = []
        MANUAL_FAQS = []
        CRAWL_CONFIG = {
            "max_pages_standard": 20,
            "max_pages_academic": 10,
            "delay_seconds": 1,
            "chunk_size_standard": 500,
            "chunk_size_academic": 600,
            "chunk_overlap": 50,
        }
        print(" 请创建 crawl_config.py 后再运行")
        exit(1)
    
    # 1. 爬取公开标准信息
    if STANDARD_URLS:
        print("\n" + "=" * 60)
        print(" 开始爬取公开标准信息...")
        print("=" * 60)
        print(f" URL数量: {len(STANDARD_URLS)}")
        print(f" 最大页数: {CRAWL_CONFIG.get('max_pages_standard', 50)}")
        print(f" 使用语义块提取: True")
        print()
        
        crawl_standard_info(
            urls=STANDARD_URLS,
            max_pages=CRAWL_CONFIG.get('max_pages_standard', 50),
            use_semantic_blocks=True  # 推荐使用语义块提取
        )
    
    # 2. 爬取学术科研信息
    if ACADEMIC_URLS:
        print("\n" + "=" * 60)
        print(" 开始爬取学术科研信息...")
        print("=" * 60)
        print(f" URL数量: {len(ACADEMIC_URLS)}")
        print(f" 最大页数: {CRAWL_CONFIG.get('max_pages_academic', 30)}")
        print()
        
        crawl_academic_info(
            urls=ACADEMIC_URLS,
            max_pages=CRAWL_CONFIG.get('max_pages_academic', 30)
        )
    
    # 3. 手动添加FAQ
    if MANUAL_FAQS:
        print("\n" + "=" * 60)
        print(" 开始添加FAQ...")
        print("=" * 60)
        print(f" FAQ数量: {len(MANUAL_FAQS)}")
        print()
        
        add_faqs_manually(MANUAL_FAQS)
    
    print("\n" + "=" * 60)
    print(" 所有任务完成！")
    print("=" * 60)

