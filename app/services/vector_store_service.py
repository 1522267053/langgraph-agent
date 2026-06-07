"""
向量存储服务

提供向量数据库的抽象层，便于切换不同的向量数据库
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import asyncio
import threading

from app.config.settings import settings


class VectorStoreService(ABC):
    """
    向量存储抽象基类

    定义向量存储的通用接口，便于切换不同的向量数据库
    """

    @abstractmethod
    async def add_texts(
        self,
        texts: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
        ids: List[str],
    ) -> List[str]:
        """
        添加向量和文本

        Args:
            texts: 文本列表
            embeddings: 向量列表
            metadatas: 元数据列表
            ids: ID 列表

        Returns:
            添加成功的 ID 列表
        """
        pass

    @abstractmethod
    async def similarity_search(
        self,
        query_embedding: List[float],
        k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        向量相似度搜索

        Args:
            query_embedding: 查询向量
            k: 返回结果数量
            filter: 过滤条件（如 knowledge_base_id）

        Returns:
            搜索结果列表，每项包含 id, text, metadata, distance
        """
        pass

    @abstractmethod
    async def delete(self, ids: List[str]) -> bool:
        """
        删除指定 ID 的向量

        Args:
            ids: 要删除的 ID 列表

        Returns:
            是否删除成功
        """
        pass

    @abstractmethod
    async def delete_by_document_id(self, document_id: int) -> bool:
        """
        按文档 ID 删除所有相关向量

        Args:
            document_id: 文档 ID

        Returns:
            是否删除成功
        """
        pass

    @abstractmethod
    async def delete_by_knowledge_base_id(self, knowledge_base_id: int) -> bool:
        """
        按知识库 ID 删除所有相关向量

        Args:
            knowledge_base_id: 知识库 ID

        Returns:
            是否删除成功
        """
        pass

    @abstractmethod
    async def get_by_id(self, id: str) -> Optional[Dict[str, Any]]:
        """
        根据 ID 获取向量记录

        Args:
            id: 向量 ID

        Returns:
            向量记录，包含 id, text, metadata, embedding
        """
        pass

    @abstractmethod
    async def count(self, filter: Optional[Dict[str, Any]] = None) -> int:
        """
        统计向量数量

        Args:
            filter: 过滤条件

        Returns:
            向量数量
        """
        pass


class ChromaVectorStoreService(VectorStoreService):
    """
    Chroma 向量存储实现

    使用 ChromaDB 作为向量数据库
    """

    def __init__(self, collection_name: Optional[str] = None):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self._persist_directory = settings.get_absolute_path(settings.vector_store_path)
        self._persist_directory.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=str(self._persist_directory),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )

        self._collection = self._client.get_or_create_collection(
            name=collection_name or settings.vector_collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    async def add_texts(
        self,
        texts: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
        ids: List[str],
    ) -> List[str]:
        """添加向量和文本"""
        if (
            len(texts) != len(embeddings)
            or len(texts) != len(metadatas)
            or len(texts) != len(ids)
        ):
            raise ValueError("texts, embeddings, metadatas, ids 长度必须一致")

        if not texts:
            return []

        await asyncio.to_thread(
            self._collection.add,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )

        return ids

    async def similarity_search(
        self,
        query_embedding: List[float],
        k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """向量相似度搜索"""
        where_filter = None
        if filter:
            where_filter = {k_: v for k_, v in filter.items()}

        results = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[query_embedding],
            n_results=k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        formatted_results = []
        if results and results.get("ids"):
            ids = results["ids"][0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for i, id_ in enumerate(ids):
                formatted_results.append(
                    {
                        "id": id_,
                        "text": documents[i] if i < len(documents) else "",
                        "metadata": metadatas[i] if i < len(metadatas) else {},
                        "distance": distances[i] if i < len(distances) else 0.0,
                    }
                )

        return formatted_results

    async def delete(self, ids: List[str]) -> bool:
        """删除指定 ID 的向量"""
        if not ids:
            return True

        await asyncio.to_thread(self._collection.delete, ids=ids)
        return True

    async def delete_by_document_id(self, document_id: int) -> bool:
        """按文档 ID 删除所有相关向量"""
        await asyncio.to_thread(
            self._collection.delete, where={"document_id": document_id}
        )
        return True

    async def delete_by_knowledge_base_id(self, knowledge_base_id: int) -> bool:
        """按知识库 ID 删除所有相关向量"""
        await asyncio.to_thread(
            self._collection.delete, where={"knowledge_base_id": knowledge_base_id}
        )
        return True

    async def get_by_id(self, id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取向量记录"""
        results = await asyncio.to_thread(
            self._collection.get,
            ids=[id],
            include=["documents", "metadatas", "embeddings"],
        )

        if results and results.get("ids"):
            return {
                "id": results["ids"][0],
                "text": results["documents"][0] if results.get("documents") else "",
                "metadata": results["metadatas"][0] if results.get("metadatas") else {},
                "embedding": results["embeddings"][0]
                if results.get("embeddings")
                else [],
            }

        return None

    async def count(self, filter: Optional[Dict[str, Any]] = None) -> int:
        """统计向量数量"""
        if filter:
            where_filter = {k: v for k, v in filter.items()}
            return await asyncio.to_thread(self._collection.count, where=where_filter)
        return await asyncio.to_thread(self._collection.count)


vector_store_service: Optional[VectorStoreService] = None
_init_lock = threading.Lock()


def get_vector_store_service() -> VectorStoreService:
    """
    获取向量存储服务单例

    根据配置返回对应的向量存储实现

    Returns:
        VectorStoreService 实例
    """
    global vector_store_service
    if vector_store_service is None:
        with _init_lock:
            if vector_store_service is None:
                if settings.vector_store_type == "chroma":
                    vector_store_service = ChromaVectorStoreService()
                else:
                    raise ValueError(
                        f"不支持的向量存储类型: {settings.vector_store_type}"
                    )
    return vector_store_service
