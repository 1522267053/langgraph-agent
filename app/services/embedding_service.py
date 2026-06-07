"""
Embedding 服务

使用 LangChain OpenAIEmbeddings 进行文本向量化。
配置优先级：global_config DB > .env 环境变量 > 降级（noop）
"""

import logging
import threading
from typing import TYPE_CHECKING, Callable, List, Optional

from langchain_openai import OpenAIEmbeddings

from app.config.settings import settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Embedding 服务类，基于 LangChain OpenAIEmbeddings"""

    MAX_BATCH_SIZE = 10
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0

    def __init__(self):
        self.api_key = settings.embedding_api_key
        self.base_url = settings.embedding_base_url.rstrip("/")
        self.model = settings.embedding_model
        self.max_batch_size: int = settings.embedding_batch_size
        self._enabled = bool(self.api_key and self.base_url)
        self._embeddings: Optional[OpenAIEmbeddings] = None

    def _get_embeddings(self) -> Optional[OpenAIEmbeddings]:
        """获取或懒创建 LangChain OpenAIEmbeddings 实例"""
        if not self._enabled:
            return None
        if self._embeddings is None:
            kwargs: dict = {
                "model": self.model,
                "openai_api_key": self.api_key,
                "openai_api_base": self.base_url,
                "check_embedding_ctx_length": False,
                "model_kwargs": {"encoding_format": "float"},
            }
            self._embeddings = OpenAIEmbeddings(**kwargs)
        return self._embeddings

    def _invalidate_embeddings(self) -> None:
        """配置变更后使 embeddings 实例失效，下次调用时重建"""
        self._embeddings = None

    async def _init_from_db(self) -> None:
        """从 DB 加载 embedding 配置（覆盖 .env 默认值）"""
        try:
            from app.config.database import AsyncSessionLocal
            from app.services.global_config_service import global_config_service

            async with AsyncSessionLocal() as db:
                cfg = await global_config_service.get_embedding_config(db)

            if cfg.get("api_key"):
                self.api_key = cfg["api_key"]
            if cfg.get("base_url"):
                self.base_url = cfg["base_url"].rstrip("/")
            if cfg.get("model"):
                self.model = cfg["model"]
            self._enabled = bool(self.api_key and self.base_url)
            self._invalidate_embeddings()
        except Exception:
            pass

    def is_available(self) -> bool:
        """向量模型是否已配置可用"""
        return self._enabled

    async def embed_texts(
        self,
        texts: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[List[float]]:
        """批量文本向量化"""
        if not texts or not self._enabled:
            return []

        embeddings = self._get_embeddings()
        if embeddings is None:
            return []

        total = len(texts)
        all_embeddings: List[List[float]] = []

        for i in range(0, total, self.max_batch_size):
            batch = texts[i : i + self.max_batch_size]
            batch_embeddings = await self._embed_batch_with_retry(batch, embeddings)
            all_embeddings.extend(batch_embeddings)
            completed = min(i + self.max_batch_size, total)
            if progress_callback and (completed % 50 == 0 or completed >= total):
                progress_callback(completed, total)

        return all_embeddings

    async def embed_query(self, query: str) -> List[float]:
        """单条查询向量化"""
        if not self._enabled:
            return []
        embeddings = self._get_embeddings()
        if embeddings is None:
            return []
        return await embeddings.aembed_query(query)

    async def _embed_batch_with_retry(
        self, texts: List[str], embeddings: OpenAIEmbeddings
    ) -> List[List[float]]:
        """带重试的批量向量化"""
        import asyncio

        last_error: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return await embeddings.aembed_documents(texts)
            except Exception as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))

        raise ValueError(
            f"Embedding 失败（重试 {self.MAX_RETRIES} 次后）: {last_error}"
        )


embedding_service: Optional[EmbeddingService] = None
_init_lock = threading.Lock()
_initialized = False


def get_embedding_service() -> EmbeddingService:
    """获取 Embedding 服务单例（同步，不含 DB 配置）"""
    global embedding_service
    if embedding_service is None:
        with _init_lock:
            if embedding_service is None:
                embedding_service = EmbeddingService()
    return embedding_service


async def get_embedding_service_async() -> EmbeddingService:
    """获取 Embedding 服务单例（异步，从 DB 加载配置）"""
    global embedding_service, _initialized
    svc = get_embedding_service()
    if not _initialized:
        with _init_lock:
            if not _initialized:
                await svc._init_from_db()
                _initialized = True
    return svc


def reset_embedding_service() -> None:
    """重置 Embedding 服务，下次获取时从 DB 重新加载配置"""
    global _initialized
    _initialized = False
