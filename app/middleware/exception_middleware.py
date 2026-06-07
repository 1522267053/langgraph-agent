"""
全局异常处理器中间件
所有异常统一返回 HTTP 200，通过 ApiResponse.code 区分错误类型
"""

import logging
import traceback
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent_flow.exceptions import (
    CheckpointError,
    FlowValidationError,
    MaxIterationsExceededException,
    NodeExecutionError,
    ToolExecutionException,
)
from app.config.settings import settings
from app.schemas.base_schema import ApiResponse

logger = logging.getLogger(__name__)


def _get_client_ip(request: Request) -> str:
    """获取客户端IP"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _log_exception(request: Request, exc: Exception, level: str = "error") -> None:
    """记录异常日志"""
    client_ip = _get_client_ip(request)
    log_msg = (
        f"[{request.method}] {request.url.path} | "
        f"Client: {client_ip} | "
        f"Exception: {type(exc).__name__}: {str(exc)}"
    )

    log_msg += f"\n{traceback.format_exc()}"

    if level == "error":
        logger.error(log_msg)
    elif level == "warning":
        logger.warning(log_msg)
    else:
        logger.info(log_msg)


def _format_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """格式化验证错误"""
    formatted = []
    for error in errors:
        loc = " -> ".join(str(x) for x in error.get("loc", []))
        formatted.append(
            {
                "field": loc,
                "message": error.get("msg", "Validation error"),
                "type": error.get("type", "value_error"),
            }
        )
    return formatted


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """处理 HTTPException"""
    if exc.status_code == 404:
        path = request.url.path
        if not path.startswith("/api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
    else:
        _log_exception(request, exc, "warning")

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(
            msg=exc.detail or "请求错误", code=exc.status_code
        ).model_dump(),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理请求验证错误"""
    _log_exception(request, exc, "warning")

    errors = _format_validation_errors(exc.errors())
    error_msg = "; ".join([f"{e['field']}: {e['message']}" for e in errors])

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(
            msg=f"参数验证失败: {error_msg}", code=422
        ).model_dump(),
    )


async def pydantic_validation_handler(
    request: Request, exc: ValidationError
) -> JSONResponse:
    """处理 Pydantic 验证错误"""
    _log_exception(request, exc, "warning")

    errors = _format_validation_errors(exc.errors())
    error_msg = "; ".join([f"{e['field']}: {e['message']}" for e in errors])

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(
            msg=f"数据验证失败: {error_msg}", code=422
        ).model_dump(),
    )


async def flow_validation_error_handler(
    request: Request, exc: FlowValidationError
) -> JSONResponse:
    """处理流程验证错误"""
    _log_exception(request, exc, "warning")

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(msg=exc.message, code=400).model_dump(),
    )


async def node_execution_error_handler(
    request: Request, exc: NodeExecutionError
) -> JSONResponse:
    """处理节点执行错误"""
    _log_exception(request, exc, "error")

    error_detail = f"节点 {exc.node_key} 执行失败"
    if settings.debug:
        error_detail = f"节点 {exc.node_key} 执行失败: {exc.message}"

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(msg=error_detail, code=500).model_dump(),
    )


async def tool_execution_exception_handler(
    request: Request, exc: ToolExecutionException
) -> JSONResponse:
    """处理工具执行异常"""
    _log_exception(request, exc, "error")

    error_detail = f"工具 {exc.tool_name} 执行失败"
    if settings.debug:
        error_detail = f"工具 {exc.tool_name} 执行失败: {exc.error_message}"

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(msg=error_detail, code=500).model_dump(),
    )


async def max_iterations_handler(
    request: Request, exc: MaxIterationsExceededException
) -> JSONResponse:
    """处理超过最大迭代次数异常"""
    _log_exception(request, exc, "warning")

    error_detail = "超过最大迭代次数"
    if settings.debug:
        error_detail = f"超过最大迭代次数: {exc.max_iterations}"

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(msg=error_detail, code=500).model_dump(),
    )


async def checkpoint_error_handler(
    request: Request, exc: CheckpointError
) -> JSONResponse:
    """处理 Checkpoint 错误"""
    _log_exception(request, exc, "error")

    error_detail = "Checkpoint 错误"
    if settings.debug:
        error_detail = f"Checkpoint 错误: {exc.message}"

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(msg=error_detail, code=500).model_dump(),
    )


async def integrity_error_handler(
    request: Request, exc: IntegrityError
) -> JSONResponse:
    """处理数据库完整性错误"""
    _log_exception(request, exc, "error")

    error_detail = "数据库操作失败"
    if settings.debug:
        error_detail = f"数据库完整性错误: {str(exc)}"

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(msg=error_detail, code=500).model_dump(),
    )


async def sqlalchemy_error_handler(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    """处理 SQLAlchemy 数据库错误"""
    _log_exception(request, exc, "error")

    error_detail = "数据库操作失败"
    if settings.debug:
        error_detail = f"数据库错误: {str(exc)}"

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(msg=error_detail, code=500).model_dump(),
    )


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """处理 ValueError，直接返回原始消息"""
    _log_exception(request, exc, "warning")

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(msg=str(exc), code=400).model_dump(),
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理通用异常（兜底）"""
    _log_exception(request, exc, "error")

    error_detail = "服务器内部错误"
    if settings.debug:
        error_detail = f"{type(exc).__name__}: {str(exc)}"

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ApiResponse.error(msg=error_detail, code=500).model_dump(),
    )


def register_exception_handlers(app) -> None:
    """
    注册所有异常处理器到 FastAPI 应用

    Args:
        app: FastAPI 应用实例
    """
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(ValidationError, pydantic_validation_handler)
    app.add_exception_handler(FlowValidationError, flow_validation_error_handler)
    app.add_exception_handler(NodeExecutionError, node_execution_error_handler)
    app.add_exception_handler(ToolExecutionException, tool_execution_exception_handler)
    app.add_exception_handler(MaxIterationsExceededException, max_iterations_handler)
    app.add_exception_handler(CheckpointError, checkpoint_error_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    logger.info("全局异常处理器已注册")
