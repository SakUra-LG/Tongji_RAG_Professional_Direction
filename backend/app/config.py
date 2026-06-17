import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # API Keys
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")

    # Models
    EMBEDDING_MODEL: str = "text-embedding-v4"
    REWRITE_MODEL_NAME: str = "qwen-flash"
    GENERATE_MODEL_NAME: str = "qwen3-max"

    # JWT Config
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-before-production")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7))
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost,http://127.0.0.1")

    # Redis Config
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    REDIS_REFRESH_PREFIX: str = "rt:"

    # MySQL Config
    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", 3306))
    MYSQL_USER: str = os.getenv("MYSQL_USER", "rag_user")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "rag_secret")
    MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "tongji_rag_db")

    # Milvus Config
    MILVUS_HOST: str = os.getenv("MILVUS_HOST", "localhost")
    MILVUS_PORT: str = os.getenv("MILVUS_PORT", "19530")
    
    COLLECTION_FAQ: str = "rag_faq"
    COLLECTION_STANDARD: str = "rag_standard"
    COLLECTION_KNOWLEDGE: str = "rag_knowledge"
    COLLECTION_INTERNAL: str = "rag_internal"
    COLLECTION_PERSONAL: str = "rag_person_info"

    GLOBAL_SEED: int = 1234

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

settings = Settings()
