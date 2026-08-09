"""
流程模板服务模块

提供内置流程/智能体模板，用户可从模板快速创建流程。
"""

from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.flow_edge_schema import FlowEdgeCreate
from app.schemas.flow_schema import FieldType, FlowCreate, FlowIOField, FlowIOSchema
from app.services.flow_service import flow_service


class TemplateNode(BaseModel):
    """模板节点"""

    node_type: str = Field(..., description="节点类型")
    node_key: str = Field(..., description="节点标识")
    node_name: str = Field(..., description="节点名称")
    position_x: int = Field(0, description="X坐标")
    position_y: int = Field(0, description="Y坐标")
    base_config: dict = Field(default_factory=dict, description="节点配置")
    ref_flow_id: Optional[int] = Field(
        default=None, description="引用流程ID（card节点用）"
    )


class TemplateEdge(BaseModel):
    """模板边"""

    source_node_key: str = Field(..., description="源节点")
    target_node_key: str = Field(..., description="目标节点")
    source_handle: str = Field(default="default", description="源handle")
    target_handle: str = Field(default="default", description="目标handle")


class FlowTemplate(BaseModel):
    """流程模板"""

    id: str = Field(..., description="模板标识")
    name: str = Field(..., description="模板名称")
    description: str = Field(default="", description="模板描述")
    flow_type: str = Field(default="flow", description="类型: flow/agent")
    node_count: int = Field(default=0, description="节点数量")
    nodes: list[TemplateNode] = Field(default_factory=list, description="节点列表")
    edges: list[TemplateEdge] = Field(default_factory=list, description="边列表")
    input_schema: FlowIOSchema = Field(
        default_factory=FlowIOSchema, description="输入参数定义"
    )
    output_schema: FlowIOSchema = Field(
        default_factory=FlowIOSchema, description="输出参数定义"
    )
    suggested_prompts: Optional[list[str]] = Field(
        default=None, description="建议提示词列表（仅 agent 有效）"
    )


# ---- 模板内容常量 ----

_SYSTEM_PROMPT_RAG = (
    "你是一个知识库问答助手。回答用户问题前，请先调用知识库检索工具搜索相关资料，"
    "然后基于检索到的内容进行回答。如果检索结果中没有相关信息，请如实告知用户。"
    "请用中文回复。"
)

_SYSTEM_PROMPT_CUSTOMER_SERVICE = (
    "你是一名专业的智能客服。请友好、专业、简洁地回答用户问题。\n"
    "回答要求：\n"
    "- 准确理解用户意图后给出清晰回答\n"
    "- 涉及退款、投诉、纠纷等敏感问题时，建议用户转接人工客服\n"
    "请用中文回复。"
)

_SYSTEM_PROMPT_BLANK_AGENT = (
    "你是一个智能AI助手，能够回答问题、提供建议并帮助用户完成各类任务。"
    "请根据用户需求提供清晰、准确、有条理的回复。请用中文回复。"
)

_SYSTEM_PROMPT_KNOWLEDGE_AGENT = (
    "你是一个知识库助手。系统已根据用户消息自动检索了相关知识库内容（见下方预检索结果）。\n"
    "请优先基于预检索结果回答。如果结果不够或需要补充信息，"
    "可以再使用知识库检索工具进行搜索。\n"
    "请用中文回复。"
)

_SYSTEM_PROMPT_FULL_AGENT = (
    "你是一个全能AI助手，拥有以下全部工具能力：\n"
    "- 知识库检索：搜索知识库获取专业信息\n"
    "- Python 代码执行：编写和运行代码处理数据\n"
    "- Shell 命令：执行系统命令、管理文件\n"
    "- API 调用：发起 HTTP 请求获取外部数据\n"
    "- MCP 工具：调用已配置的 MCP 服务\n"
    "- 技能加载：使用已配置的技能\n"
    "- 记忆管理：存储和检索长期记忆\n"
    "- 任务计划：创建和管理待办任务\n"
    "- 日程管理：管理日程安排\n"
    "- 子Agent：委派任务给其他智能体\n\n"
    "请根据用户需求选择最合适的工具完成任务。请用中文回复。"
)

_CODE_DATA_PIPELINE = """def main(input_data):
    import json
    try:
        data = json.loads(input_data) if isinstance(input_data, str) else input_data
        type_map = {dict: "dict", list: "list", str: "str", int: "int", float: "float"}
        if isinstance(data, dict):
            return {
                "status": "success",
                "field_count": len(data),
                "fields": list(data.keys()),
            }
        return {
            "status": "success",
            "type": type_map.get(type(data), "unknown"),
            "preview": str(data)[:200],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}"""

_CODE_PYTHON_TOOL = "def main(input_data):\n    return input_data"

_SYSTEM_PROMPT_DEMO = (
    "你是一个多功能演示智能体，集成了知识库检索、Python 代码执行、Shell 命令、"
    "MCP 工具、技能加载、记忆管理、任务计划、日程管理和子 Agent 等多种工具能力。\n"
    "请根据用户需求灵活选择合适的工具完成任务。请用中文回复。"
)

_CODE_PYTHON_DATA = (
    "def main(input_data):\n"
    '    return {"processed": True, "preview": str(input_data)[:200]}'
)

_CODE_LOOP_SUB = 'def main(item):\n    return f"已处理: {item}"'


# ---- 模板定义 ----

FLOW_TEMPLATES: dict[str, FlowTemplate] = {}


def _register(t: FlowTemplate) -> FlowTemplate:
    t.node_count = len(t.nodes)
    FLOW_TEMPLATES[t.id] = t
    return t


# ===== Flow 模板 =====

# 1. 空白流程
_register(
    FlowTemplate(
        id="blank_flow",
        name="空白流程",
        description="从零开始搭建流程，仅包含开始和结束节点",
        flow_type="flow",
        nodes=[
            TemplateNode(
                node_type="start",
                node_key="start",
                node_name="开始",
                position_x=50,
                position_y=200,
            ),
            TemplateNode(
                node_type="end",
                node_key="end",
                node_name="结束",
                position_x=450,
                position_y=200,
                base_config={
                    "output_variables": [
                        {"name": "result", "source": "input.message", "type": "string"}
                    ],
                },
            ),
        ],
        edges=[
            TemplateEdge(
                source_node_key="start",
                target_node_key="end",
            ),
        ],
        input_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="message",
                    type=FieldType.STRING,
                    description="用户消息",
                    required=True,
                )
            ]
        ),
        output_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="result", type=FieldType.STRING, description="输出结果"
                )
            ]
        ),
    )
)

# 2. RAG 知识库问答
_register(
    FlowTemplate(
        id="rag_qa",
        name="RAG 知识库问答",
        description="用户提问 → LLM 调用知识库检索工具 → 基于检索结果回答"
        "（创建后需为知识库节点选择目标知识库）",
        flow_type="flow",
        nodes=[
            TemplateNode(
                node_type="start",
                node_key="start",
                node_name="开始",
                position_x=50,
                position_y=200,
            ),
            TemplateNode(
                node_type="knowledge",
                node_key="knowledge",
                node_name="知识库检索",
                position_x=350,
                position_y=50,
                base_config={"top_k": 5},
            ),
            TemplateNode(
                node_type="llm",
                node_key="llm",
                node_name="LLM 回答",
                position_x=350,
                position_y=200,
                base_config={
                    "system_prompt": _SYSTEM_PROMPT_RAG,
                    "user_prompt": "{{input.message}}",
                },
            ),
            TemplateNode(
                node_type="end",
                node_key="end",
                node_name="结束",
                position_x=650,
                position_y=200,
                base_config={
                    "output_variables": [
                        {
                            "name": "result",
                            "source": "nodes.llm.result",
                            "type": "string",
                        }
                    ],
                },
            ),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="llm"),
            TemplateEdge(
                source_node_key="knowledge",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
        ],
        input_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="message",
                    type=FieldType.STRING,
                    description="用户问题",
                    required=True,
                )
            ]
        ),
        output_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="result", type=FieldType.STRING, description="LLM 回答"
                )
            ]
        ),
    )
)

# 3. 智能客服分流
_register(
    FlowTemplate(
        id="customer_service",
        name="智能客服分流",
        description="意图路由自动识别用户意图 → 常见问题由 LLM 自动回复 / "
        "复杂问题转人工处理",
        flow_type="flow",
        nodes=[
            TemplateNode(
                node_type="start",
                node_key="start",
                node_name="开始",
                position_x=50,
                position_y=200,
            ),
            TemplateNode(
                node_type="intent_router",
                node_key="intent_router",
                node_name="意图路由",
                position_x=350,
                position_y=200,
                base_config={
                    "input_variable": "input.message",
                    "intents": [
                        {
                            "key": "auto_reply",
                            "description": "可自动回复的常见问题：产品咨询、使用帮助、信息查询",
                            "examples": [
                                "这个产品怎么用",
                                "价格是多少",
                                "你们的服务时间",
                            ],
                            "rule": {
                                "keywords": [
                                    "怎么用",
                                    "价格",
                                    "多少钱",
                                    "服务时间",
                                    "地址",
                                    "电话",
                                    "帮助",
                                    "咨询",
                                ],
                                "regex_patterns": [],
                            },
                        },
                        {
                            "key": "transfer",
                            "description": "需转人工的复杂问题：投诉、退款、纠纷",
                            "examples": [
                                "我要投诉",
                                "要求退款",
                                "转人工",
                            ],
                            "rule": {
                                "keywords": [
                                    "投诉",
                                    "退款",
                                    "退货",
                                    "纠纷",
                                    "人工",
                                    "经理",
                                    "差评",
                                ],
                                "regex_patterns": [],
                            },
                        },
                    ],
                },
            ),
            TemplateNode(
                node_type="llm",
                node_key="llm",
                node_name="自动回复",
                position_x=650,
                position_y=120,
                base_config={
                    "system_prompt": _SYSTEM_PROMPT_CUSTOMER_SERVICE,
                    "user_prompt": "{{input.message}}",
                },
            ),
            TemplateNode(
                node_type="human",
                node_key="human",
                node_name="转人工",
                position_x=650,
                position_y=280,
            ),
            TemplateNode(
                node_type="end",
                node_key="end",
                node_name="结束",
                position_x=950,
                position_y=200,
                base_config={
                    "output_variables": [
                        {
                            "name": "result",
                            "source": "nodes.llm.result",
                            "type": "string",
                        }
                    ],
                },
            ),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="intent_router"),
            TemplateEdge(
                source_node_key="intent_router",
                target_node_key="llm",
                source_handle="auto_reply",
            ),
            TemplateEdge(
                source_node_key="intent_router",
                target_node_key="human",
                source_handle="transfer",
            ),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
            TemplateEdge(source_node_key="human", target_node_key="end"),
        ],
        input_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="message",
                    type=FieldType.STRING,
                    description="用户消息",
                    required=True,
                )
            ]
        ),
        output_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="result", type=FieldType.STRING, description="客服回复"
                )
            ]
        ),
    )
)

# 4. Python 数据处理
_register(
    FlowTemplate(
        id="data_pipeline",
        name="Python 数据处理",
        description="接收输入数据 → Python 脚本处理 → 返回结构化结果"
        "（可自定义处理逻辑）",
        flow_type="flow",
        nodes=[
            TemplateNode(
                node_type="start",
                node_key="start",
                node_name="开始",
                position_x=50,
                position_y=200,
            ),
            TemplateNode(
                node_type="python",
                node_key="python",
                node_name="Python 处理",
                position_x=350,
                position_y=200,
                base_config={
                    "code": _CODE_DATA_PIPELINE,
                    "timeout": 30,
                    "input_variables": [
                        {
                            "name": "input_data",
                            "source": "input.input_data",
                            "type": "string",
                        }
                    ],
                },
            ),
            TemplateNode(
                node_type="end",
                node_key="end",
                node_name="结束",
                position_x=650,
                position_y=200,
                base_config={
                    "output_variables": [
                        {
                            "name": "result",
                            "source": "nodes.python.result",
                            "type": "object",
                        }
                    ],
                },
            ),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="python"),
            TemplateEdge(source_node_key="python", target_node_key="end"),
        ],
        input_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="input_data",
                    type=FieldType.STRING,
                    description="输入数据（JSON 字符串或纯文本）",
                    required=True,
                )
            ]
        ),
        output_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="result", type=FieldType.OBJECT, description="处理结果"
                )
            ]
        ),
    )
)


# ===== Agent 模板 =====

# 5. 空白智能体
_register(
    FlowTemplate(
        id="blank_agent",
        name="空白智能体",
        description="通用对话智能体，支持自然语言交互，可按需扩展工具节点",
        flow_type="agent",
        nodes=[
            TemplateNode(
                node_type="start",
                node_key="start",
                node_name="开始",
                position_x=50,
                position_y=200,
            ),
            TemplateNode(
                node_type="llm",
                node_key="llm",
                node_name="AI 助手",
                position_x=350,
                position_y=200,
                base_config={
                    "system_prompt": _SYSTEM_PROMPT_BLANK_AGENT,
                    "user_prompt": "{{input.message}}",
                },
            ),
            TemplateNode(
                node_type="end",
                node_key="end",
                node_name="结束",
                position_x=650,
                position_y=200,
                base_config={
                    "output_variables": [
                        {
                            "name": "result",
                            "source": "nodes.llm.result",
                            "type": "string",
                        }
                    ],
                },
            ),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="llm"),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
        ],
        input_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="message",
                    type=FieldType.STRING,
                    description="用户消息",
                    required=True,
                )
            ]
        ),
        output_schema=FlowIOSchema(
            fields=[
                FlowIOField(name="result", type=FieldType.STRING, description="AI 回复")
            ]
        ),
        suggested_prompts=[
            "你好，请介绍一下你能做什么",
            "帮我写一段 Python 代码",
            "解释一下机器学习的基本概念",
            "给我一些时间管理的建议",
        ],
    )
)

# 6. 知识库助手
_register(
    FlowTemplate(
        id="knowledge_agent",
        name="知识库助手",
        description="拥有知识库检索能力的对话智能体：每轮自动预检索 + LLM 可补充搜索"
        "（创建后需选择目标知识库）",
        flow_type="agent",
        nodes=[
            TemplateNode(
                node_type="start",
                node_key="start",
                node_name="开始",
                position_x=50,
                position_y=200,
            ),
            TemplateNode(
                node_type="knowledge",
                node_key="knowledge",
                node_name="知识库检索",
                position_x=350,
                position_y=200,
                base_config={
                    "top_k": 5,
                    "input_variables": [
                        {
                            "name": "query",
                            "source": "input.message",
                            "type": "string",
                        }
                    ],
                },
            ),
            TemplateNode(
                node_type="llm",
                node_key="llm",
                node_name="AI 助手",
                position_x=650,
                position_y=200,
                base_config={
                    "system_prompt": _SYSTEM_PROMPT_KNOWLEDGE_AGENT,
                    "user_prompt": (
                        "用户问题：{{input.message}}\n\n"
                        "知识库预检索结果：\n{{nodes.knowledge.result}}"
                    ),
                },
            ),
            TemplateNode(
                node_type="end",
                node_key="end",
                node_name="结束",
                position_x=950,
                position_y=200,
                base_config={
                    "output_variables": [
                        {
                            "name": "result",
                            "source": "nodes.llm.result",
                            "type": "string",
                        }
                    ],
                },
            ),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="knowledge"),
            TemplateEdge(source_node_key="knowledge", target_node_key="llm"),
            TemplateEdge(
                source_node_key="knowledge",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
        ],
        input_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="message",
                    type=FieldType.STRING,
                    description="用户消息",
                    required=True,
                )
            ]
        ),
        output_schema=FlowIOSchema(
            fields=[
                FlowIOField(name="result", type=FieldType.STRING, description="AI 回复")
            ]
        ),
        suggested_prompts=[
            "帮我搜索产品使用文档",
            "公司的请假流程是什么？",
            "查找相关的技术规范",
            "有哪些常见问题及解决方案？",
        ],
    )
)

# 7. 全能助手
_register(
    FlowTemplate(
        id="full_agent",
        name="全能助手",
        description="集成全部工具能力的对话智能体：知识库、Python、Shell、API、MCP、"
        "技能、记忆、任务计划、日程、子Agent（mcp/skill/sub_agent 需创建后配置引用）",
        flow_type="agent",
        nodes=[
            TemplateNode(
                node_type="start",
                node_key="start",
                node_name="开始",
                position_x=50,
                position_y=200,
            ),
            TemplateNode(
                node_type="llm",
                node_key="llm",
                node_name="AI 助手",
                position_x=350,
                position_y=200,
                base_config={
                    "system_prompt": _SYSTEM_PROMPT_FULL_AGENT,
                    "user_prompt": "{{input.message}}",
                },
            ),
            # ---- 工具节点 第 1 行 ----
            TemplateNode(
                node_type="knowledge",
                node_key="knowledge",
                node_name="知识库检索",
                position_x=100,
                position_y=50,
                base_config={"top_k": 5},
            ),
            TemplateNode(
                node_type="python",
                node_key="python",
                node_name="Python 执行",
                position_x=350,
                position_y=50,
                base_config={"code": _CODE_PYTHON_TOOL, "timeout": 30},
            ),
            TemplateNode(
                node_type="shell",
                node_key="shell",
                node_name="Shell 命令",
                position_x=600,
                position_y=50,
                base_config={"command": "echo ready", "timeout": 30},
            ),
            TemplateNode(
                node_type="api",
                node_key="api",
                node_name="API 调用",
                position_x=850,
                position_y=50,
                base_config={
                    "api_url": "https://httpbin.org/get",
                    "method": "GET",
                    "headers": '{"Accept": "application/json"}',
                },
            ),
            TemplateNode(
                node_type="mcp",
                node_key="mcp",
                node_name="MCP 工具",
                position_x=1100,
                position_y=50,
                base_config={"mcp_server_ids": []},
            ),
            # ---- 工具节点 第 2 行 ----
            TemplateNode(
                node_type="skill",
                node_key="skill",
                node_name="技能",
                position_x=100,
                position_y=-50,
                base_config={"skill_ids": []},
            ),
            TemplateNode(
                node_type="memory",
                node_key="memory",
                node_name="记忆",
                position_x=350,
                position_y=-50,
            ),
            TemplateNode(
                node_type="todo",
                node_key="todo",
                node_name="任务计划",
                position_x=600,
                position_y=-50,
            ),
            TemplateNode(
                node_type="agenda",
                node_key="agenda",
                node_name="日程",
                position_x=850,
                position_y=-50,
            ),
            TemplateNode(
                node_type="sub_agent",
                node_key="sub_agent",
                node_name="子Agent",
                position_x=1100,
                position_y=-50,
                base_config={"agent_id": 0},
            ),
            # ---- 结束 ----
            TemplateNode(
                node_type="end",
                node_key="end",
                node_name="结束",
                position_x=650,
                position_y=200,
                base_config={
                    "output_variables": [
                        {
                            "name": "result",
                            "source": "nodes.llm.result",
                            "type": "string",
                        }
                    ],
                },
            ),
        ],
        edges=[
            TemplateEdge(source_node_key="start", target_node_key="llm"),
            TemplateEdge(
                source_node_key="knowledge",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="python",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="shell",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="api",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="mcp",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="skill",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="memory",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="todo",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="agenda",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="sub_agent",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(source_node_key="llm", target_node_key="end"),
        ],
        input_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="message",
                    type=FieldType.STRING,
                    description="用户消息",
                    required=True,
                )
            ]
        ),
        output_schema=FlowIOSchema(
            fields=[
                FlowIOField(name="result", type=FieldType.STRING, description="AI 回复")
            ]
        ),
        suggested_prompts=[
            "搜索知识库，解释产品核心功能",
            "用 Python 统计当前目录下的文件数量",
            "执行系统命令查看磁盘使用情况",
            "调用 API 获取外部数据并分析",
            "帮我创建一个任务计划",
            "读取并分析指定文件的内容",
        ],
    )
)

# 8. 节点全景示例
_register(
    FlowTemplate(
        id="all_nodes_demo",
        name="节点全景示例",
        description="展示所有节点类型的连接方式：意图路由分流 → 对话(LLM+全部工具) / "
        "数据处理(API+条件+人工) / 批量循环（sub_agent/mcp/skill 需创建后配置引用）",
        flow_type="flow",
        nodes=[
            # ---- 核心节点 ----
            TemplateNode(
                node_type="start",
                node_key="start",
                node_name="开始",
                position_x=50,
                position_y=100,
            ),
            TemplateNode(
                node_type="intent_router",
                node_key="intent_router",
                node_name="意图路由",
                position_x=350,
                position_y=100,
                base_config={
                    "input_variable": "input.message",
                    "intents": [
                        {
                            "key": "chat",
                            "description": "日常对话、问答、知识查询",
                            "examples": ["你好", "帮我查一下", "解释一下"],
                            "rule": {"keywords": [], "regex_patterns": []},
                        },
                        {
                            "key": "data",
                            "description": "数据获取与处理：调用API、条件判断",
                            "examples": ["获取数据", "调用接口"],
                            "rule": {
                                "keywords": ["数据", "接口", "api", "获取"],
                                "regex_patterns": [],
                            },
                        },
                        {
                            "key": "batch",
                            "description": "批量处理：循环执行任务",
                            "examples": ["批量处理", "循环执行"],
                            "rule": {
                                "keywords": ["批量", "循环", "遍历"],
                                "regex_patterns": [],
                            },
                        },
                    ],
                },
            ),
            TemplateNode(
                node_type="llm",
                node_key="llm",
                node_name="LLM 对话",
                position_x=700,
                position_y=100,
                base_config={
                    "system_prompt": _SYSTEM_PROMPT_DEMO,
                    "user_prompt": "{{input.message}}",
                },
            ),
            TemplateNode(
                node_type="end",
                node_key="end_chat",
                node_name="结束(对话)",
                position_x=1050,
                position_y=100,
                base_config={
                    "output_variables": [
                        {
                            "name": "result",
                            "source": "nodes.llm.result",
                            "type": "string",
                        }
                    ],
                },
            ),
            # ---- LLM 工具节点（tools 边连接到 LLM）----
            TemplateNode(
                node_type="knowledge",
                node_key="knowledge",
                node_name="知识库检索",
                position_x=450,
                position_y=-50,
                base_config={"top_k": 5},
            ),
            TemplateNode(
                node_type="python",
                node_key="python_tool",
                node_name="Python 工具",
                position_x=675,
                position_y=-50,
                base_config={"code": _CODE_PYTHON_TOOL, "timeout": 30},
            ),
            TemplateNode(
                node_type="shell",
                node_key="shell",
                node_name="Shell 命令",
                position_x=850,
                position_y=-50,
                base_config={"command": "echo ready", "timeout": 30},
            ),
            TemplateNode(
                node_type="mcp",
                node_key="mcp",
                node_name="MCP 工具",
                position_x=1025,
                position_y=-50,
                base_config={"mcp_server_ids": []},
            ),
            TemplateNode(
                node_type="skill",
                node_key="skill",
                node_name="技能",
                position_x=450,
                position_y=-150,
                base_config={"skill_ids": []},
            ),
            TemplateNode(
                node_type="memory",
                node_key="memory",
                node_name="记忆",
                position_x=675,
                position_y=-150,
            ),
            TemplateNode(
                node_type="todo",
                node_key="todo",
                node_name="任务计划",
                position_x=850,
                position_y=-150,
            ),
            TemplateNode(
                node_type="agenda",
                node_key="agenda",
                node_name="日程",
                position_x=1025,
                position_y=-150,
            ),
            TemplateNode(
                node_type="sub_agent",
                node_key="sub_agent",
                node_name="子Agent",
                position_x=1250,
                position_y=-150,
                base_config={"agent_id": 0},
            ),
            # ---- 数据处理分支 ----
            TemplateNode(
                node_type="api",
                node_key="api",
                node_name="API 调用",
                position_x=700,
                position_y=300,
                base_config={
                    "api_url": "https://httpbin.org/get",
                    "method": "GET",
                    "headers": '{"Accept": "application/json"}',
                },
            ),
            TemplateNode(
                node_type="python",
                node_key="python_data",
                node_name="数据处理",
                position_x=1000,
                position_y=300,
                base_config={
                    "code": _CODE_PYTHON_DATA,
                    "timeout": 30,
                    "input_variables": [
                        {
                            "name": "input_data",
                            "source": "nodes.api.body",
                            "type": "string",
                        }
                    ],
                },
            ),
            TemplateNode(
                node_type="condition",
                node_key="condition",
                node_name="条件判断",
                position_x=1300,
                position_y=300,
                base_config={
                    "logic": "and",
                    "rules": [
                        {
                            "variable": "nodes.api.status_code",
                            "operator": "==",
                            "value": "200",
                        }
                    ],
                },
            ),
            TemplateNode(
                node_type="human",
                node_key="human",
                node_name="人工审批",
                position_x=1600,
                position_y=350,
            ),
            TemplateNode(
                node_type="end",
                node_key="end_data",
                node_name="结束(数据)",
                position_x=1900,
                position_y=300,
                base_config={
                    "output_variables": [
                        {
                            "name": "result",
                            "source": "nodes.api.body",
                            "type": "string",
                        }
                    ],
                },
            ),
            # ---- 批量循环分支 ----
            TemplateNode(
                node_type="loop",
                node_key="loop",
                node_name="循环处理",
                position_x=700,
                position_y=500,
                base_config={
                    "loop_mode": "count",
                    "max_count": 3,
                    "input_mappings": [
                        {"card_field": "item", "source": "input.message"}
                    ],
                },
            ),
            TemplateNode(
                node_type="start",
                node_key="loop__start",
                node_name="循环-开始",
                position_x=750,
                position_y=580,
            ),
            TemplateNode(
                node_type="python",
                node_key="loop__python",
                node_name="循环-处理",
                position_x=970,
                position_y=580,
                base_config={
                    "code": _CODE_LOOP_SUB,
                    "timeout": 30,
                    "input_variables": [
                        {
                            "name": "item",
                            "source": "nodes.loop.input_item",
                            "type": "string",
                        }
                    ],
                },
            ),
            TemplateNode(
                node_type="end",
                node_key="loop__end",
                node_name="循环-结束",
                position_x=1190,
                position_y=580,
                base_config={
                    "output_variables": [
                        {
                            "name": "res",
                            "source": "nodes.loop__python.result",
                            "type": "string",
                        }
                    ],
                },
            ),
            TemplateNode(
                node_type="end",
                node_key="end_batch",
                node_name="结束(批量)",
                position_x=1350,
                position_y=500,
                base_config={
                    "output_variables": [
                        {
                            "name": "result",
                            "source": "nodes.loop.res",
                            "type": "array",
                        }
                    ],
                },
            ),
        ],
        edges=[
            # ---- 主流程：意图路由三分支 ----
            TemplateEdge(source_node_key="start", target_node_key="intent_router"),
            TemplateEdge(
                source_node_key="intent_router",
                target_node_key="llm",
                source_handle="chat",
            ),
            TemplateEdge(source_node_key="llm", target_node_key="end_chat"),
            TemplateEdge(
                source_node_key="intent_router",
                target_node_key="api",
                source_handle="data",
            ),
            TemplateEdge(source_node_key="api", target_node_key="python_data"),
            TemplateEdge(source_node_key="python_data", target_node_key="condition"),
            TemplateEdge(
                source_node_key="condition",
                target_node_key="end_data",
                source_handle="true",
            ),
            TemplateEdge(
                source_node_key="condition",
                target_node_key="human",
                source_handle="false",
            ),
            TemplateEdge(source_node_key="human", target_node_key="end_data"),
            TemplateEdge(
                source_node_key="intent_router",
                target_node_key="loop",
                source_handle="batch",
            ),
            TemplateEdge(source_node_key="loop", target_node_key="end_batch"),
            # ---- 循环内部子节点 ----
            TemplateEdge(source_node_key="loop__start", target_node_key="loop__python"),
            TemplateEdge(source_node_key="loop__python", target_node_key="loop__end"),
            # ---- 工具边：全部工具节点 → LLM ----
            TemplateEdge(
                source_node_key="knowledge",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="python_tool",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="shell",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="mcp",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="skill",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="memory",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="todo",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="agenda",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
            TemplateEdge(
                source_node_key="sub_agent",
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            ),
        ],
        input_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="message",
                    type=FieldType.STRING,
                    description="用户消息",
                    required=True,
                )
            ]
        ),
        output_schema=FlowIOSchema(
            fields=[
                FlowIOField(
                    name="result", type=FieldType.STRING, description="流程输出结果"
                )
            ]
        ),
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
    template: Optional[FlowTemplate] = get_template(template_id)
    if not template:
        raise ValueError(f"模板不存在: {template_id}")

    flow_data = FlowCreate(
        name=name,
        description=description or template.description,
        flow_type=template.flow_type,
        input_schema=template.input_schema or None,
        output_schema=template.output_schema or None,
        suggested_prompts=template.suggested_prompts,
    )
    flow = await flow_service.create(db, flow_data)

    nodes_data = [n.model_dump() for n in template.nodes]
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
