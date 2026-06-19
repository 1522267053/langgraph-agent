"""
流程模板服务模块

提供内置流程/智能体模板，用户可从模板快速创建流程。
"""

from typing import Optional
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.flow_service import flow_service
from app.schemas.flow_schema import FlowCreate
from app.schemas.flow_edge_schema import FlowEdgeCreate


class TemplateNode(BaseModel):
    """模板节点"""

    node_type: str = Field(..., description="节点类型")
    node_key: str = Field(..., description="节点标识")
    node_name: str = Field(..., description="节点名称")
    position_x: int = Field(0, description="X坐标")
    position_y: int = Field(0, description="Y坐标")
    base_config: dict = Field(default_factory=dict, description="节点配置")


class TemplateEdge(BaseModel):
    """模板边"""

    source_node_key: str = Field(..., description="源节点")
    target_node_key: str = Field(..., description="目标节点")
    source_handle: str = Field("default", description="源handle")
    target_handle: str = Field("default", description="目标handle")


class FlowTemplate(BaseModel):
    """流程模板"""

    id: str = Field(..., description="模板标识")
    name: str = Field(..., description="模板名称")
    description: str = Field("", description="模板描述")
    flow_type: str = Field("flow", description="类型: flow/agent")
    node_count: int = Field(0, description="节点数量")
    nodes: list[TemplateNode] = Field(default_factory=list, description="节点列表")
    edges: list[TemplateEdge] = Field(default_factory=list, description="边列表")
    input_schema: dict = Field(default_factory=dict, description="输入参数定义")
    output_schema: dict = Field(default_factory=dict, description="输出参数定义")


# ---- 模板定义 ----

FLOW_TEMPLATES: dict[str, FlowTemplate] = {}


def _register(t: FlowTemplate) -> FlowTemplate:
    t.node_count = len(t.nodes)
    FLOW_TEMPLATES[t.id] = t
    return t


# 1. 空白流程
_register(
    FlowTemplate(
        id="blank_flow",
        name="空白流程",
        description="只有开始和结束节点的空白流程",
        flow_type="flow",
        nodes=[
            TemplateNode(node_type="start", node_key="start", node_name="开始节点", position_x=50, position_y=200),
            TemplateNode(node_type="end", node_key="end", node_name="结束节点", position_x=350, position_y=200),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="end"),
        ],
        input_schema={
            "fields": [
                {"name": "message", "type": "string", "description": "用户消息", "required": True}
            ]
        },
    )
)

# 2. RAG 问答
_register(
    FlowTemplate(
        id="rag_qa",
        name="RAG 问答",
        description="接收用户问题 → 检索知识库 → LLM 总结回答",
        flow_type="flow",
        nodes=[
            TemplateNode(node_type="start", node_key="start", node_name="开始节点", position_x=50, position_y=200),
            TemplateNode(node_type="knowledge", node_key="knowledge", node_name="知识库检索", position_x=250, position_y=50, base_config={"top_k": 5}),
            TemplateNode(node_type="llm", node_key="llm", node_name="LLM 总结", position_x=250, position_y=200, base_config={"user_prompt": "根据知识库内容回答用户问题：\n\n{{input.message}}"}),
            TemplateNode(node_type="end", node_key="end", node_name="结束节点", position_x=450, position_y=200),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="llm"),
            TemplateEdge(source_node_key="knowledge", target_node_key="llm", source_handle="tools", target_handle="tools"),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
        ],
        input_schema={
            "fields": [
                {"name": "message", "type": "string", "description": "用户问题", "required": True}
            ]
        },
    )
)

# 3. 智能客服
_register(
    FlowTemplate(
        id="customer_service",
        name="智能客服",
        description="意图路由 → 自动回复或转人工",
        flow_type="flow",
        nodes=[
            TemplateNode(node_type="start", node_key="start", node_name="开始节点", position_x=50, position_y=200),
            TemplateNode(node_type="intent_router", node_key="intent_router", node_name="意图路由", position_x=250, position_y=200, base_config={
                "intents": [
                    {"key": "auto_reply", "description": "可自动回复的常见问题", "examples": [], "rule": {"keywords": [], "regex_patterns": []}},
                    {"key": "transfer", "description": "需转人工处理的复杂问题", "examples": [], "rule": {"keywords": [], "regex_patterns": []}},
                ],
            }),
            TemplateNode(node_type="llm", node_key="llm", node_name="LLM 自动回复", position_x=150, position_y=400, base_config={"user_prompt": "你是一个智能客服助手，请友好地回答用户问题。\n\n用户问题：{{input.message}}"}),
            TemplateNode(node_type="human", node_key="human", node_name="转人工", position_x=400, position_y=400),
            TemplateNode(node_type="end", node_key="end", node_name="结束节点", position_x=275, position_y=550),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="intent_router"),
            TemplateEdge(source_node_key="intent_router", target_node_key="llm", source_handle="auto_reply", target_handle="default"),
            TemplateEdge(source_node_key="intent_router", target_node_key="human", source_handle="transfer", target_handle="default"),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
            TemplateEdge(source_node_key="human", target_node_key="end"),
        ],
        input_schema={
            "fields": [
                {"name": "message", "type": "string", "description": "用户消息", "required": True}
            ]
        },
    )
)

# 4. 数据处理
_register(
    FlowTemplate(
        id="data_pipeline",
        name="数据处理",
        description="接收输入 → Python 处理 → 输出结果",
        flow_type="flow",
        nodes=[
            TemplateNode(node_type="start", node_key="start", node_name="开始节点", position_x=50, position_y=200),
            TemplateNode(node_type="python", node_key="python", node_name="Python 处理", position_x=250, position_y=200, base_config={"code": "def main(input_data):\n    print(\"hello\")\n    return \"\""}),
            TemplateNode(node_type="end", node_key="end", node_name="结束节点", position_x=450, position_y=200),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="python"),
            TemplateEdge(source_node_key="python", target_node_key="end"),
        ],
        input_schema={
            "fields": [
                {"name": "input_data", "type": "string", "description": "输入数据", "required": True}
            ]
        },
    )
)

# 5. 空白智能体
_register(
    FlowTemplate(
        id="blank_agent",
        name="空白智能体",
        description="只有 LLM 的空白智能体",
        flow_type="agent",
        nodes=[
            TemplateNode(node_type="start", node_key="start", node_name="开始节点", position_x=50, position_y=200),
            TemplateNode(node_type="llm", node_key="llm", node_name="AI 助手", position_x=250, position_y=200, base_config={"user_prompt": "{{input.message}}"}),
            TemplateNode(node_type="end", node_key="end", node_name="结束节点", position_x=450, position_y=200),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="llm"),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
        ],
        input_schema={
            "fields": [
                {"name": "message", "type": "string", "description": "用户消息", "required": True}
            ]
        },
    )
)

# 6. 知识库助手
_register(
    FlowTemplate(
        id="knowledge_agent",
        name="知识库助手",
        description="LLM 搭配知识库检索工具",
        flow_type="agent",
        nodes=[
            TemplateNode(node_type="start", node_key="start", node_name="开始节点", position_x=50, position_y=200),
            TemplateNode(node_type="llm", node_key="llm", node_name="AI 助手", position_x=250, position_y=200, base_config={"user_prompt": "根据知识库内容回答用户问题：\n\n{{input.message}}"}),
            TemplateNode(node_type="knowledge", node_key="knowledge", node_name="知识库检索", position_x=250, position_y=50, base_config={"top_k": 5}),
            TemplateNode(node_type="end", node_key="end", node_name="结束节点", position_x=450, position_y=200),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="llm"),
            TemplateEdge(source_node_key="knowledge", target_node_key="llm", source_handle="tools", target_handle="tools"),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
        ],
        input_schema={
            "fields": [
                {"name": "message", "type": "string", "description": "用户消息", "required": True}
            ]
        },
    )
)

# 7. 全能助手
_register(
    FlowTemplate(
        id="full_agent",
        name="全能助手",
        description="LLM 搭配知识库、Python、Shell 等多种工具",
        flow_type="agent",
        nodes=[
            TemplateNode(node_type="start", node_key="start", node_name="开始节点", position_x=50, position_y=200),
            TemplateNode(node_type="llm", node_key="llm", node_name="AI 助手", position_x=250, position_y=200, base_config={"user_prompt": "{{input.message}}"}),
            TemplateNode(node_type="knowledge", node_key="knowledge", node_name="知识库检索", position_x=100, position_y=50, base_config={"top_k": 5}),
            TemplateNode(node_type="python", node_key="python", node_name="Python 执行", position_x=250, position_y=50, base_config={"code": "def main(input_data):\n    print(\"hello\")\n    return \"\""}),
            TemplateNode(node_type="shell", node_key="shell", node_name="Shell 命令", position_x=400, position_y=50, base_config={"command": "dir /b\necho Done"}),
            TemplateNode(node_type="end", node_key="end", node_name="结束节点", position_x=450, position_y=200),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="llm"),
            TemplateEdge(source_node_key="knowledge", target_node_key="llm", source_handle="tools", target_handle="tools"),
            TemplateEdge(source_node_key="python", target_node_key="llm", source_handle="tools", target_handle="tools"),
            TemplateEdge(source_node_key="shell", target_node_key="llm", source_handle="tools", target_handle="tools"),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
        ],
        input_schema={
            "fields": [
                {"name": "message", "type": "string", "description": "用户消息", "required": True}
            ]
        },
    )
)


def get_templates(flow_type: Optional[str] = None) -> list[dict]:
    """获取模板列表"""
    result = []
    for t in FLOW_TEMPLATES.values():
        if flow_type and t.flow_type != flow_type:
            continue
        result.append(
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "flow_type": t.flow_type,
                "node_count": t.node_count,
            }
        )
    return result


def get_template(template_id: str) -> Optional[FlowTemplate]:
    """获取模板详情"""
    return FLOW_TEMPLATES.get(template_id)


async def create_from_template(
    db: AsyncSession,
    template_id: str,
    name: str,
    description: Optional[str] = None,
) -> int:
    """从模板创建流程"""
    from app.services.node_config_helper import fill_node_defaults

    template = get_template(template_id)
    if not template:
        raise ValueError(f"模板不存在: {template_id}")

    flow_data = FlowCreate(
        name=name,
        description=description or template.description,
        flow_type=template.flow_type,
        input_schema=template.input_schema or None,
        output_schema=template.output_schema or None,
    )
    flow = await flow_service.create(db, flow_data)

    nodes_data = []
    for n in template.nodes:
        nd = n.model_dump()
        nd["base_config"] = fill_node_defaults(n.node_type, nd.get("base_config"))
        nodes_data.append(nd)
    await flow_service.batch_add_nodes(db, flow.id, nodes_data)

    edges_create = [
        FlowEdgeCreate(
            flow_id=flow.id,
            source_node_key=e.source_node_key,
            target_node_key=e.target_node_key,
            source_handle=e.source_handle,
            target_handle=e.target_handle,
        )
        for e in template.edges
    ]
    await flow_service.batch_create_edges(db, flow.id, edges_create)

    return flow.id
