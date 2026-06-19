"""
流程服务模块

本模块提供流程定义的 CRUD 操作和流程编排相关功能。
流程（Flow）是流程编排系统的核心实体，由节点（FlowNode）和边（FlowEdge）组成。

主要功能:
    1. 流程管理: 创建、查询、更新、删除流程
    2. 节点管理: 流程节点的 CRUD 操作，支持批量创建
    3. 边管理: 流程边（连接关系）的 CRUD 操作，支持批量创建
    4. 流程转卡片: 将设计好的流程保存为可复用的能力卡片

流程结构:
    - Flow: 流程主表，存储流程基本信息（名称、描述、状态等）
    - FlowNode: 流程节点，表示流程中的一个执行步骤
    - FlowEdge: 流程边，定义节点之间的连接和流转条件

继承自 BaseService:
    自动继承以下方法:
    - get_list(): 获取流程列表
    - get_by_id(): 根据ID获取流程
    - create(): 创建流程
    - update(): 更新流程
    - delete(): 删除流程
    - page_query(): 分页查询流程
"""

from typing import List, Optional, Set
from datetime import datetime
from collections import defaultdict, deque
import logging
from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.constants.node_types import NODE_TYPE_LABELS
from app.models.flow import Flow, FlowType
from app.models.flow_node import (
    FlowNode,
    NodeType,
    AGENT_TOOL_NODE_TYPES,
    TOOL_ONLY_NODE_TYPES,
)
from app.models.flow_edge import FlowEdge
from app.models.flow_execution import FlowExecution
from app.models.checkpoint import CheckpointModel, CheckpointWrite, CheckpointBlob
from app.models.conversation_message import ConversationMessage
from app.agent_flow.exceptions import FlowValidationError
from app.services.base_service import BaseService
from app.schemas.flow_schema import FlowCreate, FlowUpdate
from app.schemas.flow_node_schema import FlowNodeCreate, FlowNodeUpdate
from app.schemas.flow_edge_schema import FlowEdgeCreate, FlowEdgeUpdate

logger = logging.getLogger(__name__)

# Agent 默认输入参数（message 字段，所有 Agent 通用）
DEFAULT_AGENT_INPUT_SCHEMA: dict = {
    "fields": [
        {
            "name": "message",
            "type": "string",
            "description": "用户消息",
            "required": True,
        }
    ]
}


class FlowService(BaseService[Flow, FlowCreate, FlowUpdate]):
    """
    流程服务类

    提供流程定义及其节点、边的完整管理能力。继承自 BaseService，
    在基础 CRUD 功能之上增加了流程特有的操作。

    核心功能:
        - get_with_nodes_and_edges: 获取流程详情（含完整拓扑）
        - save_as_card: 将流程标记为能力卡片
        - create_node/update_node/delete_node: 单个节点操作
        - create_edge/update_edge/delete_edge: 单个边操作
        - batch_create_nodes/batch_create_edges: 批量创建操作

    流程与能力卡片的关系:
        流程可以被"发布"为能力卡片，使其成为可复用的功能单元。
        能力卡片与流程一一对应，通过 saved_as_card 字段标识。

    使用示例:
        ```python
        service = FlowService()

        # 创建流程
        flow = await service.create(db, FlowCreate(name="示例流程"))

        # 添加节点
        node = await service.create_node(db, FlowNodeCreate(
            flow_id=flow.id,
            node_key="start",
            node_type=NodeType.START,
            node_name="开始"
        ))

        # 获取完整流程（含节点和边）
        flow_detail = await service.get_with_nodes_and_edges(db, flow.id)

        # 发布为能力卡片
        flow = await service.save_as_card(db, flow.id)
        ```
    """

    def __init__(self):
        """
        初始化流程服务

        调用父类构造函数，绑定 Flow 模型。
        """
        super().__init__(Flow)

    async def create(self, db: AsyncSession, obj_in: FlowCreate) -> Flow:
        """创建流程，校验名称唯一性"""
        stmt = select(Flow.id).where(Flow.name == obj_in.name, Flow.is_delete == 0)
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            raise ValueError(f"名称「{obj_in.name}」已存在")
        return await super().create(db, obj_in)

    async def fix_agent_input_schema(self, db: AsyncSession) -> int:
        """批量补偿：对所有 input_schema 为 NULL 的 Agent 设置默认值

        用于修复旧版本创建的 Agent 缺少 input_schema 的问题。
        在应用启动时调用一次。

        Returns:
            修复的 Agent 数量
        """
        from sqlalchemy import update as sql_update

        stmt = (
            sql_update(Flow)
            .where(
                Flow.flow_type == FlowType.AGENT.value,
                Flow.input_schema.is_(None),
                Flow.is_delete == 0,
            )
            .values(input_schema=DEFAULT_AGENT_INPUT_SCHEMA)
        )
        result = await db.execute(stmt)
        await db.commit()
        count = result.rowcount
        if count:
            logger.info("已为 %d 个 Agent 补充默认 input_schema", count)
        return count

    async def duplicate_flow(self, db: AsyncSession, flow_id: int) -> Flow:
        """复制流程或智能体（含全部节点和边）

        复制规则：
        - 副本名称自动生成（原名称 (副本)），确保唯一
        - node_key 保持不变（边通过 key 连接，无需重映射）
        - 保留 base_config（含 LLM api_key/base_url）
        - 副本状态为 DRAFT，saved_as_card=0
        - 不复制记忆（记忆绑定 agent_id）

        Args:
            db: 数据库异步会话
            flow_id: 源流程 ID

        Returns:
            新创建的流程对象（含节点和边）

        Raises:
            ValueError: 流程不存在或为内置流程
        """
        source = await self.get_with_nodes_and_edges(db, flow_id)
        if not source:
            raise ValueError("流程不存在")

        if getattr(source, "is_builtin", 0) == 1:
            raise ValueError("内置流程不可复制")

        # 生成唯一名称
        unique_name = await self._ensure_unique_flow_name(db, source.name)

        # 构造节点列表（node_key 保持不变）
        ai_nodes = [
            {
                "node_type": n.node_type,
                "node_key": n.node_key,
                "node_name": n.node_name,
                "position_x": n.position_x,
                "position_y": n.position_y,
                "base_config": dict(n.base_config) if n.base_config else None,
                "ref_flow_id": n.ref_flow_id,
            }
            for n in (source.nodes or [])
        ]

        # 构造边列表
        ai_edges = [
            {
                "source_node_key": e.source_node_key,
                "target_node_key": e.target_node_key,
                "source_handle": e.source_handle,
                "target_handle": e.target_handle,
                "condition": e.condition,
                "label": e.label,
            }
            for e in (source.edges or [])
        ]

        return await self.generate_flow(
            db=db,
            name=unique_name,
            flow_type=source.flow_type,
            description=source.description,
            input_schema=source.input_schema,
            output_schema=source.output_schema,
            ai_nodes=ai_nodes,
            ai_edges=ai_edges,
        )

    async def _ensure_unique_flow_name(self, db: AsyncSession, name: str) -> str:
        """确保流程名称唯一，冲突时添加 (副本) 后缀"""
        candidate = name
        num = 1
        while True:
            stmt = select(Flow.id).where(Flow.name == candidate, Flow.is_delete == 0)
            result = await db.execute(stmt)
            if not result.scalar_one_or_none():
                return candidate
            num += 1
            candidate = f"{name} (副本{num if num > 1 else ''})"
            if num > 100:
                candidate = f"{name} ({datetime.now().strftime('%Y%m%d%H%M%S')})"
                return candidate

    async def check_circular_card_refs(
        self, db: AsyncSession, flow_id: int, ref_flow_id: int
    ) -> None:
        """检查卡片引用是否形成循环链

        从 ref_flow_id 出发 BFS 遍历所有 card 节点的引用链，
        如果回到 flow_id 则说明存在循环引用。

        Args:
            db: 数据库异步会话
            flow_id: 当前流程 ID（卡片所属流程）
            ref_flow_id: 卡片要引用的目标流程 ID

        Raises:
            FlowValidationError: 存在循环引用时抛出
        """
        if ref_flow_id == flow_id:
            raise FlowValidationError("卡片节点不能引用自身所在的流程")

        visited: set[int] = {flow_id}
        queue = deque([ref_flow_id])

        while queue:
            current_id = queue.popleft()
            if current_id in visited:
                ref_flow = await self.get_by_id(db, ref_flow_id, raise_not_found=False)
                ref_name = ref_flow.name if ref_flow else str(ref_flow_id)
                raise FlowValidationError(f"卡片引用的流程「{ref_name}」会形成循环引用")
            visited.add(current_id)

            stmt = select(FlowNode.ref_flow_id, FlowNode.base_config).where(
                FlowNode.flow_id == current_id,
                FlowNode.node_type == "card",
                FlowNode.is_delete == 0,
            )
            result = await db.execute(stmt)
            for ref_id, base_config in result.fetchall():
                if ref_id is not None:
                    queue.append(ref_id)
                elif isinstance(base_config, dict) and base_config.get("ref_flow_id"):
                    queue.append(base_config["ref_flow_id"])

    async def get_with_nodes_and_edges(
        self, db: AsyncSession, flow_id: int
    ) -> Optional[Flow]:
        """
        获取流程详情（含节点和边）

        查询指定流程的完整定义，包括流程中的所有节点和边。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID

        Returns:
            Optional[Flow]: 流程对象，包含:
                - nodes: 所有流程节点的列表
                - edges: 所有流程边的列表
                如果流程不存在，返回 None

        Example:
            ```python
            flow = await service.get_with_nodes_and_edges(db, flow_id=1)
            if flow:
                print(f"流程: {flow.name}")
                print(f"节点数: {len(flow.nodes)}")
                print(f"边数: {len(flow.edges)}")
            ```
        """
        query = select(Flow).where(Flow.id == flow_id)
        result = await db.execute(query)
        flow = result.scalar_one_or_none()
        if not flow:
            return None

        nodes_query = select(FlowNode).where(
            FlowNode.flow_id == flow_id, FlowNode.is_delete == 0
        )
        nodes_result = await db.execute(nodes_query)
        flow.nodes = list(nodes_result.scalars().all())

        edges_query = select(FlowEdge).where(
            FlowEdge.flow_id == flow_id, FlowEdge.is_delete == 0
        )
        edges_result = await db.execute(edges_query)
        flow.edges = list(edges_result.scalars().all())

        return flow

    async def save_as_card(self, db: AsyncSession, flow_id: int) -> Optional[Flow]:
        """
        将流程保存为能力卡片

        将流程标记为能力卡片，使其可以被其他流程引用。
        能力卡片与流程一一对应。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID

        Returns:
            Optional[Flow]: 更新后的流程对象
                如果流程不存在，返回 None

        Raises:
            ValueError: 如果流程类型为智能体(agent)

        Note:
            调用后流程的 saved_as_card 字段会被设置为 1

        Example:
            ```python
            flow = await service.save_as_card(db, flow_id=1)
            if flow:
                print(f"流程已保存为能力卡片: {flow.name}")
            ```
        """
        flow = await self.get_with_nodes_and_edges(db, flow_id)
        if not flow:
            return None

        if flow.flow_type == FlowType.AGENT.value:
            raise ValueError("智能体类型的流程不能保存为能力卡片")

        flow.saved_as_card = 1
        await db.commit()
        await db.refresh(flow)
        return flow

    # ---- 输出变量剥离 ----

    _OUTPUT_RELATED_KEYS = ("output_variables", "output_variable", "thinking_variable")

    @staticmethod
    def _strip_output_variables(base_config: dict) -> None:
        """剥离非 end 节点的输出变量相关字段，确保由 handler 默认值决定输出"""
        for key in FlowService._OUTPUT_RELATED_KEYS:
            base_config.pop(key, None)

    # ---- 节点管理 ----

    async def create_node(
        self, db: AsyncSession, node_data: FlowNodeCreate
    ) -> FlowNode:
        """
        创建流程节点

        在流程中添加一个新的节点。节点是流程的基本执行单元。

        节点类型 (NodeType):
            - START: 开始节点，流程入口，每个流程有且仅有一个
            - END: 结束节点，流程出口，可以有多个
            - CONDITION: 条件节点，根据条件选择分支
            - CARD: 能力卡片节点，执行具体功能
            - LLM: 大模型节点，调用 AI 模型

        Args:
            db: 数据库异步会话
            node_data: 节点创建数据，包含:
                - flow_id: 所属流程ID
                - node_key: 节点唯一标识（用于边的连接）
                - node_type: 节点类型
                - node_name: 节点显示名称
                - base_config: 节点配置（JSON）
                - position_x/position_y: 画布位置
                - card_id: 关联的能力卡片ID（CARD类型节点）

        Returns:
            FlowNode: 创建的节点对象

        Example:
            ```python
            node = await service.create_node(db, FlowNodeCreate(
                flow_id=1,
                node_key="node_1",
                node_type=NodeType.CARD,
                node_name="查询用户",
                card_id=5
            ))
            ```
        """
        if node_data.base_config and node_data.node_type != NodeType.END:
            self._strip_output_variables(node_data.base_config)
        node = node_data.to_model(FlowNode)
        db.add(node)
        await db.commit()
        await db.refresh(node)
        return node

    async def bulk_create_nodes(
        self, db: AsyncSession, nodes_data: List[FlowNodeCreate]
    ) -> List[FlowNode]:
        """批量创建流程节点"""
        for nd in nodes_data:
            if nd.base_config and nd.node_type != NodeType.END:
                self._strip_output_variables(nd.base_config)
        db_nodes = [nd.to_model(FlowNode) for nd in nodes_data]
        db.add_all(db_nodes)
        await db.commit()
        for node in db_nodes:
            await db.refresh(node)
        return db_nodes

    async def update_node(
        self, db: AsyncSession, node_data: FlowNodeUpdate
    ) -> Optional[FlowNode]:
        """
        更新流程节点

        更新已存在的节点配置。支持部分更新（只更新提供的字段）。

        Args:
            db: 数据库异步会话
            node_data: 节点更新数据，必须包含 id 字段
                其他字段仅在被设置时才会更新

        Returns:
            Optional[FlowNode]: 更新后的节点对象
                如果节点不存在，返回 None

        Example:
            ```python
            node = await service.update_node(db, FlowNodeUpdate(
                id=1,
                node_name="新名称",
                base_config={"timeout": 30}
            ))
            ```
        """
        node = await db.get(FlowNode, node_data.id)
        if not node:
            return None
        update_data = node_data.model_dump(exclude={"id"}, exclude_unset=True)
        for field, value in update_data.items():
            setattr(node, field, value)
        if node.base_config and node.node_type != NodeType.END:
            self._strip_output_variables(node.base_config)
        await db.commit()
        await db.refresh(node)
        return node

    async def delete_node(self, db: AsyncSession, node_id: int) -> bool:
        """
        删除流程节点

        物理删除指定的节点。删除节点后，与之相关的边也需要手动删除。

        Args:
            db: 数据库异步会话
            node_id: 节点ID

        Returns:
            bool: 删除成功返回 True，节点不存在返回 False

        Warning:
            删除节点不会自动删除相关的边，可能导致流程结构不完整。
            建议在删除节点前先删除相关的边。

        Example:
            ```python
            success = await service.delete_node(db, node_id=1)
            if success:
                print("节点已删除")
            ```
        """
        node = await db.get(FlowNode, node_id)
        if not node:
            return False
        await db.delete(node)
        await db.commit()
        return True

    async def create_edge(
        self, db: AsyncSession, edge_data: FlowEdgeCreate
    ) -> FlowEdge:
        """
        创建流程边

        在两个节点之间创建连接关系。边定义了流程的执行路径。

        边的属性:
            - source_node_key: 源节点 key（边的起点）
            - target_node_key: 目标节点 key（边的终点）
            - condition: 流转条件（可选），用于条件分支

        Args:
            db: 数据库异步会话
            edge_data: 边创建数据，包含:
                - flow_id: 所属流程ID
                - source_node_key: 源节点 key
                - target_node_key: 目标节点 key
                - condition: 流转条件（JSON，可选）

        Returns:
            FlowEdge: 创建的边对象

        Example:
            ```python
            # 无条件边
            edge = await service.create_edge(db, FlowEdgeCreate(
                flow_id=1,
                source_node_key="start",
                target_node_key="node_1"
            ))

            # 条件边
            edge = await service.create_edge(db, FlowEdgeCreate(
                flow_id=1,
                source_node_key="condition_1",
                target_node_key="node_2",
                condition={"type": "expression", "expression": "variables['score'] > 60"}
            ))
            ```
        """
        edge = edge_data.to_model(FlowEdge)
        db.add(edge)
        await db.commit()
        await db.refresh(edge)
        return edge

    async def update_edge(
        self, db: AsyncSession, edge_data: FlowEdgeUpdate
    ) -> Optional[FlowEdge]:
        """
        更新流程边

        更新已存在的边配置，主要用于修改流转条件。

        Args:
            db: 数据库异步会话
            edge_data: 边更新数据，必须包含 id 字段

        Returns:
            Optional[FlowEdge]: 更新后的边对象
                如果边不存在，返回 None

        Example:
            ```python
            edge = await service.update_edge(db, FlowEdgeUpdate(
                id=1,
                condition={"type": "expression", "expression": "input['age'] >= 18"}
            ))
            ```
        """
        edge = await db.get(FlowEdge, edge_data.id)
        if not edge:
            return None
        update_data = edge_data.model_dump(exclude={"id"}, exclude_unset=True)
        for field, value in update_data.items():
            setattr(edge, field, value)
        await db.commit()
        await db.refresh(edge)
        return edge

    async def delete_edge(self, db: AsyncSession, edge_id: int) -> bool:
        """
        删除流程边

        物理删除指定的边，断开节点之间的连接。

        Args:
            db: 数据库异步会话
            edge_id: 边ID

        Returns:
            bool: 删除成功返回 True，边不存在返回 False

        Example:
            ```python
            success = await service.delete_edge(db, edge_id=1)
            ```
        """
        edge = await db.get(FlowEdge, edge_id)
        if not edge:
            return False
        await db.delete(edge)
        await db.commit()
        return True

    async def batch_create_nodes(
        self, db: AsyncSession, flow_id: int, nodes_data: List[FlowNodeCreate]
    ) -> List[FlowNode]:
        """
        批量创建节点

        一次性创建多个节点，通常用于保存整个流程设计。
        所有节点会被自动关联到指定的流程。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID，所有节点将关联到此流程
            nodes_data: 节点创建数据列表

        Returns:
            List[FlowNode]: 创建的节点对象列表

        Note:
            此方法会覆盖 nodes_data 中的 flow_id 字段，
            确保所有节点都关联到正确的流程。

        Example:
            ```python
            nodes = await service.batch_create_nodes(db, flow_id=1, nodes_data=[
                FlowNodeCreate(node_key="start", node_type=NodeType.START, node_name="开始"),
                FlowNodeCreate(node_key="end", node_type=NodeType.END, node_name="结束"),
            ])
            ```
        """
        nodes = []
        for node_data in nodes_data:
            node_data.flow_id = flow_id
            if node_data.base_config and node_data.node_type != NodeType.END:
                self._strip_output_variables(node_data.base_config)
            node = node_data.to_model(FlowNode)
            nodes.append(node)
            db.add(node)
        await db.commit()
        for node in nodes:
            await db.refresh(node)
        return nodes

    async def get_flow_cards(self, db: AsyncSession) -> List[Flow]:
        """
        获取已保存为能力卡片的流程列表

        查询所有 saved_as_card=1 的流程，这些流程可以被其他流程引用作为卡片节点。

        Args:
            db: 数据库异步会话

        Returns:
            List[Flow]: 已保存为卡片的流程列表

        Example:
            ```python
            flow_cards = await service.get_flow_cards(db)
            for flow in flow_cards:
                print(f"流程卡片: {flow.name}, ID: {flow.id}")
            ```
        """
        query = select(Flow).where(Flow.saved_as_card == 1, Flow.is_delete == 0)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_by_flow_type(
        self,
        db: AsyncSession,
        flow_type: str,
        page: int = 1,
        page_size: int = 20,
        exclude_id: Optional[int] = None,
    ) -> tuple[List[Flow], int]:
        """
        根据流程类型获取流程列表

        Args:
            db: 数据库异步会话
            flow_type: 流程类型（flow/agent）
            page: 页码
            page_size: 每页数量

        Returns:
            tuple[List[Flow], int]: 流程列表和总数
        """
        # 计算总数
        conditions = [Flow.flow_type == flow_type, Flow.is_delete == 0]
        if exclude_id is not None:
            conditions.append(Flow.id != exclude_id)
        count_query = select(func.count()).select_from(Flow).where(*conditions)
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        # 分页查询
        offset = (page - 1) * page_size
        query = (
            select(Flow)
            .where(*conditions)
            .order_by(Flow.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(query)
        flows = list(result.scalars().all())

        return flows, total

    async def validate_tool_edges(
        self,
        db: AsyncSession,
        flow_id: int,
        edges_data: List[FlowEdgeCreate] | List[FlowEdgeUpdate],
        singleton_types: Set[str],
    ) -> Optional[str]:
        """
        校验工具边的唯一性约束

        检查 source_handle="tools" 的边中，属于 singleton_types 的节点类型
        是否有多个连接到同一个目标 LLM 节点。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            edges_data: 待保存的边数据列表
            singleton_types: 不允许重复连接的节点类型集合

        Returns:
            错误信息字符串，校验通过返回 None
        """
        # 收集本次提交的 tools 边
        tool_edges = []
        for edge_data in edges_data:
            source_handle = getattr(edge_data, "source_handle", None)
            if source_handle == "tools":
                tool_edges.append(edge_data)

        if not tool_edges:
            return None

        # 查询所有节点，建立 node_key -> node_type 映射
        nodes_query = select(FlowNode.node_key, FlowNode.node_type).where(
            FlowNode.flow_id == flow_id, FlowNode.is_delete == 0
        )
        nodes_result = await db.execute(nodes_query)
        key_to_type = {row[0]: row[1] for row in nodes_result.fetchall()}

        # 按 (target_node_key, node_type) 分组，找出 singleton 类型重复连接
        target_type_counts: dict[tuple[str, str], list[str]] = defaultdict(list)
        for edge_data in tool_edges:
            source_key = edge_data.source_node_key
            target_key = edge_data.target_node_key
            node_type = key_to_type.get(source_key, "")
            if node_type in singleton_types:
                target_type_counts[(target_key, node_type)].append(source_key)

        for (target_key, node_type), source_keys in target_type_counts.items():
            if len(source_keys) > 1:
                node_names = "、".join(source_keys)
                return (
                    f"节点类型「{node_type}」只允许连接一个到同一 LLM 节点，"
                    f"当前 LLM 节点「{target_key}」连接了多个：{node_names}"
                )

        return None

    async def validate_agent_edges(
        self,
        db: AsyncSession,
        flow_id: int,
        edges_data: List[FlowEdgeCreate] | List[FlowEdgeUpdate],
    ) -> Optional[str]:
        """校验 Agent 模式下的边连接规则。

        规则：
        - 工具边 (source_handle=tools)：仅允许工具节点→llm
        - 非工具边：无额外限制（允许 start→任意、任意→end 等自由连接）
        """
        from app.models.flow import FlowType

        flow = await db.get(Flow, flow_id)
        if not flow or flow.flow_type != FlowType.AGENT.value:
            return None

        nodes_query = select(FlowNode.node_key, FlowNode.node_type).where(
            FlowNode.flow_id == flow_id, FlowNode.is_delete == 0
        )
        nodes_result = await db.execute(nodes_query)
        key_to_type = {row[0]: row[1] for row in nodes_result.fetchall()}

        for edge_data in edges_data:
            source_key = edge_data.source_node_key
            target_key = edge_data.target_node_key
            source_handle = getattr(edge_data, "source_handle", None)
            source_type = key_to_type.get(source_key, "")
            target_type = key_to_type.get(target_key, "")

            if source_handle == "tools":
                if source_type not in AGENT_TOOL_NODE_TYPES:
                    label = NODE_TYPE_LABELS.get(source_type, source_type)
                    return f"智能体模式下「{label}」节点不能作为工具连接到 LLM"
                if target_type != "llm":
                    return "智能体模式下工具节点只能连接到大模型调用节点"

        return None

    async def validate_handle_existence(
        self,
        db: AsyncSession,
        flow_id: int,
        edges_data: list,
    ) -> Optional[str]:
        """校验边的 source_handle / target_handle 是否在对应节点类型的 handle 定义中。

        历史数据中 source_handle/target_handle 为 NULL 的边视为 "default" 兼容。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            edges_data: 待校验的边数据列表

        Returns:
            错误信息字符串，校验通过返回 None
        """
        from app.models.flow_node import (
            NODE_SOURCE_HANDLES,
            NODE_TARGET_HANDLES,
            NodeType,
        )

        involved_keys: set[str] = set()
        for e in edges_data:
            involved_keys.add(e.source_node_key)
            involved_keys.add(e.target_node_key)

        if not involved_keys:
            return None

        nodes_query = select(FlowNode.node_key, FlowNode.node_type).where(
            FlowNode.flow_id == flow_id,
            FlowNode.is_delete == 0,
            FlowNode.node_key.in_(involved_keys),
        )
        nodes_result = await db.execute(nodes_query)
        key_to_type = {row[0]: row[1] for row in nodes_result.fetchall()}

        for edge_data in edges_data:
            src_handle = getattr(edge_data, "source_handle", None) or "default"
            tgt_handle = getattr(edge_data, "target_handle", None) or "default"
            src_type = key_to_type.get(edge_data.source_node_key, "")
            tgt_type = key_to_type.get(edge_data.target_node_key, "")

            valid_src = NODE_SOURCE_HANDLES.get(src_type, set())
            if src_type == NodeType.INTENT_ROUTER.value:
                pass
            elif src_handle not in valid_src:
                src_label = NODE_TYPE_LABELS.get(src_type, src_type)
                return (
                    f"节点「{edge_data.source_node_key}」（{src_label}）"
                    f"没有 source handle「{src_handle}」，"
                    f"可用: {', '.join(sorted(valid_src)) if valid_src else '无'}"
                )

            valid_tgt = NODE_TARGET_HANDLES.get(tgt_type, set())
            if tgt_handle not in valid_tgt:
                tgt_label = NODE_TYPE_LABELS.get(tgt_type, tgt_type)
                return (
                    f"节点「{edge_data.target_node_key}」（{tgt_label}）"
                    f"没有 target handle「{tgt_handle}」，"
                    f"可用: {', '.join(sorted(valid_tgt)) if valid_tgt else '无'}"
                )

        return None

    async def validate_tool_edge_targets(
        self,
        db: AsyncSession,
        flow_id: int,
        edges_data: list,
    ) -> Optional[str]:
        """校验工具边（source_handle=tools）的目标节点必须是 llm。

        适用于所有流程模式（Flow 和 Agent），工具节点只能通过工具边连接到 LLM 节点。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            edges_data: 待校验的边数据列表

        Returns:
            错误信息字符串，校验通过返回 None
        """
        tool_edges = [
            e for e in edges_data if getattr(e, "source_handle", None) == "tools"
        ]
        if not tool_edges:
            return None

        nodes_query = select(FlowNode.node_key, FlowNode.node_type).where(
            FlowNode.flow_id == flow_id, FlowNode.is_delete == 0
        )
        nodes_result = await db.execute(nodes_query)
        key_to_type = {row[0]: row[1] for row in nodes_result.fetchall()}

        for edge_data in tool_edges:
            target_type = key_to_type.get(edge_data.target_node_key, "")
            if target_type != "llm":
                target_label = NODE_TYPE_LABELS.get(target_type, target_type)
                return (
                    f"工具边只能连接到大模型调用节点，"
                    f"当前目标节点「{edge_data.target_node_key}」类型为「{target_label}」"
                )

        return None

    async def validate_no_tool_in_flow_edges(
        self,
        db: AsyncSession,
        flow_id: int,
        edges_data: list,
    ) -> Optional[str]:
        """校验非工具边不能连接工具节点。

        工具节点（skill/mcp/memory/knowledge/python/shell/api/todo/media_gen）
        只能通过工具边（source_handle=tools）连接到 LLM 节点，不能出现在普通流程边中。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            edges_data: 待校验的边数据列表

        Returns:
            错误信息字符串，校验通过返回 None
        """
        non_tool_edges = [
            e for e in edges_data if getattr(e, "source_handle", None) != "tools"
        ]
        if not non_tool_edges:
            return None

        involved_keys: set[str] = set()
        for e in non_tool_edges:
            involved_keys.add(e.source_node_key)
            involved_keys.add(e.target_node_key)

        if not involved_keys:
            return None

        nodes_query = select(FlowNode.node_key, FlowNode.node_type).where(
            FlowNode.flow_id == flow_id,
            FlowNode.is_delete == 0,
            FlowNode.node_key.in_(involved_keys),
        )
        nodes_result = await db.execute(nodes_query)
        key_to_type = {row[0]: row[1] for row in nodes_result.fetchall()}

        for edge_data in non_tool_edges:
            src_type = key_to_type.get(edge_data.source_node_key, "")
            if src_type in TOOL_ONLY_NODE_TYPES:
                return (
                    f"工具节点「{edge_data.source_node_key}」（{src_type}）"
                    f"只能通过工具边连接到 LLM 节点，不能作为流程节点使用"
                )
            tgt_type = key_to_type.get(edge_data.target_node_key, "")
            if tgt_type in TOOL_ONLY_NODE_TYPES:
                return (
                    f"工具节点「{edge_data.target_node_key}」（{tgt_type}）"
                    f"只能通过工具边连接到 LLM 节点，不能作为流程节点使用"
                )

        return None

    async def validate_condition_edges(
        self,
        db: AsyncSession,
        flow_id: int,
    ) -> Optional[str]:
        """校验条件节点必须同时有 true 和 false 分支边。

        扫描流程中所有 condition 节点，检查每个条件节点是否至少有一条
        source_handle="true" 的出边和一条 source_handle="false" 的出边。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID

        Returns:
            错误信息字符串，校验通过返回 None
        """
        nodes_query = select(FlowNode.node_key, FlowNode.node_type).where(
            FlowNode.flow_id == flow_id, FlowNode.is_delete == 0
        )
        nodes_result = await db.execute(nodes_query)
        condition_keys = {
            row[0] for row in nodes_result.fetchall() if row[1] == "condition"
        }
        if not condition_keys:
            return None

        edges_query = select(FlowEdge.source_node_key, FlowEdge.source_handle).where(
            FlowEdge.flow_id == flow_id, FlowEdge.is_delete == 0
        )
        edges_result = await db.execute(edges_query)

        true_sources: set[str] = set()
        false_sources: set[str] = set()
        for src_key, src_handle in edges_result.fetchall():
            if src_key not in condition_keys:
                continue
            if src_handle == "true":
                true_sources.add(src_key)
            elif src_handle == "false":
                false_sources.add(src_key)

        for ck in condition_keys:
            has_true = ck in true_sources
            has_false = ck in false_sources
            if not has_true and not has_false:
                continue
            missing = []
            if not has_true:
                missing.append("是（true）")
            if not has_false:
                missing.append("否（false）")
            if missing:
                return f"条件节点「{ck}」缺少分支边：{'、'.join(missing)}"

        return None

    async def batch_create_edges(
        self, db: AsyncSession, flow_id: int, edges_data: List[FlowEdgeCreate]
    ) -> List[FlowEdge]:
        """
        批量创建边

        一次性创建多条边，通常用于保存整个流程设计。
        所有边会被自动关联到指定的流程。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID，所有边将关联到此流程
            edges_data: 边创建数据列表

        Returns:
            List[FlowEdge]: 创建的边对象列表

        Note:
            此方法会覆盖 edges_data 中的 flow_id 字段，
            确保所有边都关联到正确的流程。

        Example:
            ```python
            edges = await service.batch_create_edges(db, flow_id=1, edges_data=[
                FlowEdgeCreate(source_node_key="start", target_node_key="node_1"),
                FlowEdgeCreate(source_node_key="node_1", target_node_key="end"),
            ])
            ```
        """
        edges = []
        for edge_data in edges_data:
            edge_data.flow_id = flow_id
            edge = edge_data.to_model(FlowEdge)
            edges.append(edge)
            db.add(edge)
        await db.commit()
        for edge in edges:
            await db.refresh(edge)
        return edges

    async def delete_with_cascade(self, db: AsyncSession, flow_id: int) -> None:
        """
        级联删除流程及其所有关联数据

        删除顺序:
        1. 查询所有关联的 flow_execution.id
        2. 物理删除 langgraph_checkpoint_write
        3. 物理删除 langgraph_checkpoint_blob
        4. 物理删除 langgraph_checkpoint
        5. 软删除 conversation_message
        6. 软删除 flow_execution
        7. 软删除 flow_edge
        8. 软删除 flow_node
        9. 软删除 flow

        Args:
            db: 数据库异步会话
            flow_id: 流程ID

        Raises:
            HTTPException: 流程不存在时抛出 404 错误

        Example:
            ```python
            await service.delete_with_cascade(db, flow_id=1)
            ```
        """

        flow = await self.get_by_id(db, flow_id, raise_not_found=True)

        if getattr(flow, "is_builtin", 0) == 1:
            raise ValueError("内置助手不可被删除")

        execution_ids = []
        executions_query = select(FlowExecution.id).where(
            FlowExecution.flow_id == flow_id, FlowExecution.is_delete == 0
        )
        executions_result = await db.execute(executions_query)
        execution_ids = [str(e[0]) for e in executions_result.fetchall()]

        if execution_ids:
            await db.execute(
                delete(CheckpointWrite).where(
                    CheckpointWrite.thread_id.in_(execution_ids)
                )
            )
            await db.execute(
                delete(CheckpointBlob).where(
                    CheckpointBlob.thread_id.in_(execution_ids)
                )
            )
            await db.execute(
                delete(CheckpointModel).where(
                    CheckpointModel.thread_id.in_(execution_ids)
                )
            )
            await db.execute(
                update(ConversationMessage)
                .where(ConversationMessage.execution_id.in_(execution_ids))
                .values(is_delete=1, modify_time=datetime.now())
            )

        await db.execute(
            update(FlowExecution)
            .where(FlowExecution.flow_id == flow_id)
            .values(is_delete=1, modify_time=datetime.now())
        )

        await db.execute(
            update(FlowEdge)
            .where(FlowEdge.flow_id == flow_id)
            .values(is_delete=1, modify_time=datetime.now())
        )

        await db.execute(
            update(FlowNode)
            .where(FlowNode.flow_id == flow_id)
            .values(is_delete=1, modify_time=datetime.now())
        )

        flow.is_delete = 1
        self._set_modifier_fields(flow)

        await db.commit()

    # ---- AI 流程生成专用方法 ----

    @staticmethod
    def _sync_schema_to_nodes(
        ai_nodes: list[dict],
        input_schema: dict | None,
        output_schema: dict | None,
    ) -> None:
        """将 input_schema/output_schema 同步到 start/end 节点的 base_config"""
        if input_schema and input_schema.get("fields"):
            for n in ai_nodes:
                if n.get("node_type") == NodeType.START.value:
                    cfg = n.get("base_config") or {}
                    if not cfg.get("input_variables"):
                        cfg["input_variables"] = input_schema["fields"]
                        n["base_config"] = cfg
                    break

        if output_schema and output_schema.get("fields"):
            for n in ai_nodes:
                if n.get("node_type") == NodeType.END.value:
                    cfg = n.get("base_config") or {}
                    existing = cfg.get("output_variables")
                    if not existing or len(existing) == 0:
                        cfg["output_variables"] = [
                            {
                                "name": f.get("name", ""),
                                "source": f.get("description", ""),
                                "type": f.get("type", "string"),
                            }
                            for f in output_schema["fields"]
                        ]
                        n["base_config"] = cfg
                    break

    async def generate_flow(
        self,
        db: AsyncSession,
        name: str,
        flow_type: str,
        description: str | None,
        input_schema: dict | None,
        output_schema: dict | None,
        ai_nodes: list[dict],
        ai_edges: list[dict],
    ) -> Flow:
        """一站式创建流程：创建 Flow → 批量创建 Nodes → 批量创建 Edges。

        Args:
            db: 数据库异步会话
            name: 流程名称
            flow_type: 流程类型（flow/agent）
            description: 流程描述
            input_schema: 输入参数定义（dict）
            output_schema: 输出参数定义（dict）
            ai_nodes: AI 节点列表（dict 格式）
            ai_edges: AI 边列表（dict 格式）

        Returns:
            Flow: 创建完成的流程对象（含 nodes 和 edges）
        """
        flow_data = FlowCreate(
            name=name,
            flow_type=flow_type,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
        )
        flow = await self.create(db, flow_data)

        self._sync_schema_to_nodes(ai_nodes, input_schema, output_schema)

        for n in ai_nodes:
            if n.get("node_type") == "card":
                ref_id = n.get("ref_flow_id")
                if not ref_id and isinstance(n.get("base_config"), dict):
                    ref_id = n["base_config"].get("ref_flow_id")
                if ref_id:
                    await self.check_circular_card_refs(db, flow.id, ref_id)

        nodes_create = []
        for n in ai_nodes:
            nodes_create.append(
                FlowNodeCreate(
                    flow_id=flow.id,
                    node_type=n["node_type"],
                    node_key=n["node_key"],
                    node_name=n.get("node_name"),
                    position_x=n.get("position_x", 0),
                    position_y=n.get("position_y", 0),
                    base_config=n.get("base_config"),
                    ref_flow_id=n.get("ref_flow_id"),
                )
            )

        edges_create = []
        for e in ai_edges:
            edges_create.append(
                FlowEdgeCreate(
                    flow_id=flow.id,
                    source_node_key=e["source_node_key"],
                    target_node_key=e["target_node_key"],
                    source_handle=e.get("source_handle"),
                    target_handle=e.get("target_handle"),
                    condition=e.get("condition"),
                    label=e.get("label"),
                )
            )

        if nodes_create:
            await self.batch_create_nodes(db, flow.id, nodes_create)
        if edges_create:
            error = await self.validate_no_tool_in_flow_edges(db, flow.id, edges_create)
            if error:
                raise ValueError(error)
            await self.batch_create_edges(db, flow.id, edges_create)

        return await self.get_with_nodes_and_edges(db, flow.id)

    async def full_update_flow(
        self,
        db: AsyncSession,
        flow_id: int,
        name: str | None,
        description: str | None,
        input_schema: dict | None,
        output_schema: dict | None,
        ai_nodes: list[dict],
        ai_edges: list[dict],
    ) -> Flow:
        """全量更新流程：更新元数据 → 差异对比 Nodes/Edges → 增删改。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            name: 流程名称
            description: 流程描述
            input_schema: 输入参数定义（dict）
            output_schema: 输出参数定义（dict）
            ai_nodes: AI 节点列表（全量，dict 格式）
            ai_edges: AI 边列表（全量，dict 格式）

        Returns:
            Flow: 更新后的流程对象（含 nodes 和 edges）

        Raises:
            ValueError: 流程不存在
        """
        await self.get_by_id(db, flow_id, raise_not_found=True)

        self._sync_schema_to_nodes(ai_nodes, input_schema, output_schema)

        for n in ai_nodes:
            if n.get("node_type") == "card":
                ref_id = n.get("ref_flow_id")
                if not ref_id and isinstance(n.get("base_config"), dict):
                    ref_id = n["base_config"].get("ref_flow_id")
                if ref_id:
                    await self.check_circular_card_refs(db, flow_id, ref_id)

        if name is not None or description is not None:
            update_data = FlowUpdate(
                id=flow_id,
                name=name,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
            )
            await self.update(db, update_data)

        existing_nodes = await self._get_flow_nodes(db, flow_id)
        existing_edges = await self._get_flow_edges(db, flow_id)

        existing_node_map: dict[str, FlowNode] = {n.node_key: n for n in existing_nodes}
        existing_edge_map: dict[str, FlowEdge] = {}
        for edge in existing_edges:
            key = f"{edge.source_node_key}|{edge.target_node_key}|{edge.source_handle or ''}"
            existing_edge_map[key] = edge

        nodes_to_create: list[FlowNodeCreate] = []
        nodes_to_update: list[FlowNodeUpdate] = []
        nodes_to_delete: list[int] = []

        new_node_keys: set[str] = set()
        for n in ai_nodes:
            nk = n["node_key"]
            new_node_keys.add(nk)
            if nk in existing_node_map:
                existing = existing_node_map[nk]
                nodes_to_update.append(
                    FlowNodeUpdate(
                        id=existing.id,
                        flow_id=flow_id,
                        node_type=n["node_type"],
                        node_key=nk,
                        node_name=n.get("node_name"),
                        position_x=n.get("position_x", 0),
                        position_y=n.get("position_y", 0),
                        base_config=n.get("base_config"),
                        ref_flow_id=n.get("ref_flow_id"),
                    )
                )
            else:
                nodes_to_create.append(
                    FlowNodeCreate(
                        flow_id=flow_id,
                        node_type=n["node_type"],
                        node_key=nk,
                        node_name=n.get("node_name"),
                        position_x=n.get("position_x", 0),
                        position_y=n.get("position_y", 0),
                        base_config=n.get("base_config"),
                        ref_flow_id=n.get("ref_flow_id"),
                    )
                )

        for nk, existing in existing_node_map.items():
            if nk not in new_node_keys:
                nodes_to_delete.append(existing.id)

        edges_to_create: list[FlowEdgeCreate] = []
        edges_to_update: list[FlowEdgeUpdate] = []
        edges_to_delete: list[int] = []

        new_edge_keys: set[str] = set()
        for e in ai_edges:
            ek = f"{e['source_node_key']}|{e['target_node_key']}|{e.get('source_handle') or ''}"
            new_edge_keys.add(ek)
            if ek in existing_edge_map:
                existing = existing_edge_map[ek]
                edges_to_update.append(
                    FlowEdgeUpdate(
                        id=existing.id,
                        flow_id=flow_id,
                        source_node_key=e["source_node_key"],
                        target_node_key=e["target_node_key"],
                        source_handle=e.get("source_handle"),
                        target_handle=e.get("target_handle"),
                        condition=e.get("condition"),
                        label=e.get("label"),
                    )
                )
            else:
                edges_to_create.append(
                    FlowEdgeCreate(
                        flow_id=flow_id,
                        source_node_key=e["source_node_key"],
                        target_node_key=e["target_node_key"],
                        source_handle=e.get("source_handle"),
                        target_handle=e.get("target_handle"),
                        condition=e.get("condition"),
                        label=e.get("label"),
                    )
                )

        for ek, existing in existing_edge_map.items():
            if ek not in new_edge_keys:
                edges_to_delete.append(existing.id)

        for nid in nodes_to_delete:
            await self.delete_node(db, nid)
        for eid in edges_to_delete:
            await self.delete_edge(db, eid)
        if nodes_to_create:
            await self.batch_create_nodes(db, flow_id, nodes_to_create)
        for nu in nodes_to_update:
            await self.update_node(db, nu)
        if edges_to_create:
            error = await self.validate_no_tool_in_flow_edges(
                db, flow_id, edges_to_create
            )
            if error:
                raise ValueError(error)
            await self.batch_create_edges(db, flow_id, edges_to_create)
        for eu in edges_to_update:
            await self.update_edge(db, eu)

        return await self.get_with_nodes_and_edges(db, flow_id)

    async def add_single_node(
        self,
        db: AsyncSession,
        flow_id: int,
        node_type: str,
        node_key: str | None = None,
        node_name: str | None = None,
        position_x: float | None = None,
        position_y: float | None = None,
        base_config: dict | None = None,
        ref_flow_id: int | None = None,
    ) -> FlowNode:
        """增量添加单个节点到指定流程。

        node_key 为空时自动生成 {node_type}_{毫秒时间戳} 格式。
        自动校验 node_key 在同一流程内不重复。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            node_type: 节点类型
            node_key: 节点唯一标识，为空时自动生成
            node_name: 节点显示名称
            position_x: X坐标
            position_y: Y坐标
            base_config: 节点配置
            ref_flow_id: 引用的流程ID

        Returns:
            FlowNode: 创建的节点

        Raises:
            ValueError: node_key 重复或流程不存在
        """
        import time

        if not node_key:
            ts = int(time.time() * 1000)
            node_key = f"{node_type}_{ts}"

        existing_nodes = await self._get_flow_nodes(db, flow_id)
        existing_keys = {n.node_key for n in existing_nodes}
        if node_key in existing_keys:
            base_key = node_key
            idx = 2
            while f"{base_key}_{idx}" in existing_keys:
                idx += 1
            node_key = f"{base_key}_{idx}"

        node_data = FlowNodeCreate(
            flow_id=flow_id,
            node_type=node_type,
            node_key=node_key,
            node_name=node_name,
            position_x=position_x or 0,
            position_y=position_y or 0,
            base_config=base_config,
            ref_flow_id=ref_flow_id,
        )
        return await self.create_node(db, node_data)

    async def update_node_by_key(
        self,
        db: AsyncSession,
        flow_id: int,
        node_key: str,
        update_fields: dict,
    ) -> Optional[FlowNode]:
        """按 node_key 增量更新单个节点。

        只更新 update_fields 中传入的字段（exclude_unset 语义）。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            node_key: 节点唯一标识
            update_fields: 要更新的字段字典

        Returns:
            Optional[FlowNode]: 更新后的节点，不存在时返回 None

        Raises:
            ValueError: 节点不存在或不在该流程中
        """
        existing_nodes = await self._get_flow_nodes(db, flow_id)
        target = None
        for n in existing_nodes:
            if n.node_key == node_key:
                target = n
                break
        if target is None:
            raise ValueError(f"节点「{node_key}」不存在")

        node_update = FlowNodeUpdate(id=target.id, **update_fields)
        return await self.update_node(db, node_update)

    async def delete_node_by_key(
        self,
        db: AsyncSession,
        flow_id: int,
        node_key: str,
    ) -> None:
        """按 node_key 删除节点，级联删除关联的边。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            node_key: 节点唯一标识

        Raises:
            ValueError: 节点不存在或不在该流程中
        """
        existing_nodes = await self._get_flow_nodes(db, flow_id)
        target = None
        for n in existing_nodes:
            if n.node_key == node_key:
                target = n
                break
        if target is None:
            raise ValueError(f"节点「{node_key}」不存在")

        related_edges = await self._get_flow_edges(db, flow_id)
        for edge in related_edges:
            if edge.source_node_key == node_key or edge.target_node_key == node_key:
                await self.delete_edge(db, edge.id)

        await self.delete_node(db, target.id)

    async def add_single_edge(
        self,
        db: AsyncSession,
        flow_id: int,
        source_node_key: str,
        target_node_key: str,
        source_handle: str | None = None,
        target_handle: str | None = None,
        condition: dict | None = None,
        label: str | None = None,
    ) -> FlowEdge:
        """增量添加单条边到指定流程。

        自动校验 source_node_key 和 target_node_key 在流程中存在。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            source_node_key: 源节点key
            target_node_key: 目标节点key
            source_handle: 源节点handle ID
            target_handle: 目标节点handle ID
            condition: 条件表达式
            label: 边标签

        Returns:
            FlowEdge: 创建的边

        Raises:
            ValueError: 源/目标节点不存在或自连接
        """
        existing_nodes = await self._get_flow_nodes(db, flow_id)
        existing_keys = {n.node_key for n in existing_nodes}

        if source_node_key not in existing_keys:
            raise ValueError(f"边的源节点「{source_node_key}」不存在")
        if target_node_key not in existing_keys:
            raise ValueError(f"边的目标节点「{target_node_key}」不存在")
        if source_node_key == target_node_key:
            raise ValueError(f"不允许自连接：节点「{source_node_key}」不能连接自身")

        is_tool_source = source_handle == "tools"
        is_tool_target = target_handle == "tools"
        if is_tool_source:
            if not is_tool_target:
                raise ValueError(
                    "工具边（tools）的 source_handle 和 target_handle 必须同时为 tools"
                )
        elif source_handle in ("true", "false"):
            if target_handle is not None:
                raise ValueError(
                    "条件分支边（true/false）的 target_handle 必须为空（连接标准输入）"
                )
        elif source_handle is None:
            if target_handle is not None:
                raise ValueError("标准数据流边的 target_handle 必须为空")

        if is_tool_source:
            key_to_type = {n.node_key: n.node_type for n in existing_nodes}
            target_type = key_to_type.get(target_node_key, "")
            if target_type != "llm":
                target_label = NODE_TYPE_LABELS.get(target_type, target_type)
                raise ValueError(
                    f"工具边只能连接到大模型调用节点，"
                    f"当前目标节点「{target_node_key}」类型为「{target_label}」"
                )
        else:
            error = await self.validate_no_tool_in_flow_edges(
                db,
                flow_id,
                [
                    type(
                        "E",
                        (),
                        {
                            "source_node_key": source_node_key,
                            "target_node_key": target_node_key,
                            "source_handle": source_handle,
                        },
                    )()
                ],
            )
            if error:
                raise ValueError(error)

        edge_data = FlowEdgeCreate(
            flow_id=flow_id,
            source_node_key=source_node_key,
            target_node_key=target_node_key,
            source_handle=source_handle,
            target_handle=target_handle,
            condition=condition,
            label=label,
        )
        return await self.create_edge(db, edge_data)

    async def _get_flow_nodes(self, db: AsyncSession, flow_id: int) -> list[FlowNode]:
        """获取流程的所有未删除节点"""
        query = select(FlowNode).where(
            FlowNode.flow_id == flow_id, FlowNode.is_delete == 0
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def _get_flow_edges(self, db: AsyncSession, flow_id: int) -> list[FlowEdge]:
        """获取流程的所有未删除边"""
        query = select(FlowEdge).where(
            FlowEdge.flow_id == flow_id, FlowEdge.is_delete == 0
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    def _check_agent_unique_for_batch(
        self,
        db: AsyncSession,
        flow_id: int,
        nodes_data: list[dict],
        existing_nodes: list,
    ) -> None:
        """Agent 批量添加节点时校验唯一性节点（start/end/llm）不能重复。

        同时检查 DB 已有节点和本次新增节点。
        """
        from app.models.flow_node import AGENT_UNIQUE_NODE_TYPES

        existing_types = {n.node_type for n in existing_nodes}
        new_types = [nd["node_type"] for nd in nodes_data]

        for ut in AGENT_UNIQUE_NODE_TYPES:
            already_exists = ut in existing_types
            new_count = new_types.count(ut)
            if already_exists and new_count > 0:
                label = NODE_TYPE_LABELS.get(ut, ut)
                raise ValueError(f"智能体已存在{label}节点，不能再添加")
            if new_count > 1:
                label = NODE_TYPE_LABELS.get(ut, ut)
                raise ValueError(f"智能体只能有一个{label}节点")

    async def batch_add_nodes(
        self, db: AsyncSession, flow_id: int, nodes_data: list[dict]
    ) -> list[dict]:
        """批量创建节点，自动处理 node_key 冲突（追加序号）。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            nodes_data: 节点数据列表（dict 格式）


        Returns:
            创建成功的节点信息列表 [{node_key, node_name, node_type}, ...]
        """
        import time

        from app.services.node_config_helper import fill_node_defaults, inject_llm_defaults
        from app.services.global_config_service import global_config_service

        existing_nodes = await self._get_flow_nodes(db, flow_id)
        existing_keys = {n.node_key for n in existing_nodes}

        flow = await self.get_by_id(db, flow_id)
        if flow and flow.flow_type == FlowType.AGENT.value:
            self._check_agent_unique_for_batch(db, flow_id, nodes_data, existing_nodes)

        global_cfg = await global_config_service.get_default_llm_config(db)

        nodes_to_create: list[FlowNodeCreate] = []
        results: list[dict] = []
        ts = int(time.time() * 1000)

        for i, nd in enumerate(nodes_data):
            node_type = nd["node_type"]
            node_key = nd.get("node_key")

            if not node_key:
                node_key = f"{node_type}_{ts}_{i:02x}"

            base_key = node_key
            idx = 2
            while node_key in existing_keys:
                node_key = f"{base_key}_{idx}"
                idx += 1
            existing_keys.add(node_key)

            bc = fill_node_defaults(node_type, nd.get("base_config"))
            if node_type in ("llm", "intent_router"):
                bc = inject_llm_defaults(bc, global_cfg)

            nodes_to_create.append(
                FlowNodeCreate(
                    flow_id=flow_id,
                    node_type=node_type,
                    node_key=node_key,
                    node_name=nd.get("node_name"),
                    position_x=nd.get("position_x", 0),
                    position_y=nd.get("position_y", 0),
                    base_config=bc,
                    ref_flow_id=nd.get("ref_flow_id"),
                )
            )
            results.append(
                {
                    "node_key": node_key,
                    "node_name": nd.get("node_name"),
                    "node_type": node_type,
                }
            )

        if nodes_to_create:
            await self.batch_create_nodes(db, flow_id, nodes_to_create)

        return results

    async def batch_update_nodes_by_keys(
        self, db: AsyncSession, flow_id: int, nodes_data: list[dict]
    ) -> int:
        """按 node_key 批量更新节点配置（node_name、base_config、position）。

        base_config 为整体替换，非合并。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            nodes_data: 节点配置列表，每项含 node_key 及要更新的字段

        Returns:
            更新的节点数量

        Raises:
            ValueError: 节点不存在
        """
        existing_nodes = await self._get_flow_nodes(db, flow_id)
        key_to_node = {n.node_key: n for n in existing_nodes}

        for item in nodes_data:
            key = item["node_key"]
            if key not in key_to_node:
                raise ValueError(f"节点「{key}」不存在")
            update_fields = {
                k: v for k, v in item.items() if k != "node_key" and v is not None
            }
            if not update_fields:
                continue
            node_update = FlowNodeUpdate(
                id=key_to_node[key].id, flow_id=flow_id, **update_fields
            )
            await self.update_node(db, node_update)

        return len(nodes_data)

    async def batch_delete_nodes_by_keys(
        self, db: AsyncSession, flow_id: int, node_keys: list[str]
    ) -> int:
        """按 node_key 批量删除节点，级联删除关联边。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            node_keys: 要删除的节点 node_key 列表

        Returns:
            删除的节点数量

        Raises:
            ValueError: 节点不存在
        """
        existing_nodes = await self._get_flow_nodes(db, flow_id)
        key_to_node = {n.node_key: n for n in existing_nodes}

        for key in node_keys:
            if key not in key_to_node:
                raise ValueError(f"节点「{key}」不存在")

        keys_set = set(node_keys)
        existing_edges = await self._get_flow_edges(db, flow_id)
        edges_to_delete = [
            e
            for e in existing_edges
            if e.source_node_key in keys_set or e.target_node_key in keys_set
        ]

        for edge in edges_to_delete:
            await self.delete_edge(db, edge.id)

        for key in node_keys:
            await self.delete_node(db, key_to_node[key].id)

        return len(node_keys)

    async def batch_delete_edges_by_identifiers(
        self, db: AsyncSession, flow_id: int, edges_data: list[dict]
    ) -> int:
        """按边标识批量删除边。

        每条边通过 {source_node_key, target_node_key, source_handle?} 标识。

        Args:
            db: 数据库异步会话
            flow_id: 流程ID
            edges_data: 边标识列表

        Returns:
            删除的边数量

        Raises:
            ValueError: 边不存在
        """
        existing_edges = await self._get_flow_edges(db, flow_id)

        deleted_count = 0
        for edge_data in edges_data:
            src = edge_data["source_node_key"]
            tgt = edge_data["target_node_key"]
            src_handle = edge_data.get("source_handle") or "default"

            found = False
            for edge in existing_edges:
                edge_src_handle = edge.source_handle or "default"
                if (
                    edge.source_node_key == src
                    and edge.target_node_key == tgt
                    and edge_src_handle == src_handle
                ):
                    await self.delete_edge(db, edge.id)
                    deleted_count += 1
                    found = True
                    break

            if not found:
                raise ValueError(f"边「{src} → {tgt}」({src_handle}) 不存在")

        return deleted_count


# 全局单例实例
# 使用单例模式避免重复创建服务实例
flow_service = FlowService()
