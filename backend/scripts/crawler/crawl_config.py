"""
爬虫配置示例文件
复制此文件为 crawl_config.py 并修改URL列表，然后运行 crawler.py
"""

from app.manual_faqs import MANUAL_FAQS

# ==========================================
# 1. 公开标准信息 (rag_standard)
# ==========================================
STANDARD_URLS = [
    "https://www.tongji.edu.cn/xxgk1/xxjj1.htm",  # 同济大学官网首页
    "https://www.tongji.edu.cn/xxgk1/xxzc.htm",
    "https://www.tongji.edu.cn/xxgk1/lsyg1.htm",
    "https://www.tongji.edu.cn/xxgk1/tjgl.htm",
    "https://www.tongji.edu.cn/xxgk1/xrld1.htm",
    "https://www.tongji.edu.cn/xxgk1/lrld1.htm",
    "https://news.tongji.edu.cn/",
]

# ==========================================
# 2. 学术科研信息 (rag_knowledge)
# ==========================================
ACADEMIC_URLS = [
    "https://www.tongji.edu.cn/info/1155/20365.htm",  # 科研成果与知识产权
    "https://www.tongji.edu.cn/info/1156/24322.htm",
    "https://www.tongji.edu.cn/info/1153/20345.htm", #科研项目概况
    "https://www.tongji.edu.cn/info/1153/20343.htm", #新立重大项目
    "https://www.tongji.edu.cn/info/1130/20349.htm", #政府批建机构
    "https://www.tongji.edu.cn/info/1130/20347.htm", #联合共建机构
    "https://www.tongji.edu.cn/info/1154/20355.htm", #科研地方合作
    "https://www.tongji.edu.cn/info/1154/20353.htm", #科研企业合作
    "https://www.tongji.edu.cn/info/1154/20351.htm", #科研国际合作
    "https://www.tongji.edu.cn/info/1154/20349.htm", #科研成果获奖
    "https://www.tongji.edu.cn/info/1155/20363.htm", #科研专利管理
    "https://www.tongji.edu.cn/info/1155/20361.htm", #科研技术转移
    "https://www.tongji.edu.cn/kxyj1/xsqk.htm",      #学术期刊
]

# ==========================================
# 3. 常见问题FAQ (rag_faq)
# ==========================================
# FAQ 的唯一维护位置是 app/manual_faqs.py，避免爬虫和应用各存一份导致重复。

# ==========================================
# 4. 爬取配置
# ==========================================
CRAWL_CONFIG = {
    "max_pages_standard": 50,  # 公开信息最大爬取页数（建议设置为URL数量或更大）
    "max_pages_academic": 50,  # 学术信息最大爬取页数（建议设置为URL数量或更大）
    "delay_seconds": 1,  # 每次请求延迟（秒）
    "chunk_size_standard": 1000,# 公开信息文本块大小
    "chunk_size_academic": 1000,  # 学术信息文本块大小
    "chunk_overlap": 100,  # 文本块重叠大小
}

