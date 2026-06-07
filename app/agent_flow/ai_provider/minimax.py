"""MiniMax 提供商（API 路径与 OpenAI 不兼容，媒体部分使用 httpx）

官方文档：https://platform.minimaxi.com/docs/api-reference/api-overview
- 图片：POST /v1/image_generation（response_format: base64/url）
- 音乐：POST /v1/music_generation（同步返回 hex 编码音频）
- 视频：POST /v1/video_generation → GET /v1/query/video_generation → GET /v1/files/retrieve
"""

import base64

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from app.agent_flow.ai_provider.base import (
    AIProviderRegistry,
    BaseAIProvider,
    MediaGenFieldDef,
    MediaResult,
)


class MiniMaxImageToolInput(BaseModel):
    """MiniMax 图片生成工具参数"""

    prompt: str = Field(..., description="图片描述，越详细越好，最长 1500 字符")
    aspect_ratio: str = Field(
        default="1:1",
        description="图片宽高比。可选值: 1:1, 16:9, 4:3, 3:2, 2:3, 3:4, 9:16, 21:9",
    )
    reference_file_id: int = Field(
        default=0,
        description="参考图片的文件 ID（用于图生图）。提供后将基于参考图片中的人物生成新图片，传 0 或不传则为文生图",
    )


class MiniMaxAudioToolInput(BaseModel):
    """MiniMax 音乐生成工具参数"""

    prompt: str = Field(
        ...,
        description="音乐风格描述，包括风格、情绪、场景等。例如'流行音乐, 难过, 适合在下雨的晚上'",
    )
    lyrics: str = Field(
        default="",
        description="歌曲歌词，用换行符分隔每行。支持结构标签如 [Verse]、[Chorus]、[Bridge] 等。留空且 lyrics_optimizer=true 时自动生成",
    )
    is_instrumental: bool = Field(
        default=False,
        description="是否生成纯音乐（无人声）。设为 true 时 lyrics 可留空",
    )
    lyrics_optimizer: bool = Field(
        default=False,
        description="是否根据 prompt 自动生成歌词。仅当 lyrics 为空时生效",
    )


@AIProviderRegistry.register("minimax")
class MiniMaxProvider(BaseAIProvider):
    name = "minimax"
    label = "MiniMax"
    default_base_url = "https://api.minimaxi.com/anthropic"
    supports_image = True
    supports_audio = True
    supports_video = False

    def __init__(self, api_key: str, base_url: str):
        super().__init__(api_key, base_url or self.default_base_url)
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.generate_base_url = "https://api.minimaxi.com"

    def create_chat_model(self, model: str, **kwargs) -> BaseChatModel:
        base_url = self.base_url or self.default_base_url
        return init_chat_model(
            model_provider="anthropic",
            model=model,
            api_key=self.api_key,
            base_url=base_url,
            **kwargs,
        )

    def get_image_tool_schema(self):
        return MiniMaxImageToolInput

    def get_audio_tool_schema(self):
        return MiniMaxAudioToolInput

    # ---- 图片生成 ----

    async def generate_image(
        self,
        prompt: str,
        model: str = "image-01",
        *,
        aspect_ratio: str = "1:1",
        reference_file_id: int = 0,
        **_kwargs,
    ) -> MediaResult:
        """
        通过 MiniMax 图片生成 API 生成图片（支持文生图和图生图）

        文档：
        - 文生图：https://platform.minimaxi.com/docs/api-reference/image-generation-t2i
        - 图生图：https://platform.minimaxi.com/docs/api-reference/image-generation-i2i
        """
        url = f"{self.generate_base_url}/v1/image_generation"
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "response_format": "base64",
            "n": 1,
        }

        if reference_file_id:
            image_file = await self._resolve_file_to_data_url(reference_file_id)
            payload["subject_reference"] = [
                {"type": "character", "image_file": image_file}
            ]

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=payload, headers=self._headers)
            resp.raise_for_status()
            data = resp.json()

        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            raise RuntimeError(
                f"MiniMax 图片生成失败: {base_resp.get('status_msg', '未知错误')}"
            )

        img_data = data.get("data", {})

        b64_list = img_data.get("image_base64", [])
        if b64_list:
            content = base64.b64decode(b64_list[0])
            return MediaResult(
                content=content,
                mime_type="image/png",
                file_ext="png",
                file_name="generated_image.png",
            )

        url_list = img_data.get("image_urls", [])
        if url_list:
            image_url = url_list[0]
            content = await self._download_file(image_url)
            return MediaResult(
                url=image_url,
                content=content,
                mime_type="image/png",
                file_ext="png",
                file_name="generated_image.png",
            )

        raise ValueError(f"MiniMax 图片生成未返回有效数据: {data}")

    async def _resolve_file_to_data_url(self, file_id: int) -> str:
        """根据 file_id 从数据库查询文件路径，读取并转为 base64 Data URL"""

        from app.config.settings import settings
        from app.config.database import AsyncSessionLocal
        from app.services.file_service import file_service

        async with AsyncSessionLocal() as db:
            file_obj = await file_service.get_by_id(db, file_id)
            if not file_obj:
                raise FileNotFoundError(f"参考图片不存在: file_id={file_id}")

        abs_path = settings.get_absolute_path(file_obj.file_path)
        if not abs_path.exists():
            raise FileNotFoundError(f"参考图片文件不存在: {abs_path}")

        ext = abs_path.suffix.lstrip(".").lower()
        mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}
        mime_type = mime_map.get(ext, "jpeg")
        content = abs_path.read_bytes()
        b64 = base64.b64encode(content).decode()
        return f"data:image/{mime_type};base64,{b64}"

    # ---- 音乐生成 ----

    async def generate_audio(
        self,
        prompt: str = "",
        model: str = "music-2.6-free",
        *,
        lyrics: str = "",
        is_instrumental: bool = False,
        lyrics_optimizer: bool = False,
        **_kwargs,
    ) -> MediaResult:
        """
        通过 MiniMax 音乐生成 API 生成音乐

        文档：https://platform.minimaxi.com/docs/api-reference/music-generation
        同步返回，data.audio 为 hex 编码的音频数据（output_format=hex）
        data.status=2 表示已完成
        """
        url = f"{self.generate_base_url}/v1/music_generation"
        payload = {
            "model": model,
            "prompt": prompt,
            "lyrics": lyrics or None,
            "is_instrumental": is_instrumental,
            "lyrics_optimizer": lyrics_optimizer,
            "output_format": "hex",
            "stream": False,
            "audio_setting": {
                "sample_rate": 44100,
                "bitrate": 256000,
                "format": "mp3",
            },
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=payload, headers=self._headers)
            resp.raise_for_status()
            data = resp.json()

        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            raise RuntimeError(
                f"MiniMax 音乐生成失败: {base_resp.get('status_msg', '未知错误')}"
            )

        music_data = data.get("data", {})
        status = music_data.get("status")
        if status == 2:
            hex_str = music_data.get("audio", "")
            if hex_str:
                content = bytes.fromhex(hex_str)
                return MediaResult(
                    content=content,
                    mime_type="audio/mpeg",
                    file_ext="mp3",
                    file_name="generated_music.mp3",
                )

        raise ValueError(f"MiniMax 音乐生成未返回有效数据: {data}")

    async def _download_file(self, url: str) -> bytes:
        """下载远程文件内容"""
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.content

    @classmethod
    def get_media_gen_fields(cls, media_type: str):
        if media_type == "image":
            return [
                MediaGenFieldDef(
                    "prompt",
                    "提示词",
                    "textarea",
                    "",
                    True,
                    placeholder="图片描述，越详细越好，最长 1500 字符",
                    description="图片描述，越详细越好，最长 1500 字符",
                ),
                MediaGenFieldDef(
                    "aspect_ratio",
                    "宽高比",
                    "text",
                    "1:1",
                    False,
                    options=["1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"],
                    placeholder="如 1:1、16:9，支持变量引用",
                    description="图片宽高比，可选值：1:1, 16:9, 4:3, 3:2, 2:3, 3:4, 9:16, 21:9",
                ),
                MediaGenFieldDef(
                    "reference_file_id",
                    "参考图ID",
                    "text",
                    "",
                    False,
                    placeholder="留空为文生图，填入文件ID为图生图，支持变量引用",
                    description="参考图片的文件 ID（用于图生图）",
                ),
            ]
        elif media_type == "audio":
            return [
                MediaGenFieldDef(
                    "prompt",
                    "风格描述",
                    "textarea",
                    "",
                    True,
                    placeholder="音乐风格描述，如'流行音乐, 难过, 适合在下雨的晚上'",
                    description="音乐风格描述，包括风格、情绪、场景等",
                ),
                MediaGenFieldDef(
                    "lyrics",
                    "歌词",
                    "textarea",
                    "",
                    False,
                    placeholder="用 [Verse]/[Chorus] 标签，留空可自动生成",
                    description="歌曲歌词，支持结构标签",
                ),
                MediaGenFieldDef(
                    "is_instrumental",
                    "纯音乐",
                    "switch",
                    False,
                    False,
                    description="是否生成纯音乐（无人声）",
                ),
                MediaGenFieldDef(
                    "lyrics_optimizer",
                    "自动填词",
                    "switch",
                    False,
                    False,
                    description="是否根据风格描述自动生成歌词",
                ),
            ]
        return []
