"""
API节点处理器
负责执行外部API调用，支持：
- GET/POST/PUT/DELETE 请求方法
- 自定义请求头
- JSON 请求体 / multipart/form-data 文件上传
- 变量插值
- 响应文件下载保存到文件管理
"""

import asyncio
import json
import logging
import uuid
from datetime import date
from typing import Any, Optional, TYPE_CHECKING

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field, create_model

from app.models.flow_node import FlowNode
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.agent_flow.handler_registry import NodeHandlerRegistry

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig

logger = logging.getLogger(__name__)

MAX_UPLOAD_FILE_SIZE = 50 * 1024 * 1024


def _write_bytes(path, content: bytes) -> None:
    """同步写入字节到文件（供 asyncio.to_thread 调用）"""
    with open(path, "wb") as f:
        f.write(content)


class ApiNodeConfig(BaseNodeConfig):
    output_variables: list[NodeVariable] = [
        NodeVariable(name="body"),
        NodeVariable(name="status_code", type="number"),
        NodeVariable(name="headers", type="object"),
    ]
    file_config: dict = {}
    api_url: Optional[str] = None
    method: Optional[str] = None
    headers: Optional[str] = None
    body: Optional[str] = None
    content_type: str = "application/json"
    form_fields: list[dict] = []
    description: Optional[str] = None
    use_preset_for_tool: bool = False


class ApiCallInput(BaseModel):
    """API调用工具输入参数"""

    api_url: str = Field(..., description="API请求地址，完整的URL")
    method: str = Field(
        default="GET", description="HTTP方法：GET、POST、PUT、DELETE、PATCH"
    )
    headers: str = Field(default="", description="请求头，JSON格式字符串")
    body: str = Field(default="", description="请求体，JSON格式字符串")
    upload_fields: str = Field(
        default="",
        description=(
            '上传文件配置，JSON数组。每项: {"field":"multipart字段名","file_ids":[文件ID列表],"file_paths":[文件绝对路径列表]}。'
            "file_ids 和 file_paths 至少填一个，可同时填写。"
            '示例: [{"field":"resume","file_ids":[42]},{"field":"photo","file_paths":["/tmp/img.png"]}]'
        ),
    )
    download_file: bool = Field(
        default=False, description="是否将响应作为文件下载保存到文件管理"
    )


@NodeHandlerRegistry.register("api")
class ApiNodeHandler(BaseNodeHandler):
    """
    API节点处理器

    功能：
    1. 从流程状态中解析变量并插值到URL/请求体
    2. 构建HTTP请求（支持GET/POST/PUT/DELETE）
    3. 支持 multipart/form-data 文件上传（通过 file_id 从文件管理读取）
    4. 支持响应文件下载保存到文件管理
    5. 将响应存储到指定的输出变量
    """

    ConfigClass = ApiNodeConfig

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        return True

    @classmethod
    def get_tool_singleton_config_field(cls) -> Optional[str]:
        return "use_preset_for_tool"

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """
        执行API节点

        Args:
            node: 节点对象，包含配置信息
            state: 当前流程状态
            config: LangGraph 运行配置

        Returns:
            更新后的流程状态
        """
        cfg = self._get_config(node)
        file_config = cfg.file_config

        input_data = self.__class__.get_input_content(
            node, state, self._resolver, node.base_config or {}
        )

        api_url = input_data.get("api_url") if input_data else None
        method = (input_data.get("method") or "GET").upper() if input_data else "GET"
        headers_str = input_data.get("headers", "") if input_data else ""
        body_str = input_data.get("body", "") if input_data else ""

        if not api_url:
            state.add_error(node.node_key, "API地址不能为空")
            return state

        headers = {}
        if headers_str:
            try:
                headers = json.loads(headers_str)
            except json.JSONDecodeError:
                state.add_error(node.node_key, "请求头JSON格式无效")
                return state

        # ---- 解析 body ----
        body = None
        if method in ["POST", "PUT", "PATCH"] and body_str:
            try:
                body = body_str
                if body_str.strip().startswith("{"):
                    body = json.loads(body)
            except json.JSONDecodeError:
                body = body_str

        # ---- 解析上传文件 ----
        files: list[tuple[str, str, bytes, str]] = []
        upload_fields_config = file_config.get("upload_fields", [])
        if upload_fields_config and isinstance(upload_fields_config, list):
            for field_item in upload_fields_config:
                if not isinstance(field_item, dict):
                    continue
                field_name = (field_item.get("field_name") or "file").strip()
                file_ids = field_item.get("file_ids", [])
                if isinstance(file_ids, list):
                    loaded = await self._load_files(file_ids)
                    for fname, content, mime in loaded:
                        files.append((field_name, fname, content, mime))

        if files:
            headers.pop("Content-Type", None)

        # ---- 判断是否需要原始响应（用于文件下载）----
        download_config = file_config.get("download", {})
        raw_response = bool(download_config.get("enabled", False))

        try:
            result = await self._call_api(
                api_url,
                method,
                headers,
                body,
                files=files if files else None,
                raw_response=raw_response,
            )
            if "error" in result:
                state.add_error(node.node_key, result["error"])
            elif result.get("_is_file"):
                # ---- 文件下载：保存到文件管理 ----
                file_info = await self._save_response_file(
                    result["_content"],
                    result.get("_content_type", ""),
                    result.get("_suggested_name", ""),
                )
                if file_info:
                    state.set_node_variable(node.node_key, "downloaded_file", file_info)
                else:
                    state.add_error(node.node_key, "文件下载保存失败")
            else:
                output_names = self._get_output_var_names(
                    node, ["body", "status_code", "headers"]
                )
                body_name = output_names[0] if len(output_names) > 0 else "body"
                status_name = (
                    output_names[1] if len(output_names) > 1 else "status_code"
                )
                headers_name = output_names[2] if len(output_names) > 2 else "headers"
                state.set_node_variable(node.node_key, body_name, result.get("data"))
                state.set_node_variable(
                    node.node_key, status_name, result.get("status_code")
                )
                state.set_node_variable(
                    node.node_key, headers_name, dict(result.get("headers", {}))
                )
        except Exception as e:
            state.add_error(node.node_key, f"API调用失败: {str(e)}")

        return state

    # ---- 文件加载 ----

    async def _load_files(self, file_ids: list[int]) -> list[tuple[str, bytes, str]]:
        """
        通过 file_id 从文件管理读取文件

        Args:
            file_ids: 文件ID列表

        Returns:
            (文件名, 文件内容, mime_type) 列表
        """
        from app.config.database import AsyncSessionLocal
        from app.services.file_service import file_service

        result: list[tuple[str, bytes, str]] = []
        async with AsyncSessionLocal() as db:
            for fid in file_ids:
                try:
                    (
                        path,
                        original_name,
                        mime_type,
                    ) = await file_service.get_download_path(db, fid)
                    if not path.exists():
                        logger.warning("文件不存在: file_id=%d, path=%s", fid, path)
                        continue
                    file_size = path.stat().st_size
                    if file_size > MAX_UPLOAD_FILE_SIZE:
                        logger.warning("文件过大: file_id=%d, size=%d", fid, file_size)
                        continue
                    content = path.read_bytes()
                    result.append((original_name, content, mime_type))
                except (FileNotFoundError, Exception) as e:
                    logger.warning("读取文件失败: file_id=%d, error=%s", fid, e)
        return result

    async def _resolve_upload_fields(
        self, upload_fields: str
    ) -> list[tuple[str, str, bytes, str]]:
        """
        解析 upload_fields JSON 配置，返回 [(field_name, 文件名, 字节, mime_type)]

        Args:
            upload_fields: JSON数组，每项含 field + file_id/file_path

        Returns:
            (字段名, 文件名, 文件内容, mime_type) 列表
        """
        try:
            items = json.loads(upload_fields)
        except json.JSONDecodeError:
            logger.warning("upload_fields JSON 解析失败: %s", upload_fields)
            return []

        if not isinstance(items, list):
            return []

        result: list[tuple[str, str, bytes, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            field_name = (item.get("field") or "file").strip()
            file_ids = item.get("file_ids")
            file_paths = item.get("file_paths")

            if file_ids and isinstance(file_ids, list):
                try:
                    ids = [int(x) for x in file_ids if isinstance(x, (int, float, str))]
                    loaded = await self._load_files(ids)
                    for fname, content, mime in loaded:
                        result.append((field_name, fname, content, mime))
                except (ValueError, TypeError):
                    logger.warning("file_ids 格式错误: %s", file_ids)

            if file_paths and isinstance(file_paths, list):
                path_str = ",".join(str(p) for p in file_paths if p)
                if path_str:
                    loaded = self._load_files_by_paths(path_str)
                    for fname, content, mime in loaded:
                        result.append((field_name, fname, content, mime))

        return result

    @staticmethod
    def _load_files_by_paths(file_paths: str) -> list[tuple[str, bytes, str]]:
        """
        通过绝对路径读取文件

        Args:
            file_paths: 逗号分隔的绝对路径字符串

        Returns:
            (文件名, 文件内容, mime_type) 列表
        """
        import mimetypes
        from pathlib import Path

        result: list[tuple[str, bytes, str]] = []
        for raw_path in file_paths.split(","):
            raw_path = raw_path.strip()
            if not raw_path:
                continue
            try:
                path = Path(raw_path)
                if not path.exists() or not path.is_file():
                    logger.warning("文件不存在或不是文件: %s", raw_path)
                    continue
                file_size = path.stat().st_size
                if file_size > MAX_UPLOAD_FILE_SIZE:
                    logger.warning("文件过大: path=%s, size=%d", raw_path, file_size)
                    continue
                content = path.read_bytes()
                mime_type = (
                    mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                )
                result.append((path.name, content, mime_type))
            except Exception as e:
                logger.warning("读取文件失败: path=%s, error=%s", raw_path, e)
        return result

    # ---- 文件保存 ----

    async def _save_response_file(
        self, content: bytes, content_type: str, suggested_name: str
    ) -> Optional[dict]:
        """
        将下载的文件保存到文件管理

        Args:
            content: 文件内容字节
            content_type: 响应的 Content-Type
            suggested_name: 建议的文件名

        Returns:
            文件信息字典，失败返回 None
        """
        from app.config.database import AsyncSessionLocal
        from app.config.settings import settings
        from app.models.file import File

        ext = self._guess_ext_from_content_type(content_type, suggested_name)
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        today = date.today().isoformat()
        relative_path = f"{settings.upload_dir}/api_download/{today}/{unique_name}"
        absolute_path = settings.get_absolute_path(relative_path)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            await asyncio.to_thread(_write_bytes, absolute_path, content)

            original_name = suggested_name or f"downloaded.{ext}"
            if not original_name.endswith(f".{ext}"):
                original_name = f"{original_name}.{ext}"

            async with AsyncSessionLocal() as db:
                file_obj = File(
                    source_type="api_download",
                    original_name=original_name,
                    file_path=relative_path,
                    file_type=ext,
                    file_size=len(content),
                    mime_type=content_type,
                )
                db.add(file_obj)
                await db.commit()
                await db.refresh(file_obj)

            return {
                "file_id": file_obj.id,
                "original_name": file_obj.original_name,
                "file_type": file_obj.file_type,
                "file_size": file_obj.file_size,
                "mime_type": file_obj.mime_type,
                "download_url": f"/api/file/download/{file_obj.id}",
            }
        except Exception as e:
            logger.error("保存下载文件失败: %s", e)
            return None

    @staticmethod
    def _guess_ext_from_content_type(content_type: str, suggested_name: str) -> str:
        """根据 Content-Type 或建议文件名推断扩展名"""
        ct = content_type.lower().split(";")[0].strip() if content_type else ""
        mime_to_ext = {
            "application/pdf": "pdf",
            "application/zip": "zip",
            "application/x-rar-compressed": "rar",
            "application/x-7z-compressed": "7z",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
            "application/msword": "doc",
            "application/vnd.ms-excel": "xls",
            "application/octet-stream": "bin",
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/svg+xml": "svg",
            "image/bmp": "bmp",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/ogg": "ogg",
            "audio/flac": "flac",
            "audio/aac": "aac",
            "video/mp4": "mp4",
            "video/webm": "webm",
            "video/avi": "avi",
            "video/quicktime": "mov",
            "text/csv": "csv",
            "text/xml": "xml",
            "text/html": "html",
        }
        if ct in mime_to_ext:
            return mime_to_ext[ct]
        if suggested_name and "." in suggested_name:
            return suggested_name.rsplit(".", 1)[-1].lower()[:10] or "bin"
        return "bin"

    @staticmethod
    def _is_file_content_type(content_type: str) -> bool:
        """判断响应 Content-Type 是否为文件类型（非 JSON/text）"""
        if not content_type:
            return False
        ct = content_type.lower().split(";")[0].strip()
        if ct.startswith("application/json"):
            return False
        if ct.startswith("text/"):
            return False
        if ct.startswith("application/"):
            return ct not in (
                "application/json",
                "application/xml",
                "application/x-www-form-urlencoded",
            )
        if (
            ct.startswith("image/")
            or ct.startswith("audio/")
            or ct.startswith("video/")
        ):
            return True
        return False

    # ---- HTTP 请求 ----

    async def _call_api(
        self,
        url: str,
        method: str,
        headers: dict,
        body: Any = None,
        files: Optional[list[tuple[str, str, bytes, str]]] = None,
        timeout: float = 30.0,
        raw_response: bool = False,
    ) -> dict:
        """
        发送HTTP请求

        Args:
            url: 请求地址
            method: 请求方法
            headers: 请求头
            body: 请求体（JSON 模式）或额外 form fields（multipart 模式）
            files: 上传文件列表 [(field_name, 文件名, 字节, mime_type)]
            timeout: 超时时间（秒）
            raw_response: 是否返回原始响应（用于文件下载）

        Returns:
            包含响应内容的字典，失败时包含error字段
            raw_response=True 时额外包含 _content, _content_type, _suggested_name, _is_file
        """
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                kwargs: dict[str, Any] = {"url": url, "headers": headers}

                if files and method in ["POST", "PUT", "PATCH"]:
                    # ---- multipart/form-data 模式 ----
                    form_data: dict[str, Any] = {}
                    if isinstance(body, dict):
                        form_data = body
                    upload_files: list[tuple[str, tuple[str, bytes, str]]] = []
                    for field_name, fname, content, mime in files:
                        upload_files.append((field_name, (fname, content, mime)))
                    kwargs["data"] = form_data
                    kwargs["files"] = upload_files
                elif body and method in ["POST", "PUT", "PATCH"]:
                    if isinstance(body, dict):
                        kwargs["json"] = body
                    else:
                        kwargs["content"] = body

                response = await client.request(method, **kwargs)

                if raw_response:
                    return self._build_raw_result(response)

                try:
                    response_data = response.json()
                except json.JSONDecodeError:
                    response_data = response.text

                return {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "data": response_data,
                    "success": 200 <= response.status_code < 300,
                }

        except httpx.TimeoutException:
            return {"error": f"请求超时（{timeout}秒）"}
        except httpx.RequestError as e:
            return {"error": f"请求失败: {str(e)}"}
        except Exception as e:
            return {"error": f"未知错误: {str(e)}"}

    def _build_raw_result(self, response: httpx.Response) -> dict:
        """构建原始响应结果（用于文件下载判断）"""
        content_type = response.headers.get("content-type", "")
        is_file = self._is_file_content_type(content_type)
        suggested_name = ""
        disposition = response.headers.get("content-disposition", "")
        if disposition and "filename=" in disposition:
            parts = disposition.split("filename=")
            if len(parts) > 1:
                suggested_name = parts[1].strip('"').strip("'")

        result: dict[str, Any] = {
            "status_code": response.status_code,
            "success": 200 <= response.status_code < 300,
            "_content_type": content_type,
            "_suggested_name": suggested_name,
            "_is_file": False,
        }

        if is_file:
            result["_is_file"] = True
            result["_content"] = response.content
        else:
            try:
                result["data"] = response.json()
            except json.JSONDecodeError:
                result["data"] = response.text

        return result

    # ---- 工具模式（LLM 调用）----

    async def get_tool(self, node: FlowNode) -> Optional[StructuredTool]:
        """
        返回API调用工具

        use_preset_for_tool=True 时：使用节点已配置的 api_url/method/headers/body，
        LLM 只需提供 input_variables 定义的业务参数，看不到技术细节。
        use_preset_for_tool=False 时：通用 api_call_tool，LLM 填写全部参数。
        """
        cfg = self._get_config(node)

        if cfg.use_preset_for_tool:
            return self._build_preset_tool(node, cfg)

        handler = self

        async def call_api(
            api_url: str,
            method: str = "GET",
            headers: str = "",
            body: str = "",
            upload_fields: str = "",
            download_file: bool = False,
        ) -> str:
            return await handler._call_api_json(
                api_url,
                method,
                headers,
                body,
                upload_fields=upload_fields,
                download_file=download_file,
            )

        return StructuredTool(
            name="api_call_tool",
            description="API调用",
            func=None,
            coroutine=call_api,
            args_schema=ApiCallInput,
        )

    def _build_preset_tool(
        self, node: FlowNode, cfg: ApiNodeConfig
    ) -> Optional[StructuredTool]:
        """构建预设参数的API工具，LLM 只提供业务参数"""
        import re

        handler = self
        input_variables = cfg.input_variables

        # ---- 动态构建 args_schema ----
        fields: dict[str, tuple[type, Any]] = {}
        for var in input_variables:
            name = var.get("name", "")
            if not name:
                continue
            var_type = var.get("type", "string")
            if var_type == "number":
                py_type = float
                default = Field(default=0.0, description=name)
            elif var_type == "boolean":
                py_type = bool
                default = Field(default=False, description=name)
            else:
                py_type = str
                default = Field(default="", description=name)
            fields[name] = (py_type, default)

        if not fields:
            fields["_dummy"] = (str, Field(default="", description="忽略"))

        ToolInput = create_model(
            f"{node.node_key}_api_input", __base__=BaseModel, **fields
        )

        tool_name = (
            (node.node_name or node.node_key)
            .strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
        )
        description = cfg.description or f"调用 {node.node_name or node.node_key} API"

        async def call_preset_api(**kwargs) -> str:
            context = {k: v for k, v in kwargs.items() if k != "_dummy"}

            def _simple_render(template: str) -> str:
                if not template:
                    return template

                def replacer(match):
                    key = match.group(1).strip()
                    return str(context.get(key, match.group(0)))

                return re.sub(r"\{\{(\w+)\}\}", replacer, template)

            rendered_url = _simple_render(cfg.api_url or "")
            rendered_headers = _simple_render(cfg.headers or "")
            rendered_body = _simple_render(cfg.body or "")
            return await handler._call_api_json(
                rendered_url,
                cfg.method or "GET",
                rendered_headers,
                rendered_body,
            )

        return StructuredTool(
            name=tool_name,
            description=description,
            func=None,
            coroutine=call_preset_api,
            args_schema=ToolInput,
        )

    async def _call_api_json(
        self,
        url: str,
        method: str,
        headers: str,
        body: str,
        upload_fields: str = "",
        download_file: bool = False,
    ) -> str:
        """
        执行API调用并返回JSON字符串（供工具调用）

        Args:
            url: 请求地址
            method: 请求方法
            headers: 请求头JSON字符串
            body: 请求体JSON字符串
            upload_fields: 上传文件配置JSON数组
            download_file: 是否下载响应文件

        Returns:
            JSON格式的响应结果
        """
        request_headers = {}
        if headers:
            try:
                request_headers = json.loads(headers)
            except json.JSONDecodeError:
                pass

        request_body = None
        if method in ["POST", "PUT", "PATCH"] and body:
            request_body = body
            if body.strip().startswith("{"):
                try:
                    request_body = json.loads(body)
                except json.JSONDecodeError:
                    pass

        # ---- 解析 upload_fields ----
        files: list[tuple[str, str, bytes, str]] = []
        if upload_fields:
            files = await self._resolve_upload_fields(upload_fields)

        if files:
            request_headers.pop("Content-Type", None)

        result = await self._call_api(
            url,
            method,
            request_headers,
            request_body,
            files=files if files else None,
            raw_response=download_file,
        )

        if download_file and result.get("_is_file"):
            file_info = await self._save_response_file(
                result["_content"],
                result.get("_content_type", ""),
                result.get("_suggested_name", ""),
            )
            if file_info:
                return json.dumps(
                    {"success": True, "downloaded_file": file_info},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"success": False, "error": "文件下载保存失败"},
                ensure_ascii=False,
            )

        # 清理内部字段
        return json.dumps(
            {k: v for k, v in result.items() if not k.startswith("_")},
            ensure_ascii=False,
        )

    # ---- 输入/输出内容 ----

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """获取API节点的输入内容"""
        if config is None:
            config = node.base_config or {}
        input_data = {}

        input_vars = config.get("input_variables", [])
        context = {}
        for var in input_vars:
            name = var.get("name", "")
            source = var.get("source", "")
            if name and source:
                context[name] = resolver.resolve_safe(source, state)

        if config.get("api_url"):
            raw_url = config.get("api_url")
            input_data["api_url"] = resolver.render_template(raw_url, state, context)
        if config.get("method"):
            input_data["method"] = config.get("method")
        if config.get("headers"):
            input_data["headers"] = resolver.render_template(
                config.get("headers"), state, context
            )
        if config.get("body"):
            input_data["body"] = resolver.render_template(
                config.get("body"), state, context
            )

        return input_data if input_data else None

    @classmethod
    def get_output_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        """获取API节点的输出内容"""
        if config is None:
            config = node.base_config or {}
        output = {}

        output_vars = config.get("output_variables", [])
        if output_vars:
            for var in output_vars:
                name = (
                    var.get("name", "")
                    if isinstance(var, dict)
                    else getattr(var, "name", "")
                )
                if name:
                    value = state.get_node_variable(node.node_key, name)
                    if value is not None:
                        output[name] = value
        else:
            value = state.get_node_variable(node.node_key, "api_result")
            if value is not None:
                output["api_result"] = value

        # 文件下载变量也纳入输出
        file_value = state.get_node_variable(node.node_key, "downloaded_file")
        if file_value is not None:
            output["downloaded_file"] = file_value

        return output if output else None

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将API节点配置添加到工具配置"""
        config.api_node_keys.append(node.node_key)
        node_config = node.base_config or {}
        config.api_configs[node.node_key] = {
            "name": node.node_name or "API调用",
            "description": node_config.get(
                "description",
                "调用外部API接口，需要提供完整的URL、HTTP方法、请求头和请求体",
            ),
        }
        return True
