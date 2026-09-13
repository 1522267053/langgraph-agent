"""
调试 API 路由

提供节点/工具脚本的独立运行端点，与流程中真实执行路径共享核心沙箱逻辑，
但刻意隔离副作用（不写 File DB、不调文件追踪）以避免污染生产环境。

当前端点：
- POST /api/debug/python：Python 节点 / LLM tool_check_script 试运行
"""

import base64
import re
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agent_flow.node_handlers.python_handler import _run_python_in_sandbox
from app.schemas.base_schema import ApiResponse
from app.utils.debug_file import save_debug_bytes

router = APIRouter(prefix="/api/debug", tags=["调试"])

# debug_session_id 合法字符：字母/数字/下划线/连字符（UUID v4 默认格式安全）
_DEBUG_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def _validate_debug_session_id(value: str) -> str:
    """校验 debug_session_id 防止路径穿越或异常字符"""
    if not value or not _DEBUG_SESSION_ID_PATTERN.match(value):
        # 兜底生成 UUID 避免阻断调试流程
        return uuid.uuid4().hex
    return value


def _make_json_safe(value: Any) -> Any:
    """递归将不可 JSON 序列化的对象降级为 repr 字符串。

    沙箱内用户的 main() 可能返回任何 Python 对象（包括 type 对象如 bool/str、
    自定义类实例等）。PythonDebugResponse.result: Any 仍会被 Pydantic 序列化，
    遇到 type 对象会抛 PydanticSerializationError("Unable to serialize unknown type")。

    为保证试运行接口永不被用户脚本搞 500，对 result 做递归清洗：
    - dict / list / tuple：递归处理每个元素
    - str / int / float / bool / None：直接返回（基础可序列化类型）
    - bytes：转 base64 字符串（避免丢内容）
    - 其他：fallback 为 repr(value) 并标注类型名
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_make_json_safe(v) for v in value]
    if isinstance(value, bytes):
        # bytes 转 base64 字符串保留内容
        return {"__bytes_b64__": base64.b64encode(value).decode("ascii")}
    # 兜底：不可序列化对象（type、类实例、生成器等）→ repr + 类型标记
    return f"<non-serializable {type(value).__name__}: {repr(value)[:200]}>"


class PythonDebugRequest(BaseModel):
    """Python 试运行请求体"""

    code: str = Field(
        ..., description="Python 代码（含 main 函数定义）", max_length=200_000
    )
    timeout: int = Field(30, ge=5, le=300, description="超时时间（秒）")
    input_data: dict[str, Any] = Field(
        default_factory=dict, description="输入变量字典（main 函数参数值）"
    )
    debug_session_id: str = Field(
        ..., description="调试会话 ID（前端 localStorage 持久化）"
    )


class PythonDebugResponse(BaseModel):
    """Python 试运行响应"""

    stdout: str = ""
    stderr: str = ""
    result: Any = None
    success: bool = True


@router.post(
    "/python",
    response_model=ApiResponse[PythonDebugResponse],
    summary="Python 脚本试运行",
    description="在 RestrictedPython 沙箱中独立执行 Python 代码，与流程中真实执行共享沙箱核心逻辑，但 __save_file__ 落盘到独立临时目录且不写数据库。",
)
async def debug_python(req: PythonDebugRequest) -> ApiResponse[PythonDebugResponse]:
    """执行 Python 调试代码并返回结果"""
    debug_session_id = _validate_debug_session_id(req.debug_session_id)

    # 调用核心沙箱（与生产路径 _execute_python 共享同一函数）
    result = await _run_python_in_sandbox(req.code, req.input_data, float(req.timeout))

    # 调试路径处理 __save_file__：写盘但不污染 DB 与文件追踪
    if result["success"]:
        exec_result = result["result"]
        if isinstance(exec_result, dict) and exec_result.get("__save_file__"):
            try:
                content_b64 = exec_result.get("content_base64", "")
                mime_type = exec_result.get("mime_type", "application/octet-stream")
                filename = exec_result.get("filename", "")
                if content_b64:
                    content = base64.b64decode(content_b64)
                    file_info = await save_debug_bytes(
                        content, debug_session_id, mime_type, filename
                    )
                    # 把 file_info 合并进 result.result，让前端可直接渲染 preview_url
                    result["result"] = {
                        "__save_file__": True,
                        "__file_info__": file_info,
                        "__raw__": exec_result,
                    }
            except Exception as e:
                # 文件保存失败不应阻断试运行结果返回
                result["result"] = {
                    "__save_file__": True,
                    "__file_error__": f"文件保存失败: {e}",
                    "__raw__": exec_result,
                }

    response = PythonDebugResponse(
        stdout=result["stdout"],
        stderr=result["stderr"],
        result=_make_json_safe(result["result"]),
        success=result["success"],
    )
    return ApiResponse.success(data=response, msg="试运行完成")
