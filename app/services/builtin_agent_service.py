"""
内置 Agent 服务

管理内置 Agent 的创建、配置同步。
内置 Agent 是系统的默认入口，用户首页直接与之对话。
"""

import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.config.build_utils import get_internal_dir
from app.models.flow import Flow, FlowType
from app.models.flow_node import FlowNode, NodeType
from app.models.skill import Skill
from app.services.flow_service import flow_service, DEFAULT_AGENT_INPUT_SCHEMA
from app.services.global_config_service import global_config_service
from app.services.node_config_helper import fill_node_defaults
from app.schemas.flow_schema import FlowCreate
from app.schemas.flow_node_schema import FlowNodeCreate
from app.schemas.flow_edge_schema import FlowEdgeCreate

logger = logging.getLogger(__name__)

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_skill_frontmatter(file_path: Path) -> Optional[dict]:
    """解析 SKILL.md 的 YAML front matter，提取 name/description"""
    content = file_path.read_text(encoding="utf-8")
    match = _FRONT_MATTER_RE.match(content)
    if not match:
        return None

    info: dict = {}
    current_key = None
    current_lines: list[str] = []
    in_block = False

    for line in match.group(1).strip().split("\n"):
        if in_block and (
            line.startswith("  ") or line.startswith("\t") or line.strip() == ""
        ):
            current_lines.append(line.rstrip())
            continue
        if in_block:
            info[current_key] = "\n".join(current_lines).strip()
            in_block = False
            current_key = None
            current_lines = []
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip().lower().replace("-", "_")
            v = v.strip()
            if v == "|":
                in_block = True
                current_key = k
                current_lines = []
            else:
                info[k] = v.strip("\"'")

    if in_block and current_key:
        info[current_key] = "\n".join(current_lines).strip()

    if "name" not in info or "description" not in info:
        return None
    return info


def _discover_builtin_skills() -> list[dict]:
    """扫描 skills/ 目录，从 SKILL.md front matter 发现内置 skill"""
    skills_dir = get_internal_dir() / "skills"
    if not skills_dir.is_dir():
        return []

    result: list[dict] = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        md = entry / "SKILL.md"
        if not md.exists():
            continue
        parsed = _parse_skill_frontmatter(md)
        if not parsed:
            logger.warning("内置 Skill %s 的 SKILL.md 解析失败", entry.name)
            continue
        result.append(
            {
                "name": parsed["name"],
                "description": parsed["description"],
                "source_dir": f"skills/{entry.name}",
            }
        )
    return result


def _dir_signature(path: Path) -> str:
    """计算目录签名（所有文件相对路径 + 内容的 MD5），用于检测目录内容变化"""
    if not path.is_dir():
        return ""
    hasher = hashlib.md5()
    for file_path in sorted(path.rglob("*")):
        if file_path.is_file():
            rel = file_path.relative_to(path).as_posix()
            hasher.update(rel.encode())
            hasher.update(file_path.read_bytes())
    return hasher.hexdigest()


BUILTIN_AGENT_SYSTEM_PROMPT = """你是 AI Agent OS 的内置助手，是用户与系统交互的主要入口。

## 核心能力

### 1. 创建智能体 (Agent)
帮助用户创建新智能体。智能体由「开始→大模型调用→结束」三个节点组成，可连接工具节点。

创建前需了解：
- 智能体的名称和用途
- 需要连接哪些工具
- 编写合适的 system_prompt

### 2. 创建工作流 (Workflow)
帮助用户创建工作流。工作流支持条件分支、循环节点、能力卡片等复杂拓扑。

### 3. 管理已有智能体和工作流
列出、查看、修改已有的智能体和工作流。

## 操作方式

你有以下工具：
1. **load_skill** — 先调用 `load_skill` 加载「agent-manager」技能文档，里面包含所有 API 接口说明和节点配置详情
2. **api_call_tool** — 按照技能文档中的说明，调用后端 REST API 来创建/查询/修改/删除流程

典型操作流程：
1. 用户说"帮我创建一个XXX智能体"
2. 调用 load_skill("agent-manager") 获取完整的 API 文档
3. 根据文档调用 /api/ai/flow/create 创建空流程
4. 调用 /api/ai/flow/{id}/nodes/batch 添加节点
5. 调用 /api/ai/flow/{id}/edges/batch 添加边

## 注意事项
- 创建智能体/工作流前，先充分了解用户需求
- 创建用户的智能体时，LLM 节点的 api_key 留空即可，系统会自动注入全局 API Key
- 修改已有内容前，先展示当前配置供用户确认
- 如需了解节点类型配置，调用 GET /api/ai/flow/node-types/{type}/config-schema
"""


class BuiltinAgentService:
    """内置 Agent 服务"""

    async def ensure(self, db: AsyncSession) -> Optional[int]:
        """
        确保内置 Agent 存在，不存在则自动创建

        Args:
            db: 数据库会话

        Returns:
            内置 Agent 的 Flow ID
        """
        query = select(Flow).where(
            Flow.is_builtin == 1,
            Flow.is_delete == 0,
            Flow.flow_type == FlowType.AGENT.value,
        )
        result = await db.execute(query)
        builtin_flow = result.scalar_one_or_none()

        if builtin_flow:
            logger.info("内置 Agent 已存在: id=%d", builtin_flow.id)
            # 补偿：旧版本创建的内置 Agent 可能没有 input_schema
            if not builtin_flow.input_schema:
                builtin_flow.input_schema = DEFAULT_AGENT_INPUT_SCHEMA
                await db.commit()
                logger.info("已为内置 Agent 补充 input_schema: id=%d", builtin_flow.id)
            return builtin_flow.id

        skills = await self._ensure_skill(db)
        flow = await self._create(db, skills)
        logger.info("内置 Agent 创建成功: id=%d", flow.id)
        return flow.id

    async def sync_llm_config(self, db: AsyncSession) -> None:
        """
        全局配置变更后，同步更新内置 Agent 的 LLM 节点配置

        读取全局配置，查找内置 Agent 的 LLM 节点，
        将空的 provider/model/api_key/base_url 回填为全局配置值。
        """
        global_llm = await global_config_service.get_default_llm_config(db)
        if not global_llm.get("api_key"):
            return

        query = select(Flow).where(
            Flow.is_builtin == 1,
            Flow.is_delete == 0,
            Flow.flow_type == FlowType.AGENT.value,
        )
        result = await db.execute(query)
        builtin_flow = result.scalar_one_or_none()
        if not builtin_flow:
            return

        node_query = select(FlowNode).where(
            FlowNode.flow_id == builtin_flow.id,
            FlowNode.node_type == NodeType.LLM.value,
            FlowNode.is_delete == 0,
        )
        node_result = await db.execute(node_query)
        llm_node = node_result.scalar_one_or_none()
        if not llm_node or not llm_node.base_config:
            return

        config = llm_node.base_config if isinstance(llm_node.base_config, dict) else {}
        updated = False

        if not config.get("provider"):
            config["provider"] = global_llm.get("provider", "deepseek")
            updated = True
        if not config.get("model"):
            config["model"] = global_llm.get("model", "")
            updated = True
        if not config.get("api_key"):
            config["api_key"] = global_llm.get("api_key", "")
            updated = True
        if not config.get("base_url"):
            config["base_url"] = global_llm.get("base_url", "")
            updated = True
        if not config.get("context_length") and global_llm.get("context_length"):
            config["context_length"] = global_llm["context_length"]
            updated = True

        if updated:
            llm_node.base_config = config
            await db.commit()
            logger.info("内置 Agent LLM 配置已同步: flow_id=%d", builtin_flow.id)

    async def sync_skills(self, db: AsyncSession) -> None:
        """启动时同步 skills/ 目录，注册未入库的内置技能"""
        await self._ensure_skill(db)

    async def _ensure_skill(self, db: AsyncSession) -> list[dict]:
        """
        确保所有内置 Skill 已注册到 DB

        扫描 skills/ 目录，从每份 SKILL.md 的 front matter 读取 name/description，
        复制到 uploads/skills/ 并创建 DB 记录。
        返回所有注册的 skill 信息列表 [{id, name}, ...]
        """
        builtin_skills = _discover_builtin_skills()
        registered: list[dict] = []

        for skill_info in builtin_skills:
            skill_name = skill_info["name"]
            source_dir = skill_info["source_dir"]
            description = skill_info["description"]

            query = select(Skill).where(Skill.name == skill_name, Skill.is_delete == 0)
            result = await db.execute(query)
            existing = result.scalar_one_or_none()

            if existing:
                # 检查源目录和目标目录内容是否不同，不同则覆盖
                source_path = get_internal_dir() / source_dir
                upload_dir = (
                    settings.get_absolute_path(settings.upload_dir)
                    / "skills"
                    / skill_name
                )
                if source_path.is_dir():
                    source_sig = _dir_signature(source_path)
                    target_sig = _dir_signature(upload_dir)
                    if source_sig != target_sig:
                        shutil.rmtree(upload_dir, ignore_errors=True)
                        shutil.copytree(source_path, upload_dir)
                        if existing.description != description:
                            existing.description = description
                            await db.commit()
                        logger.info("内置 Skill 已更新: name=%s", skill_name)
                registered.append({"id": existing.id, "name": skill_name})
                continue

            # 从 skills/ 复制到 uploads/skills/
            source_path = get_internal_dir() / source_dir
            upload_dir = (
                settings.get_absolute_path(settings.upload_dir) / "skills" / skill_name
            )

            if source_path.exists():
                if upload_dir.exists():
                    shutil.rmtree(upload_dir, ignore_errors=True)
                shutil.copytree(source_path, upload_dir)
                db_skill_path = f"{settings.upload_dir}/skills/{skill_name}/SKILL.md"
            else:
                logger.warning("内置 Skill 目录不存在: %s", source_path)
                db_skill_path = f"{source_dir}/SKILL.md"

            skill = Skill(
                name=skill_name,
                description=description,
                skill_path=db_skill_path,
                category="system",
                is_enabled=1,
                is_system=1,
            )
            db.add(skill)
            await db.commit()
            await db.refresh(skill)
            logger.info("内置 Skill 注册成功: name=%s, id=%d", skill_name, skill.id)
            registered.append({"id": skill.id, "name": skill_name})

        return registered

    async def _create(self, db: AsyncSession, skills: list[dict]) -> Flow:
        """创建内置 Agent（Start → LLM → End + 全部工具节点）"""
        from app.agent_flow.ai_provider.base import AIProviderRegistry

        global_llm = await global_config_service.get_default_llm_config(db)

        provider_name = global_llm.get("provider", "deepseek")
        model = global_llm.get("model", "")
        api_key = global_llm.get("api_key", "")
        base_url = global_llm.get("base_url", "")

        if not base_url:
            provider_cls = AIProviderRegistry.get(provider_name)
            base_url = provider_cls.default_base_url if provider_cls else ""

        flow_data = FlowCreate(
            name="AI 助手",
            description="系统内置 AI 助手，可以帮助你创建智能体和工作流",
            flow_type=FlowType.AGENT.value,
            input_schema=DEFAULT_AGENT_INPUT_SCHEMA,
        )
        flow = await flow_service.create(db, flow_data)

        flow.is_builtin = 1
        await db.commit()
        await db.refresh(flow)

        # ---- 主链路节点 ----
        nodes_data = [
            {
                "node_type": NodeType.START.value,
                "node_key": "start",
                "node_name": "开始",
                "position_x": 100,
                "position_y": 200,
                "base_config": {
                    "input_variables": [
                        {
                            "name": "message",
                            "type": "string",
                            "description": "用户消息",
                            "required": True,
                        }
                    ]
                },
            },
            {
                "node_type": NodeType.LLM.value,
                "node_key": "llm",
                "node_name": "AI 助手",
                "position_x": 350,
                "position_y": 200,
                "base_config": fill_node_defaults(
                    "llm",
                    {
                        "provider": provider_name,
                        "model": model,
                        "api_key": api_key,
                        "base_url": base_url,
                        "context_length": global_llm.get("context_length"),
                        "system_prompt": BUILTIN_AGENT_SYSTEM_PROMPT,
                        "user_prompt": "{{message}}",
                        "max_tool_iterations": 100,
                        "input_variables": [
                            {
                                "name": "message",
                                "source": "input.message",
                                "type": "string",
                            }
                        ],
                    },
                ),
            },
            {
                "node_type": NodeType.END.value,
                "node_key": "end",
                "node_name": "结束",
                "position_x": 600,
                "position_y": 200,
                "base_config": {
                    "output_variables": [
                        {"name": "res", "source": "nodes.llm.result", "type": "string"}
                    ]
                },
            },
        ]

        # ---- 工具节点 ----
        tool_nodes = []
        if skills:
            tool_nodes.append(
                (
                    "skill_tool",
                    NodeType.SKILL.value,
                    "技能",
                    100,
                    450,
                    fill_node_defaults(
                        "skill", {"skill_ids": [s["id"] for s in skills]}
                    ),
                )
            )

        tool_nodes.extend(
            [
                (
                    "api_tool",
                    NodeType.API.value,
                    "API 工具",
                    300,
                    450,
                    fill_node_defaults("api"),
                ),
                (
                    "todo_tool",
                    NodeType.TODO.value,
                    "任务计划",
                    500,
                    450,
                    fill_node_defaults("todo"),
                ),
                (
                    "python_tool",
                    NodeType.PYTHON.value,
                    "Python 工具",
                    700,
                    450,
                    fill_node_defaults("python"),
                ),
                (
                    "shell_tool",
                    NodeType.SHELL.value,
                    "Shell 工具",
                    900,
                    450,
                    fill_node_defaults("shell"),
                ),
                (
                    "memory_tool",
                    NodeType.MEMORY.value,
                    "记忆管理",
                    300,
                    600,
                    fill_node_defaults("memory"),
                ),
                (
                    "agenda_tool",
                    NodeType.AGENDA.value,
                    "日程管理",
                    500,
                    600,
                    fill_node_defaults("agenda"),
                ),
            ]
        )
        for key, ntype, name, x, y, cfg in tool_nodes:
            nodes_data.append(
                {
                    "node_type": ntype,
                    "node_key": key,
                    "node_name": name,
                    "position_x": x,
                    "position_y": y,
                    "base_config": cfg,
                }
            )

        node_creates = [
            FlowNodeCreate(
                flow_id=flow.id,
                node_type=n["node_type"],
                node_key=n["node_key"],
                node_name=n["node_name"],
                position_x=n["position_x"],
                position_y=n["position_y"],
                base_config=n.get("base_config"),
            )
            for n in nodes_data
        ]
        await flow_service.batch_create_nodes(db, flow.id, node_creates)

        # ---- 边 ----
        edges_data = [
            {
                "source_node_key": "start",
                "target_node_key": "llm",
                "source_handle": "default",
                "target_handle": "default",
            },
            {
                "source_node_key": "llm",
                "target_node_key": "end",
                "source_handle": "default",
                "target_handle": "default",
            },
        ]
        for key, *_ in tool_nodes:
            edges_data.append(
                {
                    "source_node_key": key,
                    "target_node_key": "llm",
                    "source_handle": "tools",
                    "target_handle": "tools",
                }
            )

        edge_creates = [
            FlowEdgeCreate(
                flow_id=flow.id,
                source_node_key=e["source_node_key"],
                target_node_key=e["target_node_key"],
                source_handle=e.get("source_handle"),
                target_handle=e.get("target_handle"),
            )
            for e in edges_data
        ]
        await flow_service.batch_create_edges(db, flow.id, edge_creates)

        return await flow_service.get_with_nodes_and_edges(db, flow.id)


builtin_agent_service = BuiltinAgentService()
