"""
MCP客户端管理器

管理MCP服务器连接和工具调用。
通过持久化 MCP session 实现 stdio 子进程跨工具调用复用，
通过 per-server 锁保证同一服务器的工具调用串行执行。
每个连接由独立后台 Task 管理生命周期，确保 anyio cancel scope
的 __aenter__/__aexit__ 在同一 Task 中执行。
"""

import asyncio
import logging
import sys
import threading
import time
from typing import Any, Optional
from datetime import datetime

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer, McpToolCache
from app.schemas.mcp_server_schema import McpToolInfo

logger = logging.getLogger(__name__)

CLOSE_TIMEOUT = 10
MCP_CONNECT_TIMEOUT = 30
MCP_TOOL_CALL_TIMEOUT = 120
MCP_REMOTE_TOOL_CALL_TIMEOUT = 60
MAX_CONNECTION_AGE = 600
STDIO_CONSECUTIVE_TIMEOUT_LIMIT = 2


class McpConnectionError(Exception):
    """MCP连接错误"""

    pass


def _get_tool_schema(tool) -> dict | None:
    """
    获取工具的 JSON Schema

    兼容两种情况：
    - args_schema 是 dict（直接是 JSON Schema）
    - args_schema 是 Pydantic 模型（需要调用 model_json_schema()）
    """
    if not tool.args_schema:
        return None
    if isinstance(tool.args_schema, dict):
        return tool.args_schema
    if hasattr(tool.args_schema, "model_json_schema"):
        return tool.args_schema.model_json_schema()
    return None


def _extract_error_message(exc: Exception) -> str:
    """从异常中提取错误信息，处理 ExceptionGroup 包装"""
    if sys.version_info >= (3, 11) and isinstance(exc, ExceptionGroup):
        return "; ".join(_extract_error_message(e) for e in exc.exceptions)
    return str(exc)


class ConnectionHolder:
    """
    MCP 连接生命周期持有者

    每个连接由一个独立后台 asyncio Task 管理，该 Task 负责执行
    create_session 的 __aenter__ 和 __aexit__，确保 anyio cancel scope
    在同一个 Task 中正确进出。

    使用方式：
    - 调用方通过 close_event 通知后台 Task 退出
    - 后台 Task 退出时自动执行 __aexit__ 清理子进程
    """

    def __init__(
        self,
        session: Any,
        close_event: asyncio.Event,
        lifecycle_task: asyncio.Task,
    ):
        self.session = session
        self.close_event = close_event
        self.lifecycle_task = lifecycle_task
        self.created_at = time.monotonic()

    def age(self) -> float:
        """返回连接空闲时间（秒），成功调用后会刷新"""
        return time.monotonic() - self.created_at

    def touch(self) -> None:
        """刷新连接活跃时间"""
        self.created_at = time.monotonic()

    async def close(self) -> None:
        """通知后台 Task 退出并等待清理完成"""
        self.close_event.set()
        try:
            await asyncio.wait_for(self.lifecycle_task, timeout=CLOSE_TIMEOUT)
        except asyncio.TimeoutError:
            self.lifecycle_task.cancel()
            try:
                await self.lifecycle_task
            except asyncio.CancelledError:
                pass
        except Exception:
            logger.warning("关闭MCP连接后台任务异常", exc_info=True)


class McpToolManager:
    """
    MCP工具管理器（单例模式）

    管理 MCP 服务器连接和工具获取。
    - 持久化 session：stdio 子进程在多次工具调用之间保持存活
    - per-server 锁：同一服务器的工具调用串行执行，避免 stdin/stdout 冲突
    - 异常自愈：工具调用失败时自动断开旧连接，下次 get_tools 时自动重建
    - 连接老化：超过 MAX_CONNECTION_AGE 自动断开重建
    - stdio 超时保护：连续超时达阈值后自动断开连接
    """

    _instance: Optional["McpToolManager"] = None
    _instance_lock = threading.Lock()
    _connections: dict[int, ConnectionHolder] = {}
    _tools_cache: dict[int, list[BaseTool]] = {}
    _call_locks: dict[int, asyncio.Lock] = {}
    _lock: asyncio.Lock = asyncio.Lock()
    _consecutive_timeouts: dict[int, int] = {}

    def __new__(cls) -> "McpToolManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def _get_call_lock(self, server_id: int) -> asyncio.Lock:
        """获取指定 MCP 服务器的调用锁"""
        if server_id not in self._call_locks:
            self._call_locks[server_id] = asyncio.Lock()
        return self._call_locks[server_id]

    def _record_timeout(self, server_id: int) -> int:
        """记录超时次数并返回当前连续超时计数"""
        self._consecutive_timeouts[server_id] = (
            self._consecutive_timeouts.get(server_id, 0) + 1
        )
        return self._consecutive_timeouts[server_id]

    def _reset_timeouts(self, server_id: int) -> None:
        """重置连续超时计数"""
        self._consecutive_timeouts.pop(server_id, None)

    def _wrap_tool_with_lock(
        self,
        tool: BaseTool,
        server_id: int,
        transport: str = "",
        timeout: Optional[int] = None,
        server_name: str = "",
        keep_alive: bool = True,
    ) -> BaseTool:
        """
        包装 MCP 工具，添加 per-server 串行锁

        - 名称前缀：工具名改为 mcp__{server_name}__{tool_name}，避免不同服务器同名工具冲突
        - 串行锁：同一服务器的并发工具调用排队等待，避免 stdio 管道冲突
        - 异常自愈：仅传输级错误（管道断裂等）才断开连接，应用级错误保持 session 存活
        - 超时保护：stdio 类型连续超时达阈值时断开连接，非 stdio 类型每次超时都断开
        - 超时优先级：用户配置 > 默认值（stdio=120s, 远程=60s）
        - keep_alive=False 时，每次调用完成后自动关闭连接释放资源（如 xlsx 编辑）
        - 连接老化：超过 MAX_CONNECTION_AGE 时自动断开重建
        """
        original_coro = tool.coroutine
        is_stdio = transport == "stdio"

        async def locked_coro(**kwargs):
            lock = self._get_call_lock(server_id)
            async with lock:
                effective_timeout = (
                    timeout
                    if timeout is not None
                    else (
                        MCP_TOOL_CALL_TIMEOUT
                        if is_stdio
                        else MCP_REMOTE_TOOL_CALL_TIMEOUT
                    )
                )

                # ---- 连接老化检查 ----
                async with self._lock:
                    holder = self._connections.get(server_id)
                if holder and holder.age() > MAX_CONNECTION_AGE:
                    logger.info(
                        f"MCP服务器 {server_name} 连接已超过 {MAX_CONNECTION_AGE}s，自动断开重建"
                    )
                    await self._close_connection(server_id)
                    async with self._lock:
                        self._tools_cache.pop(server_id, None)

                try:
                    result = await asyncio.wait_for(
                        original_coro(**kwargs), timeout=effective_timeout
                    )
                except (
                    BrokenPipeError,
                    ConnectionError,
                    ConnectionResetError,
                    OSError,
                    httpx.RemoteProtocolError,
                    httpx.ConnectError,
                    httpx.ReadTimeout,
                ):
                    await self._close_connection(server_id)
                    async with self._lock:
                        self._tools_cache.pop(server_id, None)
                    raise
                except asyncio.TimeoutError:
                    self._reset_timeouts(server_id) if not is_stdio else None
                    should_disconnect = not is_stdio
                    if is_stdio:
                        count = self._record_timeout(server_id)
                        if count >= STDIO_CONSECUTIVE_TIMEOUT_LIMIT:
                            should_disconnect = True
                            logger.warning(
                                f"MCP服务器 {server_name} 连续超时 {count} 次，断开连接"
                            )
                            self._reset_timeouts(server_id)
                    if should_disconnect:
                        await self._close_connection(server_id)
                        async with self._lock:
                            self._tools_cache.pop(server_id, None)
                    raise TimeoutError(
                        f"MCP工具调用超时({effective_timeout}s)"
                        + ("，已断开连接" if should_disconnect else "")
                    )

                # 调用成功，重置超时计数 + 刷新连接活跃时间
                self._reset_timeouts(server_id)
                async with self._lock:
                    holder = self._connections.get(server_id)
                if holder:
                    holder.touch()

                if not keep_alive:
                    logger.debug(
                        f"MCP服务器 {server_name} keep_alive=False，调用后释放连接"
                    )
                    await self._close_connection(server_id)
                    async with self._lock:
                        self._tools_cache.pop(server_id, None)

                return result

        prefixed_name = f"mcp__{server_name}__{tool.name}" if server_name else tool.name

        return StructuredTool(
            name=prefixed_name,
            description=tool.description,
            args_schema=tool.args_schema,
            coroutine=locked_coro,
            response_format=getattr(tool, "response_format", "content_and_artifact"),
            metadata=getattr(tool, "metadata", None),
        )

    async def get_tools(
        self, db: AsyncSession, server_ids: list[int]
    ) -> list[BaseTool]:
        """
        获取多个MCP服务器的所有工具

        Args:
            db: 数据库会话
            server_ids: 服务器ID列表

        Returns:
            工具列表
        """
        tools: list[BaseTool] = []

        for server_id in server_ids:
            try:
                server_tools = await asyncio.wait_for(
                    self._get_server_tools(db, server_id),
                    timeout=MCP_CONNECT_TIMEOUT,
                )
                tools.extend(server_tools)
            except asyncio.TimeoutError:
                logger.warning(
                    f"MCP服务器 {server_id} 连接超时({MCP_CONNECT_TIMEOUT}s)，跳过"
                )
            except McpConnectionError as e:
                logger.warning(f"MCP服务器加载失败，跳过: {e}")

        return tools

    async def _get_server_tools(
        self, db: AsyncSession, server_id: int
    ) -> list[BaseTool]:
        """获取单个服务器的工具，优先使用已缓存的持久连接"""
        # ---- 快速路径：缓存命中且连接存活 ----
        cached_tools = None
        async with self._lock:
            if server_id in self._tools_cache and server_id in self._connections:
                holder = self._connections[server_id]
                if holder.age() <= MAX_CONNECTION_AGE:
                    cached_tools = self._tools_cache[server_id]

        if cached_tools is not None:
            from app.services.mcp_server_service import mcp_server_service

            disabled = await mcp_server_service.get_disabled_tool_names(db, server_id)
            if not disabled:
                return cached_tools
            return [
                t
                for t in cached_tools
                if t.name.replace("mcp__", "", 1).split("__", 1)[-1] not in disabled
            ]

        # ---- 慢路径：需要加载/重建 ----
        server = await self._get_server(db, server_id)
        if not server or not server.is_enabled:
            return []

        try:
            tools = await self._load_tools_from_server(db, server)
            return tools
        except Exception as e:
            raise McpConnectionError(
                f"无法从MCP服务器 {server.name} 加载工具: {str(e)}"
            )

    async def _get_server(
        self, db: AsyncSession, server_id: int
    ) -> Optional[McpServer]:
        """获取服务器配置"""
        from sqlalchemy import select

        query = select(McpServer).where(
            McpServer.id == server_id, McpServer.is_delete == 0
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def _get_server_config(
        self, db: AsyncSession, server_id: int
    ) -> dict[str, Any]:
        """获取服务器解析后的连接配置"""
        from app.services.mcp_server_service import mcp_server_service

        return await mcp_server_service.get_parsed_config(db, server_id)

    async def _start_connection(
        self, server: McpServer, connection: dict[str, Any]
    ) -> ConnectionHolder:
        """
        启动 MCP 连接的后台生命周期管理 Task

        创建一个独立的后台 asyncio Task 来持有 create_session 上下文，
        确保 __aenter__ 和 __aexit__ 在同一 Task 中执行，避免 anyio
        cancel scope 跨 Task 错误。

        Args:
            server: MCP 服务器模型
            connection: 已构建的连接配置（由 _build_connection 返回的 dict 部分）

        Returns:
            ConnectionHolder，包含 session 和关闭控制权
        """
        from langchain_mcp_adapters.sessions import create_session

        if server.id in self._connections:
            await self._close_connection(server.id)

        session_ctx = create_session(connection)

        ready_event = asyncio.Event()
        close_event = asyncio.Event()
        session_ref: list[Any] = [None]
        error_ref: list[Exception] = [None]

        async def _lifecycle():
            try:
                session = await session_ctx.__aenter__()
                await session.initialize()
                session_ref[0] = session
                ready_event.set()
                await close_event.wait()
            except Exception as e:
                error_ref[0] = e
                ready_event.set()
            finally:
                try:
                    await session_ctx.__aexit__(None, None, None)
                except Exception:
                    logger.warning(
                        f"关闭MCP连接失败: server_id={server.id}",
                        exc_info=True,
                    )

        task = asyncio.create_task(_lifecycle(), name=f"mcp-lifecycle-{server.id}")
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=MCP_CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            raise TimeoutError(
                f"MCP服务器 {server.name} 连接超时({MCP_CONNECT_TIMEOUT}s)"
            )

        if error_ref[0] is not None:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            raise error_ref[0]

        return ConnectionHolder(
            session=session_ref[0], close_event=close_event, lifecycle_task=task
        )

    async def _load_tools_from_server(
        self, db: AsyncSession, server: McpServer
    ) -> list[BaseTool]:
        """
        从MCP服务器加载工具，并保持 session 持久化

        通过后台 Task 管理 create_session 的上下文生命周期，
        使 stdio 子进程在多次工具调用之间保持存活。
        工具经过锁包装，保证同一服务器的调用串行执行。
        """
        from langchain_mcp_adapters.tools import load_mcp_tools

        config = await self._get_server_config(db, server.id)
        connection, timeout = self._build_connection(server.transport, config)
        holder = await self._start_connection(server, connection)
        tools = await load_mcp_tools(holder.session, server_name=server.name)

        wrapped_tools = [
            self._wrap_tool_with_lock(
                t,
                server.id,
                server.transport,
                timeout,
                server.name,
                bool(server.keep_alive),
            )
            for t in tools
        ]

        async with self._lock:
            self._connections[server.id] = holder
            self._tools_cache[server.id] = wrapped_tools

        await self._save_tools_cache(db, server.id, tools)

        from app.services.mcp_server_service import mcp_server_service

        disabled = await mcp_server_service.get_disabled_tool_names(db, server.id)
        if not disabled:
            return wrapped_tools
        prefix = f"mcp__{server.name}__"
        return [
            t for t in wrapped_tools if t.name not in {f"{prefix}{n}" for n in disabled}
        ]

    def _build_connection(
        self, transport: str, config: dict[str, Any]
    ) -> tuple[dict[str, Any], Optional[int]]:
        """构建MCP连接配置，提取超时时间并处理 args 拆分"""
        import shlex

        result: dict[str, Any] = {"transport": transport}
        timeout: Optional[int] = None
        for key, value in config.items():
            if key == "timeout":
                timeout = int(value) if value is not None else None
                continue
            if key == "args":
                if isinstance(value, list):
                    result["args"] = [str(a) for a in value]
                elif isinstance(value, str) and value.strip():
                    result["args"] = shlex.split(value)
                continue
            result[key] = value
        return result, timeout

    async def _close_connection(self, server_id: int) -> None:
        """关闭指定 MCP 服务器的持久连接，通过后台 Task 安全退出上下文"""
        async with self._lock:
            holder = self._connections.pop(server_id, None)
        if holder is not None:
            await holder.close()

    async def _save_tools_cache(
        self, db: AsyncSession, server_id: int, tools: list[BaseTool]
    ) -> None:
        """保存工具缓存到数据库，已有工具保留启用状态"""
        from sqlalchemy import delete as sa_delete
        from sqlalchemy import select

        current_names = {t.name for t in tools}

        await db.execute(
            sa_delete(McpToolCache).where(
                McpToolCache.server_id == server_id,
                McpToolCache.tool_name.notin_(current_names)
                if current_names
                else False,
            )
        )

        for tool in tools:
            cache = await db.execute(
                select(McpToolCache).where(
                    McpToolCache.server_id == server_id,
                    McpToolCache.tool_name == tool.name,
                    McpToolCache.is_delete == 0,
                )
            )
            existing_row = cache.scalar_one_or_none()
            if existing_row:
                existing_row.description = tool.description
                existing_row.tool_schema = _get_tool_schema(tool)
                existing_row.cached_at = datetime.now()
            else:
                new_cache = McpToolCache(
                    server_id=server_id,
                    tool_name=tool.name,
                    description=tool.description,
                    tool_schema=_get_tool_schema(tool),
                    cached_at=datetime.now(),
                )
                db.add(new_cache)

        await db.commit()

    async def test_connection(
        self, db: AsyncSession, server_id: int
    ) -> tuple[bool, list[McpToolInfo], Optional[str]]:
        """
        测试MCP服务器连接

        成功后保持连接存活并缓存工具，后续流程执行可直接复用。
        返回所有工具及其启用状态（不过滤禁用工具）。

        Returns:
            (是否成功, 工具列表, 错误信息)
        """
        from langchain_mcp_adapters.tools import load_mcp_tools
        from app.services.mcp_server_service import mcp_server_service

        server = await self._get_server(db, server_id)
        if not server:
            return False, [], "服务器不存在"

        # 读取已有的工具启用状态，刷新后保留
        old_enabled_map = await mcp_server_service.get_tool_enabled_map(db, server_id)

        await self._close_connection(server_id)
        self._tools_cache.pop(server_id, None)

        try:
            config = await self._get_server_config(db, server_id)
            connection, timeout = self._build_connection(server.transport, config)
            holder = await self._start_connection(server, connection)

            tools = await load_mcp_tools(holder.session, server_name=server.name)

            tool_infos = [
                McpToolInfo(
                    name=t.name,
                    description=t.description,
                    input_schema=_get_tool_schema(t),
                    is_enabled=old_enabled_map.get(t.name, 1),
                )
                for t in tools
            ]

            await self._save_tools_cache(db, server_id, tools)

            disabled = set(n for n, s in old_enabled_map.items() if s == 0)

            async with self._lock:
                self._connections[server_id] = holder
                self._tools_cache[server_id] = [
                    self._wrap_tool_with_lock(
                        t,
                        server_id,
                        server.transport,
                        timeout,
                        server.name,
                        bool(server.keep_alive),
                    )
                    for t in tools
                    if t.name not in disabled
                ]

            return True, tool_infos, None

        except Exception as e:
            logger.exception(e)
            await self._close_connection(server_id)
            return False, [], _extract_error_message(e)

    async def clear_cache(self, server_id: int) -> None:
        """清除服务器缓存并关闭连接"""
        await self._close_connection(server_id)
        async with self._lock:
            self._tools_cache.pop(server_id, None)
        self._consecutive_timeouts.pop(server_id, None)

    async def clear_all_cache(self) -> None:
        """清除所有缓存并关闭所有连接（应用关闭时调用）"""
        async with self._lock:
            connections_snapshot = dict(self._connections)
            self._connections.clear()
            self._tools_cache.clear()
        tasks = []
        for server_id, holder in connections_snapshot.items():
            tasks.append(holder.close())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._call_locks.clear()
        self._consecutive_timeouts.clear()


mcp_tool_manager = McpToolManager()
