"""节点类型相关常量"""

NODE_TYPE_LABELS: dict[str, str] = {
    "start": "开始",
    "end": "结束",
    "llm": "大模型调用",
    "condition": "条件",
    "loop": "循环",
    "card": "能力卡片",
    "human": "人类回答",
    "mcp": "MCP",
    "knowledge": "知识库",
    "api": "API调用",
    "skill": "技能",
    "python": "Python",
    "shell": "Shell",
    "memory": "记忆",
    "todo": "任务计划",
    "intent_router": "意图路由",
    "sub_agent": "子Agent",
    "agenda": "日程",
}
