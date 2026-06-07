"""
媒体生成节点处理器

支持两种工作模式：
1. 独立执行模式：配置参数后直接执行，结果存入 state 变量供下游使用
2. 工具提供模式：通过 source_handle="tools" 连接到 LLM 节点，由 LLM 动态调用
"""

import asyncio
import json
import logging
import uuid
from datetime import date
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import StreamWriter

from app.agent_flow.ai_provider import MediaResult, create_provider
from app.agent_flow.flow_context import FlowState
from app.agent_flow.flow_event import NodeStartEvent
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeConfig,
    BaseNodeHandler,
    NodeVariable,
)
from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.models.file import File
from app.models.flow_node import FlowNode

logger = logging.getLogger(__name__)


def _write_bytes(path, content: bytes) -> None:
    """同步写入字节到文件（供 asyncio.to_thread 调用）"""
    with open(path, "wb") as f:
        f.write(content)


MEDIA_TYPE_LABELS = {"image": "图片", "audio": "音频", "video": "视频"}
MEDIA_TYPE_GENERATORS = {
    "image": "generate_image",
    "audio": "generate_audio",
    "video": "generate_video",
}
_MEDIA_TYPE_DEFAULTS = {
    "image": ("openai_compatible", "dall-e-3"),
    "audio": ("openai_compatible", "tts-1"),
    "video": ("minimax", "video-01"),
}


# ---- 文件保存 ----


async def save_generated_file(
    db_session_factory,
    result: MediaResult,
    flow_id: int = 0,
    source_type: str = "generation",
) -> File:
    """将生成的媒体保存到本地并写入数据库"""
    ext = result.file_ext or "bin"
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    today = date.today().isoformat()
    relative_path = f"{settings.upload_dir}/generated/{today}/{unique_name}"
    absolute_path = settings.get_absolute_path(relative_path)
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    await asyncio.to_thread(_write_bytes, absolute_path, result.content)

    async with db_session_factory() as db:
        file_obj = File(
            flow_id=flow_id,
            source_type=source_type,
            original_name=result.file_name or f"generated.{ext}",
            file_path=relative_path,
            file_type=ext,
            file_size=len(result.content),
            mime_type=result.mime_type,
        )
        db.add(file_obj)
        await db.commit()
        await db.refresh(file_obj)

    return file_obj


def _build_result(file_obj: File) -> str:
    """构建工具返回值（JSON 字符串）"""
    return json.dumps(
        {
            "success": True,
            "file_id": file_obj.id,
            "file_name": file_obj.original_name,
            "mime_type": file_obj.mime_type,
            "download_url": f"/api/file/download/{file_obj.id}",
            "preview_url": f"/{file_obj.file_path}",
            "message": "媒体文件已生成并保存",
        },
        ensure_ascii=False,
    )


# ---- 工具创建 ----


def _get_current_flow_id() -> int:
    """从执行上下文获取当前 flow_id"""
    from app.agent_flow.execution_context import get_execution_context

    ctx = get_execution_context()
    return ctx.flow_id if ctx else 0


def create_image_tool(
    config: dict,
    db_session_factory,
    tool_name: str = "generate_image",
) -> BaseTool:
    """创建图片生成工具"""
    provider = create_provider(
        config.get("provider", "openai_compatible"),
        config.get("api_key", ""),
        config.get("base_url", ""),
    )
    model = config.get("model", "dall-e-3")
    args_schema = provider.get_image_tool_schema()
    _flow_id = _get_current_flow_id()

    async def generate_image(**kwargs) -> str:
        try:
            result = await provider.generate_image(model=model, **kwargs)
            file_obj = await save_generated_file(
                db_session_factory, result, flow_id=_flow_id
            )
            return _build_result(file_obj)
        except Exception as e:
            logger.error(f"图片生成失败: {e}")
            return json.dumps(
                {"success": False, "error": f"图片生成失败: {str(e)}"},
                ensure_ascii=False,
            )

    return StructuredTool(
        name=tool_name,
        description="生成图片。支持文生图和图生图。当用户要求生成、创建、绘制图片时使用此工具。可通过 reference_file_id 参数传入已上传图片的文件 ID 实现图生图。",
        func=None,
        coroutine=generate_image,
        args_schema=args_schema,
    )


def create_audio_tool(
    config: dict,
    db_session_factory,
    tool_name: str = "generate_audio",
) -> BaseTool:
    """创建音频生成工具"""
    provider = create_provider(
        config.get("provider", "openai_compatible"),
        config.get("api_key", ""),
        config.get("base_url", ""),
    )
    model = config.get("model", "tts-1")
    args_schema = provider.get_audio_tool_schema()
    _flow_id = _get_current_flow_id()

    async def generate_audio(**kwargs) -> str:
        try:
            result = await provider.generate_audio(model=model, **kwargs)
            file_obj = await save_generated_file(
                db_session_factory, result, flow_id=_flow_id
            )
            return _build_result(file_obj)
        except Exception as e:
            logger.error(f"音频生成失败: {e}")
            return json.dumps(
                {"success": False, "error": f"音频生成失败: {str(e)}"},
                ensure_ascii=False,
            )

    return StructuredTool(
        name=tool_name,
        description="生成音乐/音频。当用户要求创作音乐、生成歌曲时使用此工具。可提供歌词和风格描述。",
        func=None,
        coroutine=generate_audio,
        args_schema=args_schema,
    )


def create_video_tool(
    config: dict,
    db_session_factory,
    tool_name: str = "generate_video",
) -> BaseTool:
    """创建视频生成工具"""
    provider = create_provider(
        config.get("provider", "minimax"),
        config.get("api_key", ""),
        config.get("base_url", ""),
    )
    model = config.get("model", "video-01")
    args_schema = provider.get_video_tool_schema()
    _flow_id = _get_current_flow_id()

    async def generate_video(**kwargs) -> str:
        try:
            result = await provider.generate_video(model=model, **kwargs)
            file_obj = await save_generated_file(
                db_session_factory, result, flow_id=_flow_id
            )
            return _build_result(file_obj)
        except Exception as e:
            logger.error(f"视频生成失败: {e}")
            return json.dumps(
                {"success": False, "error": f"视频生成失败: {str(e)}"},
                ensure_ascii=False,
            )

    return StructuredTool(
        name=tool_name,
        description="生成视频。当用户要求生成、创建视频时使用此工具。注意：视频生成可能需要数分钟时间。",
        func=None,
        coroutine=generate_video,
        args_schema=args_schema,
    )


class MediaGenConfig(BaseNodeConfig):
    output_variables: list[NodeVariable] = [
        NodeVariable(name="url"),
        NodeVariable(name="media_type"),
    ]
    media_type: str = "image"
    image: dict = {}
    audio: dict = {}
    video: dict = {}


# ---- 节点处理器 ----


@NodeHandlerRegistry.register("media_gen")
class MediaGenNodeHandler(BaseNodeHandler):
    """
    媒体生成节点处理器

    支持图片、音频、视频生成。可独立执行或作为 LLM 工具使用。
    """

    ConfigClass = MediaGenConfig

    def check_config(
        self,
        config: dict,
        node_key: str,
        state: FlowState,
        writer: Optional[StreamWriter] = None,
    ) -> dict | None:
        """校验媒体生成必填配置"""
        media_type = config.get("media_type", "image")
        type_config = config.get(media_type, {})
        type_label = MEDIA_TYPE_LABELS.get(media_type, media_type)

        if not type_config.get("enabled"):
            msg = f"{type_label}生成未启用"
            state.add_error(node_key, msg)
            self._emit_error(writer, node_key, msg)
            return None

        api_key = self._require_config(
            type_config, "api_key", node_key, f"{type_label} API Key", state, writer
        )
        if not api_key:
            return None

        model = self._require_config(
            type_config, "model", node_key, f"{type_label}模型", state, writer
        )
        if not model:
            return None

        return {
            "media_type": media_type,
            "type_config": type_config,
            "api_key": api_key,
            "model": model,
            "provider_name": type_config.get("provider", "openai_compatible"),
            "base_url": type_config.get("base_url", ""),
            "type_label": type_label,
        }

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """独立执行模式：按 media_type 指定的类型生成，结果存入 state"""
        cfg = self._get_config(node)

        checked = self.check_config(
            node.base_config or {}, node.node_key, state, writer
        )
        if not checked:
            return state

        media_type = checked["media_type"]
        type_config = checked["type_config"]
        api_key = checked["api_key"]
        model = checked["model"]
        provider_name = checked["provider_name"]
        base_url = checked["base_url"]
        type_label = checked["type_label"]

        context = self._resolve_input_variables(cfg.input_variables, state)

        input_data = self.get_input_content(node, state, self._resolver)
        self._emit(
            writer,
            NodeStartEvent(
                node_key=node.node_key,
                node_type=node.node_type,
                node_name=node.node_name,
                input_data=input_data if input_data else None,
            ),
        )

        try:
            provider = create_provider(provider_name, api_key, base_url)
        except Exception as e:
            state.add_error(
                node.node_key,
                f"{type_label} 创建 Provider 失败: {e}",
            )
            return state

        gen_kwargs = self._build_gen_kwargs(media_type, type_config, state, context)

        try:
            generator = getattr(
                provider, MEDIA_TYPE_GENERATORS.get(media_type, "generate_image")
            )
            result = await generator(model=model, **gen_kwargs)
            file_obj = await save_generated_file(
                AsyncSessionLocal, result, flow_id=_get_current_flow_id()
            )
            result_url = f"/{file_obj.file_path}"
            output_names = self._get_output_var_names(node, ["url", "media_type"])
            url_name = output_names[0] if len(output_names) > 0 else "url"
            media_type_name = output_names[1] if len(output_names) > 1 else "media_type"
            state.set_node_variable(node.node_key, url_name, result_url)
            state.set_node_variable(node.node_key, media_type_name, media_type)
        except NotImplementedError:
            state.add_error(
                node.node_key,
                f"[{provider_name}] 不支持{MEDIA_TYPE_LABELS.get(media_type, media_type)}生成",
            )
        except Exception as e:
            state.add_error(
                node.node_key,
                f"{MEDIA_TYPE_LABELS.get(media_type, media_type)}生成失败: {e}",
            )

        return state

    _NUMERIC_PARAMS = {"reference_file_id"}

    def _build_gen_kwargs(
        self,
        media_type: str,
        type_config: dict,
        state: FlowState,
        context: dict | None = None,
    ) -> dict:
        """从 type_config.params 读取所有参数，递归解析模板 + context 自动填充"""
        params = type_config.get("params", {})
        if not isinstance(params, dict):
            return {}

        resolved = self._resolve_config(params, state, context)

        for key in self._NUMERIC_PARAMS:
            if key in resolved:
                try:
                    resolved[key] = int(resolved[key])
                except (ValueError, TypeError):
                    pass

        return {
            k: v
            for k, v in resolved.items()
            if v is not None and v != "" and v != 0 and v != "0"
        }

    async def get_tool(
        self, node: FlowNode
    ) -> Optional[BaseTool] | list[BaseTool] | None:
        """工具提供模式：返回已启用媒体类型的生成工具列表"""
        cfg = self._get_config(node)
        node_key = node.node_key
        db_session_factory = AsyncSessionLocal
        tools: list[BaseTool] = []

        for media_type, factory in [
            ("image", create_image_tool),
            ("audio", create_audio_tool),
            ("video", create_video_tool),
        ]:
            type_config = getattr(cfg, media_type, {})
            if (
                type_config.get("enabled")
                and type_config.get("provider")
                and type_config.get("api_key")
            ):
                tool_name = f"{MEDIA_TYPE_GENERATORS[media_type]}_{node_key}"
                tool = factory(type_config, db_session_factory, tool_name=tool_name)
                tools.append(tool)

        return tools if tools else None

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """注入媒体生成工具的使用提示"""
        cfg = self._get_config(node)
        capabilities = []
        for media_type in ("image", "audio", "video"):
            type_config = getattr(cfg, media_type, {})
            if (
                type_config.get("enabled")
                and type_config.get("provider")
                and type_config.get("api_key")
            ):
                tool_name = f"{MEDIA_TYPE_GENERATORS[media_type]}_{node.node_key}"
                capabilities.append(f"{MEDIA_TYPE_LABELS[media_type]}({tool_name})")

        if not capabilities:
            return None

        hint = f"\n## 媒体生成工具\n你具备{', '.join(capabilities)}生成能力，生成后用工具返回的 preview_url 以 markdown 展示，例如：[标题](/uploads/generated/2026-05-05/xxxx.png)。"
        image_cfg = getattr(cfg, "image", {})
        if image_cfg.get("enabled"):
            hint += "图片支持 reference_file_id 参数实现图生图。"
        return hint

    @classmethod
    def get_input_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
        if config is None:
            config = node.base_config or {}

        context = resolver.resolve_all(config.get("input_variables", []), state)

        result = {}
        for media_type in MEDIA_TYPE_GENERATORS:
            type_config = config.get(media_type, {})
            if not type_config.get("enabled"):
                continue
            info: dict = {"provider": type_config.get("provider", "")}
            params = type_config.get("params", {})
            if isinstance(params, dict):
                info.update(resolver.resolve_config(params, state, context))
            result[media_type] = info

        return result if result else None

    @classmethod
    def get_output_content(
        cls,
        node: FlowNode,
        state: FlowState,
        resolver,
        config: Optional[dict] = None,
    ) -> Optional[dict]:
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
            value = state.get_node_variable(node.node_key, "media_result")
            if value is not None:
                output["media_result"] = value

        return output if output else None
