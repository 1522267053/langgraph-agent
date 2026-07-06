"""
流程节点 API 路由
处理流程节点相关的路由定义
"""

import logging
from collections import defaultdict

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_flow.exceptions import FlowValidationError
from app.api.base_api import BaseApi, RouteConfig
from app.constants.node_types import NODE_TYPE_LABELS
from app.models.flow import Flow, FlowType
from app.models.flow_node import (
    AGENT_ALLOWED_NODE_TYPES,
    AGENT_UNIQUE_NODE_TYPES,
    FlowNode,
)
from app.services.flow_service import flow_service
from app.services.global_config_service import global_config_service
from app.schemas.flow_node_schema import FlowNodeBase, FlowNodeCreate, FlowNodeUpdate
from app.utils.flow_utils import flow_contains_nodes

logger = logging.getLogger(__name__)


class FlowNodeApi(
    BaseApi[FlowNode, FlowNodeBase, FlowNodeBase, FlowNodeCreate, FlowNodeUpdate]
):
    """流程节点 API"""

    def __init__(self):
        super().__init__(
            service=flow_service,
            router_prefix="/api/flow-node",
            router_tags=["流程节点"],
            route_config=RouteConfig(
                enable_page=False,
                enable_get=False,
                enable_create=True,
                enable_update=True,
                enable_delete=True,
                enable_batch_delete=False,
                enable_batch_create=True,
                enable_batch_update=True,
            ),
        )

    @staticmethod
    async def _check_and_disable_scheduled_tasks(
        db: AsyncSession, flow_id: int, node_types: list[str]
    ) -> None:
        """检查流程是否新增了人类帮助节点，如有则禁用关联的定时任务。"""
        if "human" not in node_types:
            return

        from app.models.scheduled_task import ScheduledTask, ScheduledTaskTargetType

        stmt = select(ScheduledTask).where(
            ScheduledTask.target_type == ScheduledTaskTargetType.FLOW.value,
            ScheduledTask.target_id == flow_id,
            ScheduledTask.is_enabled == 1,
            ScheduledTask.is_delete == 0,
        )
        result = await db.execute(stmt)
        tasks = list(result.scalars().all())

        if not tasks:
            return

        from app.services.scheduler_service import scheduler_service

        for task in tasks:
            task.is_enabled = 0
            task.next_run_time = None
            scheduler_service.remove_job(task.id)
            logger.info(
                f"流程[{flow_id}]新增人类帮助节点，自动禁用定时任务「{task.name}」(id={task.id})"
            )

    async def _check_memory_unique(self, db: AsyncSession, flow_id: int) -> None:
        stmt = select(func.count(FlowNode.id)).where(
            FlowNode.flow_id == flow_id,
            FlowNode.node_type == "memory",
            FlowNode.is_delete == 0,
        )
        result = await db.execute(stmt)
        if (result.scalar() or 0) > 0:
            raise FlowValidationError("每个智能体最多只能有一个记忆节点")

    async def _check_agent_node(
        self, db: AsyncSession, flow: Flow, node_type: str
    ) -> None:
        """校验 Agent 模式下节点类型白名单和唯一性约束（单条操作，查 DB）"""
        if flow.flow_type != FlowType.AGENT.value:
            return
        if node_type not in AGENT_ALLOWED_NODE_TYPES:
            raise FlowValidationError(
                f"智能体不支持「{node_type}」类型的节点，"
                f"仅支持：开始、结束、大模型调用、条件、意图路由和工具节点"
            )
        if node_type in AGENT_UNIQUE_NODE_TYPES:
            stmt = select(func.count(FlowNode.id)).where(
                FlowNode.flow_id == flow.id,
                FlowNode.node_type == node_type,
                FlowNode.is_delete == 0,
            )
            result = await db.execute(stmt)
            if (result.scalar() or 0) > 0:
                label = NODE_TYPE_LABELS.get(node_type, node_type)
                raise FlowValidationError(f"智能体只能有一个{label}节点")

    @staticmethod
    def _check_agent_batch(node_types: list[str]) -> None:
        """校验 Agent 模式下节点类型白名单和唯一性节点数量。

        检查本次批量中 UNIQUE 类型（start/end/llm）的数量不超过 1，
        完整唯一性（含 DB 已有节点）由执行时 _validate_agent_flow 兜底。
        """
        for nt in node_types:
            if nt not in AGENT_ALLOWED_NODE_TYPES:
                raise FlowValidationError(
                    f"智能体不支持「{nt}」类型的节点，"
                    f"仅支持：开始、结束、大模型调用、条件、意图路由和工具节点"
                )
        for ut in AGENT_UNIQUE_NODE_TYPES:
            count = node_types.count(ut)
            if count > 1:
                label = NODE_TYPE_LABELS.get(ut, ut)
                raise FlowValidationError(f"智能体只能有一个{label}节点")

    async def _check_nested_loop(
        self, db: AsyncSession, flow_id: int, node_key: str
    ) -> None:
        """校验循环节点不能嵌套在另一个循环节点内（含跨流程间接嵌套）。

        1. 同 flow 内：node_key 使用 __ 分隔层级（如 loop_a__card_b__loop_c），
           遍历所有祖先前缀，检查是否有循环节点祖先。
        2. 跨 flow：当前 flow 被其他 flow 的 card 节点引用，
           且该 card 节点位于循环节点内时拒绝。
        """
        if "__" in node_key:
            parts = node_key.split("__")
            ancestors = ["__".join(parts[:i]) for i in range(1, len(parts))]
            if ancestors:
                stmt = select(FlowNode.node_key).where(
                    FlowNode.flow_id == flow_id,
                    FlowNode.node_key.in_(ancestors),
                    FlowNode.node_type == "loop",
                    FlowNode.is_delete == 0,
                )
                result = await db.execute(stmt)
                if result.scalar_one_or_none():
                    raise FlowValidationError("循环节点内不能嵌套另一个循环节点")

        # ---- 跨 flow 检查：当前 flow 是否被 card 引用且该 card 在 loop 内 ----
        stmt = select(FlowNode.flow_id, FlowNode.node_key, FlowNode.base_config).where(
            FlowNode.node_type == "card",
            FlowNode.is_delete == 0,
        )
        rows = (await db.execute(stmt)).fetchall()

        by_flow: dict[int, set[str]] = defaultdict(set)
        for card_flow_id, card_node_key, card_config in rows:
            if not isinstance(card_config, dict):
                continue
            if card_config.get("ref_flow_id") != flow_id:
                continue
            if "__" not in card_node_key:
                continue
            card_parts = card_node_key.split("__")
            for i in range(1, len(card_parts)):
                by_flow[card_flow_id].add("__".join(card_parts[:i]))

        for fid, ancestors in by_flow.items():
            loop_stmt = select(FlowNode.node_key).where(
                FlowNode.flow_id == fid,
                FlowNode.node_key.in_(list(ancestors)),
                FlowNode.node_type == "loop",
                FlowNode.is_delete == 0,
            )
            if (await db.execute(loop_stmt)).scalar_one_or_none():
                raise FlowValidationError(
                    "该流程已被放置在循环节点内，不能再添加循环节点"
                )

    async def _check_card_loop_nesting(
        self,
        db: AsyncSession,
        flow_id: int,
        node_key: str,
        ref_flow_id: int | None = None,
        base_config: dict | None = None,
    ) -> None:
        """校验包含循环节点的能力卡片不能放置在循环节点内。

        当 card 节点位于 loop 内时，检查其引用的 flow 是否含有循环节点。
        优先使用顶层 ref_flow_id 字段，兜底从 base_config 中读取。
        """
        resolved_ref = ref_flow_id
        if not resolved_ref and isinstance(base_config, dict):
            resolved_ref = base_config.get("ref_flow_id")
        if not resolved_ref:
            return

        is_inside_loop = False
        if "__" in node_key:
            parts = node_key.split("__")
            ancestors = ["__".join(parts[:i]) for i in range(1, len(parts))]
            stmt = select(FlowNode.node_type).where(
                FlowNode.flow_id == flow_id,
                FlowNode.node_key.in_(ancestors),
                FlowNode.node_type == "loop",
                FlowNode.is_delete == 0,
            )
            is_inside_loop = bool((await db.execute(stmt)).scalar_one_or_none())

        if not is_inside_loop:
            return

        from app.utils.flow_utils import flow_contains_nodes

        if await flow_contains_nodes(db, resolved_ref, {"loop"}):
            raise FlowValidationError("包含循环节点的能力卡片不能放置在循环节点内")

    async def _validate_node(
        self,
        db: AsyncSession,
        flow_id: int,
        node_key: str,
        node_type: str,
        ref_flow_id: int | None,
        base_config: dict | None,
    ) -> None:
        """统一的节点校验：循环嵌套 + 卡片循环嵌套 + 卡片循环引用 + 子Agent嵌套。"""
        if node_type == "loop":
            await self._check_nested_loop(db, flow_id, node_key)
        if node_type == "card":
            resolved_ref = ref_flow_id
            if not resolved_ref and isinstance(base_config, dict):
                resolved_ref = base_config.get("ref_flow_id")
            if resolved_ref:
                await flow_service.check_circular_card_refs(db, flow_id, resolved_ref)
            await self._check_card_loop_nesting(
                db,
                flow_id,
                node_key,
                ref_flow_id=ref_flow_id,
                base_config=base_config,
            )
        if node_type == "sub_agent":
            await self._check_nested_sub_agent(db, flow_id, base_config)

    async def _check_nested_sub_agent(
        self,
        db: AsyncSession,
        flow_id: int,
        base_config: dict | None,
    ) -> None:
        """校验子Agent节点引用的Agent必须已发布、描述非空、且不含sub_agent节点"""
        agent_id = (
            base_config.get("agent_id") if isinstance(base_config, dict) else None
        )
        if not agent_id:
            return

        agent = await flow_service.get_by_id(db, agent_id, raise_not_found=False)
        if not agent:
            raise FlowValidationError("被引用的Agent不存在")
        if agent.flow_type != FlowType.AGENT.value:
            raise FlowValidationError("子Agent节点只能引用智能体(Agent)类型的流程")
        if not agent.description or not agent.description.strip():
            raise FlowValidationError(
                "被引用的Agent必须填写描述(description)，以便父Agent了解其能力"
            )
        if await flow_contains_nodes(db, agent_id, {"sub_agent"}):
            raise FlowValidationError("子Agent节点引用的Agent不能包含子Agent节点")

    @staticmethod
    async def _inject_llm_defaults(
        db: AsyncSession, node_type: str, base_config: dict | None
    ) -> dict | None:
        """为 LLM 节点注入全局默认配置（仅回填空字段）"""
        if node_type not in ("llm", "intent_router") or base_config is None:
            return base_config
        from app.services.node_config_helper import inject_llm_defaults

        global_cfg = await global_config_service.get_default_llm_config(db)
        return inject_llm_defaults(base_config, global_cfg)

    async def create(self, db: AsyncSession, data: FlowNodeCreate) -> FlowNode:
        """创建节点（含 Agent 校验 + 循环嵌套校验 + LLM 全局默认配置注入）"""
        data.base_config = await self._inject_llm_defaults(
            db, data.node_type, data.base_config
        )
        flow = await flow_service.get_by_id(db, data.flow_id, raise_not_found=True)
        await self._check_agent_node(db, flow, data.node_type)
        await self._validate_node(
            db,
            data.flow_id,
            data.node_key,
            data.node_type,
            data.ref_flow_id,
            data.base_config,
        )
        node = await flow_service.create_node(db, data)
        await self._check_and_disable_scheduled_tasks(
            db, data.flow_id, [data.node_type]
        )
        return node

    async def update(self, db: AsyncSession, data: FlowNodeUpdate) -> FlowNode | None:
        """更新节点"""
        node = await flow_service.update_node(db, data)
        if node and data.node_type:
            await self._check_and_disable_scheduled_tasks(
                db, node.flow_id, [data.node_type]
            )
        return node

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除节点"""
        await flow_service.delete_node(db, id)

    async def batch_create(
        self, db: AsyncSession, data_list: list[FlowNodeCreate]
    ) -> None:
        """批量创建节点（含 Agent 类型白名单校验 + 循环嵌套校验 + LLM 全局默认配置注入）"""
        if not data_list:
            return
        for data in data_list:
            data.base_config = await self._inject_llm_defaults(
                db, data.node_type, data.base_config
            )
        flow_ids = {d.flow_id for d in data_list}
        for fid in flow_ids:
            flow = await flow_service.get_by_id(db, fid)
            if flow and flow.flow_type == FlowType.AGENT.value:
                types_for_flow = [d.node_type for d in data_list if d.flow_id == fid]
                self._check_agent_batch(types_for_flow)
        for data in data_list:
            await self._validate_node(
                db,
                data.flow_id,
                data.node_key,
                data.node_type,
                data.ref_flow_id,
                data.base_config,
            )
        await flow_service.bulk_create_nodes(db, data_list)
        for fid in flow_ids:
            types_for_flow = [d.node_type for d in data_list if d.flow_id == fid]
            await self._check_and_disable_scheduled_tasks(db, fid, types_for_flow)

    async def batch_update(
        self, db: AsyncSession, data_list: list[FlowNodeUpdate]
    ) -> None:
        """批量更新节点"""
        for data in data_list:
            data.base_config = await self._inject_llm_defaults(
                db, data.node_type, data.base_config
            )
            await flow_service.update_node(db, data)
        flow_ids = {d.flow_id for d in data_list if d.flow_id}
        for fid in flow_ids:
            types_for_flow = [
                d.node_type for d in data_list if d.flow_id == fid and d.node_type
            ]
            if types_for_flow:
                await self._check_and_disable_scheduled_tasks(db, fid, types_for_flow)


flow_node_api = FlowNodeApi()
router = flow_node_api.router
