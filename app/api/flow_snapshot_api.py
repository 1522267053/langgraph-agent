"""
流程版本快照 API 路由

提供快照的创建、恢复、列表查询、删除和置顶功能。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.services.flow_snapshot_service import flow_snapshot_service
from app.schemas.flow_snapshot_schema import FlowSnapshotBase, FlowSnapshotCreate


class FlowSnapshotApi:
    """流程版本快照 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/flow-snapshot", tags=["版本快照"])
        self._register_routes()

    def _register_routes(self):
        """注册所有路由"""

        @self.router.post(
            "/auto/{flow_id}",
            response_model=ApiResponse,
            summary="自动快照（保存前调用）",
        )
        async def auto_snapshot(flow_id: int, db: AsyncSession = Depends(get_db)):
            """创建自动快照"""
            snapshot = await flow_snapshot_service.create_snapshot(
                db, flow_id, snapshot_type="auto"
            )
            if not snapshot:
                return ApiResponse.error(msg="流程不存在")
            return ApiResponse.success(msg="快照已创建")

        @self.router.post(
            "/create/{flow_id}",
            response_model=ApiResponse,
            summary="手动创建快照",
        )
        async def create_snapshot(
            flow_id: int,
            data: FlowSnapshotCreate,
            db: AsyncSession = Depends(get_db),
        ):
            """手动创建快照"""
            snapshot = await flow_snapshot_service.create_snapshot(
                db,
                flow_id,
                name=data.name,
                description=data.description,
                snapshot_type="manual",
            )
            if not snapshot:
                return ApiResponse.error(msg="流程不存在")
            return ApiResponse.success(
                data=FlowSnapshotBase.model_to_view(snapshot), msg="快照已创建"
            )

        @self.router.get(
            "/list/{flow_id}",
            response_model=ApiResponse,
            summary="获取快照列表",
        )
        async def list_snapshots(flow_id: int, db: AsyncSession = Depends(get_db)):
            """获取流程的所有快照"""
            snapshots = await flow_snapshot_service.list_snapshots(db, flow_id)
            views = FlowSnapshotBase.model_to_view_batch(snapshots)
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.post(
            "/restore/{snapshot_id}",
            response_model=ApiResponse,
            summary="恢复快照",
        )
        async def restore_snapshot(
            snapshot_id: int, db: AsyncSession = Depends(get_db)
        ):
            """恢复快照到对应流程"""
            try:
                result = await flow_snapshot_service.restore_snapshot(db, snapshot_id)
                if not result:
                    return ApiResponse.error(msg="快照不存在")
                return ApiResponse.success(data=result, msg="恢复成功")
            except ValueError as e:
                return ApiResponse.error(msg=str(e))
            except Exception as e:
                return ApiResponse.error(msg=f"恢复失败: {e}")

        @self.router.get(
            "/delete/{id}",
            response_model=ApiResponse,
            summary="删除快照",
        )
        async def delete_snapshot(id: int, db: AsyncSession = Depends(get_db)):
            """删除快照"""
            await flow_snapshot_service.delete(db, id)
            return ApiResponse.success(msg="删除成功")

        @self.router.post(
            "/pin/{id}",
            response_model=ApiResponse,
            summary="置顶/取消置顶快照",
        )
        async def toggle_pin(id: int, db: AsyncSession = Depends(get_db)):
            """切换快照的置顶状态（置顶的快照不被自动清理）"""
            new_pinned = await flow_snapshot_service.toggle_pin(db, id)
            if new_pinned is None:
                return ApiResponse.error(msg="快照不存在")

            return ApiResponse.success(
                data={"is_pinned": new_pinned},
                msg="已置顶" if new_pinned == 1 else "已取消置顶",
            )


flow_snapshot_api = FlowSnapshotApi()
router = flow_snapshot_api.router
