"""
自动更新 API 路由

提供更新状态查询、后台下载触发、应用更新（重启替换）、取消下载等接口。
"""

import logging

from fastapi import APIRouter

from app.schemas.base_schema import ApiResponse
from app.services.update_service import update_service

logger = logging.getLogger(__name__)


class UpdateApi:
    """自动更新 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/update", tags=["自动更新"])
        self._register_routes()

    def _register_routes(self):
        @self.router.get(
            "/check-update",
            response_model=ApiResponse,
            summary="检查更新",
        )
        async def check_update():
            """检查是否有新版本可用（实时远程请求，不缓存）"""
            result = await update_service.fetch_latest_version()
            return ApiResponse.success(data=result)

        @self.router.get(
            "/status",
            response_model=ApiResponse,
            summary="查询更新状态",
        )
        async def get_status():
            """查询当前更新状态（下载进度、就绪态、上次结果等）"""
            return ApiResponse.success(data=update_service.get_status(), msg="查询成功")

        @self.router.post(
            "/download",
            response_model=ApiResponse,
            summary="检查并后台下载更新包",
        )
        async def download_update():
            """检查新版本，若有则启动后台下载"""
            result = await update_service.check_and_download()
            return ApiResponse.success(data=result, msg="操作成功")

        @self.router.post(
            "/apply",
            response_model=ApiResponse,
            summary="应用更新（重启替换）",
        )
        async def apply_update():
            """触发更新：启动 updater 后退出主进程，由 updater 完成替换重启"""
            result = await update_service.apply_update()
            if "error" in result:
                return ApiResponse.error(msg=result["error"])
            if result.get("manual_update"):
                # systemd 环境：返回标记供前端弹窗引导手动更新，不触发"更新已启动"
                return ApiResponse.success(data=result)
            return ApiResponse.success(data=result, msg="更新已启动，即将重启")

        @self.router.post(
            "/cancel",
            response_model=ApiResponse,
            summary="取消下载",
        )
        async def cancel_update():
            """取消进行中的下载任务"""
            result = await update_service.cancel_download()
            return ApiResponse.success(data=result, msg="已取消")

        @self.router.post(
            "/ack",
            response_model=ApiResponse,
            summary="确认更新结果",
        )
        async def ack_result():
            """清除上次更新结果标记（前端展示后调用）"""
            update_service.clear_last_result()
            return ApiResponse.success(msg="已确认")


update_api = UpdateApi()
router = update_api.router
