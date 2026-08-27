"""
file_read 工具实现

以多模态/多格式统一读取本地文件并返回模型友好的结构化结果：
- 图片/PDF/音频：按模型媒体能力自动注入下一轮对话（media_caps 门控）
- xlsx/docx：调用 document_processor 转为 markdown 全文，再按行/字符分段返回
- 普通文本：带行号返回，字节封顶与下游截断器同源透传

不支持的输入（网络 URL、视频、.xls/.doc 旧格式）均返回明确错误引导。
"""

from pathlib import Path
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent_flow.tools.common import (
    MAX_FILE_SIZE,
    detect_and_read,
    has_utf16_bom,
    validate_file_path,
)
from app.config.settings import settings
from app.utils.document_processor import document_processor

MAX_FILE_READ_LINE_LENGTH = 2000


def _file_read_caps() -> tuple[int, int]:
    """file_read 单次返回封顶值

    与下游 smart_truncate_output 阈值（tool_output_truncate.py）同源透传：
    直接引用同一份全局配置，内部封顶 == 下游截断阈值，字段级检查用严格大于号
    且作用于原始字符串，故 file_read 结果恒可透传、不会触发二次截断。
    若未来截断器 dict 路径增加 1× 序列化总量闸门，此处需重新预留 JSON 转义余量。

    Returns:
        (字节上限, 行数上限)
    """
    return (settings.tool_output_max_bytes, settings.tool_output_max_lines)


def _shrink_end_to_bytes(text: str, start: int, end: int, max_bytes: int) -> int:
    """收缩 end 直到 text[start:end] 的 UTF-8 字节数不超过 max_bytes"""
    while end > start and len(text[start:end].encode("utf-8")) > max_bytes:
        over = len(text[start:end].encode("utf-8")) - max_bytes
        end = max(start, end - max(1, over // 3 + 1))
    return end


def _is_binary_sample(sample: bytes) -> bool:
    """按前几 KB 采样判断文件是否为二进制：含 NUL 字节即判二进制；
    否则不可打印控制字符占比 >30% 判二进制"""
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    non_printable = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return non_printable / len(sample) > 0.3


def _truncate_line_length(line: str) -> str:
    """单行超长截断（防止内嵌 base64 / 压缩 JS 等单行超长内容撑爆上下文）"""
    if len(line) <= MAX_FILE_READ_LINE_LENGTH:
        return line
    return (
        line[:MAX_FILE_READ_LINE_LENGTH]
        + f"... (line truncated to {MAX_FILE_READ_LINE_LENGTH} chars)"
    )


class FileReadInput(BaseModel):
    """文件读取工具输入参数"""

    file_path: str = Field(..., description="文件绝对路径")
    offset: Optional[int] = Field(
        None, description="起始行号（从1开始），与start_char互斥"
    )
    limit: Optional[int] = Field(
        None,
        description=(
            f"读取行数，单次最多 {settings.tool_output_max_lines} 行，"
            "不传则读取上限行数，与end_char互斥"
        ),
    )
    start_char: Optional[int] = Field(
        None,
        description="起始字符位置（0-indexed，包含），与offset互斥，适合读取单行大文件的特定片段",
    )
    end_char: Optional[int] = Field(
        None,
        description=(
            "结束字符位置（0-indexed，不包含），与limit互斥，不传则读取到文件末尾。"
            "单次读取有字节/行数封顶，超出会被截断并返回 truncated_hint 提示续读位置"
        ),
    )


class FileReadService:
    """file_read 工具服务

    每次 get_tool 时用当前节点的 media_caps 构造；read() 返回值即工具输出，
    含媒体注入标记（由 llm_tool_executor 收集后构建多模态 HumanMessage）。
    """

    def __init__(self, media_caps: set):
        self._media_caps = media_caps or set()

    # ---- L1 文档提取：xlsx/docx 转 markdown 全文 ----

    def _try_extract_document(self, path: Path) -> Optional[dict]:
        """识别可转换的文档类型并提取文本

        Returns:
            需要拒绝时返回错误 dict（.xls/.doc 等旧格式）；
            成功转换返回 {"raw": 文本, "ext": 扩展名}；
            非文档类型返回 None（继续走后续通用分支）
        """
        ext = path.suffix.lower().lstrip(".")
        if ext == "xls":
            return {
                "success": False,
                "error": "暂不支持旧版 .xls 格式，请先另存为 .xlsx 后读取",
            }
        if ext == "doc":
            return {
                "success": False,
                "error": "暂不支持旧版 .doc 格式，请先另存为 .docx 后读取",
            }
        if ext not in ("docx", "xlsx"):
            return None

        raw = document_processor.extract_text_from_bytes(path.read_bytes(), ext)
        return {"raw": raw, "ext": ext}

    async def read(
        self,
        file_path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        start_char: Optional[int] = None,
        end_char: Optional[int] = None,
    ) -> str | dict:
        stripped_path = (file_path or "").strip()
        if stripped_path.startswith(("http://", "https://")):
            return {
                "success": False,
                "error": (
                    "不支持直接读取网络资源，请先用 shell_executor"
                    "（curl/wget）下载到本地后再读取本地路径"
                ),
            }

        is_valid, error_msg = validate_file_path(file_path)
        if not is_valid:
            return {"error": error_msg, "success": False}

        path = Path(file_path).resolve()
        if not path.exists():
            return {"error": f"文件不存在: {file_path}", "success": False}
        if not path.is_file():
            return {"error": f"路径不是文件: {file_path}", "success": False}

        file_size = path.stat().st_size

        # ---- L1 文档提取：xlsx/docx 自动转文本（在媒体/二进制分支之前截胡）----
        doc_result = self._try_extract_document(path)
        raw = None
        document_ext = None
        if isinstance(doc_result, dict):
            if doc_result.get("success") is False:
                return doc_result
            raw = doc_result["raw"]
            document_ext = doc_result["ext"]

        # ---- L2 媒体自动注入：图片/音频/PDF 以多模态注入下一轮对话 ----
        if raw is None:
            from app.utils.media_resolver import (
                MAX_FILE_SIZE as MEDIA_MAX_FILE_SIZE,
                _classify_by_ext,
            )

            capability = _classify_by_ext(path.name)
            # 仅在模型具备媒体能力时才走媒体分支；否则视为普通文件，
            # 由下方二进制拦截兜底（避免对不支持视觉的模型提示抽帧看图）
            if capability and self._media_caps:
                if capability == "video":
                    return {
                        "success": False,
                        "error": "视频文件暂不支持查看，可用 shell/ffmpeg 抽帧为图片后读取",
                    }
                if capability in self._media_caps:
                    if file_size > MEDIA_MAX_FILE_SIZE:
                        return {
                            "success": False,
                            "error": (
                                f"媒体文件过大（{file_size / 1024 / 1024:.1f}MB），"
                                f"注入上限 {MEDIA_MAX_FILE_SIZE // 1024 // 1024}MB"
                            ),
                        }
                    return {
                        "success": True,
                        "file_path": str(path),
                        "media_type": capability,
                        "injected": True,
                        "message": "媒体内容已注入，将在下一轮对话中可见",
                    }
                return {
                    "success": False,
                    "error": f"当前模型不支持{capability}输入，无法查看该文件",
                }

            if file_size > MAX_FILE_SIZE:
                return {
                    "error": f"文件过大（{file_size} 字节），最大支持 {MAX_FILE_SIZE} 字节",
                    "success": False,
                }

            # ---- L3 二进制拦截：前 4KB 采样检测，避免二进制被当文本读成乱码 ----
            # UTF-16 BOM 文件（PowerShell 重定向产物）含大量 NUL 字节，属文本白名单
            try:
                with open(path, "rb") as fb:
                    sample = fb.read(4096)
                if _is_binary_sample(sample) and not has_utf16_bom(sample):
                    return {
                        "success": False,
                        "error": (
                            f"Cannot read binary file: {path}"
                            "（二进制文件不适合按文本读取）。"
                            "如需提取信息，请用 file_write 写入 python 脚本后"
                            "用 shell_executor 执行处理"
                        ),
                    }
            except OSError as e:
                return {"error": f"文件读取失败: {e}", "success": False}

            try:
                raw, _encoding = detect_and_read(path)
            except Exception as e:
                return {"error": f"文件读取失败: {e}", "success": False}

        total_chars = len(raw)
        cap_bytes, cap_lines = _file_read_caps()

        def _base_result() -> dict:
            r: dict = {"success": True, "file_path": str(path)}
            if document_ext:
                r["content_type"] = "document"
                r["ext"] = document_ext
            return r

        # ---- L4 字符模式 ----
        if start_char is not None:
            if offset is not None:
                return {
                    "error": "start_char 和 offset 不能同时使用，请选择一种模式",
                    "success": False,
                }
            s = max(0, start_char)
            e = min(end_char, total_chars) if end_char is not None else total_chars

            # 单次读取双维度封顶；续读位置始终基于原始文本，不受展示层截断影响
            requested_e = e
            e = _shrink_end_to_bytes(raw, s, e, cap_bytes)
            content = raw[s:e]
            if content.count("\n") >= cap_lines:
                content = "\n".join(content.split("\n")[:cap_lines])
                e = s + len(content)
            truncated_by_limit = e < requested_e
            line_cut = any(
                len(ln) > MAX_FILE_READ_LINE_LENGTH for ln in content.split("\n")
            )
            display = "\n".join(_truncate_line_length(ln) for ln in content.split("\n"))
            result = _base_result()
            result.update(
                {
                    "total_chars": total_chars,
                    "start_char": s,
                    "end_char": e,
                    "content": display,
                }
            )
            if truncated_by_limit:
                result["truncated_hint"] = (
                    f"(Output capped at {cap_bytes // 1024}KB / {cap_lines} lines. "
                    f"Use start_char={e} to continue.)"
                )
            elif line_cut:
                result["truncated_hint"] = (
                    f"部分行超过 {MAX_FILE_READ_LINE_LENGTH} 字符仅展示前缀，"
                    "如需完整内容请用 start_char/end_char 按字符位置精确读取"
                )
            if e < total_chars:
                result["has_more"] = True
            return result

        # ---- L4 行模式 ----
        lines = raw.splitlines()
        total_lines = len(lines)
        actual_limit = min(limit, cap_lines) if limit else cap_lines
        start = (offset - 1) if offset and offset >= 1 else 0
        end = min(start + actual_limit, len(lines))
        selected = lines[start:end]

        # 单行大文件：提示改用字符模式分段读取
        if total_lines == 1 and len(raw.encode("utf-8")) > cap_bytes:
            line_end = _shrink_end_to_bytes(raw, 0, total_chars, cap_bytes)
            content = _truncate_line_length(f"1: {raw[:line_end]}")
            result = _base_result()
            result.update(
                {
                    "total_lines": total_lines,
                    "total_chars": total_chars,
                    "offset": 1,
                    "limit": 1,
                    "content": content,
                    "truncated_hint": (
                        f"(Output capped at {cap_bytes // 1024}KB."
                        "单行大文件请用 start_char 和 end_char 参数按字符位置分段读取)"
                    ),
                }
            )
            return result

        # 逐行累加字节封顶（含行号前缀，先做单行展示截断再计入预算），超限提前停止
        content_lines: list[str] = []
        byte_count = 0
        for i, line in enumerate(selected):
            formatted = _truncate_line_length(f"{start + i + 1}: {line}")
            line_size = len(formatted.encode("utf-8")) + (1 if content_lines else 0)
            if byte_count + line_size > cap_bytes:
                break
            content_lines.append(formatted)
            byte_count += line_size
        returned = len(content_lines)
        result = _base_result()
        result.update(
            {
                "total_lines": total_lines,
                "total_chars": total_chars,
                "offset": start + 1,
                "limit": returned,
                "content": "\n".join(content_lines),
            }
        )
        if start + returned < total_lines:
            result["has_more"] = True
            next_offset = start + returned + 1
            if returned < len(selected):
                if returned == 0:
                    result["truncated_hint"] = (
                        f"(第 {start + 1} 行超过单次上限 {cap_bytes // 1024}KB，"
                        "请用 start_char 和 end_char 参数读取该行片段)"
                    )
                else:
                    result["truncated_hint"] = (
                        f"(Output capped at {cap_bytes // 1024}KB. Showing lines "
                        f"{start + 1}-{start + returned}. "
                        f"Use offset={next_offset} to continue.)"
                    )
            else:
                result["hint"] = (
                    f"(Showing lines {start + 1}-{start + returned} of {total_lines}. "
                    f"Use offset={next_offset} to continue.)"
                )
        return result

    def build_tool(self) -> StructuredTool:
        """构造 file_read StructuredTool（description 动态引用全局配置）"""
        return StructuredTool(
            name="file_read",
            description=(
                "读取本地文件内容。图片/PDF/音频文件会自动以多模态形式注入下一轮对话"
                "（模型支持时）；xlsx/docx 自动转为文本（markdown 表格/标题）读取；"
                "不支持网络 URL、视频和 .xls/.doc 旧格式。\n"
                "1. 行模式（默认）：返回带行号的文本(格式如 ```12: 文件内容的一行```)，"
                f"单次默认读取 {settings.tool_output_max_lines} 行、总字节封顶约 "
                f"{settings.tool_output_max_bytes // 1024}KB，超限提前停止并提示续读 offset。\n"
                "2. 字符模式：传 start_char/end_char 按字符位置读取（0-indexed），"
                "适合读取单行大文件的特定片段，不传 end_char 则读取到文件末尾，"
                "超出封顶会在 truncated_hint 中提示续读的 start_char。\n"
                "两种模式互斥（offset 与 start_char 不能同时传）。"
                "超过 2000 字符的单行仅展示前缀。读取前无需校验文件是否存在。"
            ),
            func=None,
            coroutine=self.read,
            args_schema=FileReadInput,
        )
