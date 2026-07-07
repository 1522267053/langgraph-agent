"""
Skill 节点处理器

处理 Skill 节点的执行，提供 load_skill 工具供 LLM 调用
"""

import json
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config.settings import settings
from app.config.database import AsyncSessionLocal
from app.models.flow_node import FlowNode
from app.models.skill import Skill
from app.agent_flow.flow_context import FlowState
from app.agent_flow.node_handlers.base_handler import BaseNodeHandler, BaseNodeConfig
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.flow_event import NodeStartEvent, NodeDoneEvent

if TYPE_CHECKING:
    from app.agent_flow.tool_resolver import LlmToolConfig


class LoadSkillInput(BaseModel):
    """load_skill 工具输入参数"""

    skill_name: str = Field(..., description="要加载的技能名称")


class SkillNodeConfig(BaseNodeConfig):
    """Skill 节点配置"""

    skill_ids: list[int] = Field(default=[], description="Skill ID 列表")


@NodeHandlerRegistry.register("skill")
class SkillNodeHandler(BaseNodeHandler):
    """
    Skill 节点处理器

    功能：
    1. 从数据库加载 Skill 配置
    2. 提供 load_skill 工具供 LLM 调用
    3. LLM 通过 load_skill 加载完整的 SKILL.md 文档
    4. LLM 根据文档内容自主决定如何执行
    """

    ConfigClass = SkillNodeConfig

    def __init__(self, db_session: AsyncSession = None):
        super().__init__()
        self.db_session = db_session
        self._skill_cache: dict[int, Skill] = {}

    async def _get_skill(self, skill_id: int) -> Optional[Skill]:
        if skill_id in self._skill_cache:
            return self._skill_cache[skill_id]

        if not self.db_session:
            return None

        query = select(Skill).where(Skill.id == skill_id, Skill.is_delete == 0)
        result = await self.db_session.execute(query)
        skill = result.scalar_one_or_none()

        if skill:
            self._skill_cache[skill_id] = skill

        return skill

    async def _get_skills_by_ids(self, skill_ids: list[int]) -> list[Skill]:
        if not self.db_session or not skill_ids:
            return []

        uncached_ids = [sid for sid in skill_ids if sid not in self._skill_cache]
        if uncached_ids:
            query = select(Skill).where(
                Skill.id.in_(uncached_ids), Skill.is_delete == 0
            )
            result = await self.db_session.execute(query)
            for skill in result.scalars().all():
                self._skill_cache[skill.id] = skill

        skills = []
        for sid in skill_ids:
            skill = self._skill_cache.get(sid)
            if skill and skill.is_enabled == 1:
                skills.append(skill)
        return skills

    async def _get_skill_by_name(self, skill_name: str) -> Optional[Skill]:
        if not self.db_session:
            return None

        query = select(Skill).where(
            Skill.name == skill_name, Skill.is_enabled == 1, Skill.is_delete == 0
        )
        result = await self.db_session.execute(query)
        return result.scalar_one_or_none()

    def _generate_directory_tree(
        self, path: Path, prefix: str = "", level: int = 0, max_level: int = 4
    ) -> str:
        """
        生成目录树形结构字符串

        Args:
            path: 目录路径
            prefix: 前缀字符串（用于缩进）
            level: 当前层级
            max_level: 最大递归层级

        Returns:
            树形结构字符串
        """
        if level >= max_level:
            return ""

        items = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        lines = []

        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{item.name}")

            if item.is_dir():
                next_prefix = prefix + ("    " if is_last else "│   ")
                subtree = self._generate_directory_tree(
                    item, next_prefix, level + 1, max_level
                )
                if subtree:
                    lines.append(subtree)

        return "\n".join(lines)

    async def _load_skill_markdown(self, skill: Skill) -> str:
        """
        从 skill_path 读取 Markdown 文件内容，并生成目录树形结构
        """
        if not skill.skill_path:
            return json.dumps(
                {"error": f"技能 '{skill.name}' 未配置文件路径"}, ensure_ascii=False
            )

        skill_file = settings.get_absolute_path(skill.skill_path)

        if not skill_file.exists():
            return json.dumps(
                {"error": f"技能文件不存在: {skill.skill_path}"}, ensure_ascii=False
            )

        try:
            content = skill_file.read_text(encoding="utf-8")

            skill_dir = skill_file.parent if skill_file.is_file() else skill_file
            current_directory_structure = ""
            if skill_dir.exists() and skill_dir.is_dir():
                tree_lines = [f"{skill_dir}/"]
                subtree = self._generate_directory_tree(skill_dir)
                if subtree:
                    tree_lines.append(subtree)
                current_directory_structure = "\n".join(tree_lines)

            return json.dumps(
                {
                    "skill_name": skill.name,
                    "description": skill.description,
                    "content": content,
                    "current_directory_structure": current_directory_structure,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {"error": f"读取技能文件失败: {str(e)}"}, ensure_ascii=False
            )

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:

        if writer:
            writer(
                NodeStartEvent(
                    node_key=node.node_key,
                    node_type=node.node_type,
                    node_name=node.node_name,
                    input_data={},
                )
            )

        if writer:
            writer(NodeDoneEvent(node_key=node.node_key, output={}))

        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """skill 节点使用固定工具名 load_skill，多实例会导致工具名冲突"""
        return False

    async def get_tool(self, node: FlowNode) -> Optional[StructuredTool]:
        """
        返回 load_skill 工具，供 LLM 调用

        Args:
            node: 节点对象

        Returns:
            LangChain StructuredTool
        """
        node_config = node.base_config or {}
        skill_ids = node_config.get("skill_ids", [])

        if not skill_ids:
            skill_id = node_config.get("skill_id")
            if skill_id:
                skill_ids = [skill_id]

        if not skill_ids:
            return None

        async with AsyncSessionLocal() as db:
            self.db_session = db
            skills = await self._get_skills_by_ids([int(sid) for sid in skill_ids])

        if not skills:
            return None

        handler = self
        skill_ids_set = set(s.id for s in skills)

        async def load_skill(skill_name: str) -> str:
            try:
                async with AsyncSessionLocal() as db:
                    handler.db_session = db
                    target_skill = await handler._get_skill_by_name(skill_name)
                    if not target_skill:
                        return json.dumps(
                            {"error": f"技能不存在: {skill_name}"}, ensure_ascii=False
                        )

                    if target_skill.id not in skill_ids_set:
                        available_names = ", ".join([s.name for s in skills])
                        return json.dumps(
                            {
                                "error": f"当前节点配置的技能为: {available_names}，不支持加载 '{skill_name}'"
                            },
                            ensure_ascii=False,
                        )

                    return await handler._load_skill_markdown(target_skill)
            except Exception as e:
                return json.dumps(
                    {"error": f"加载技能失败: {str(e)}"}, ensure_ascii=False
                )

        skill_desc_list = [
            f"技能名称(id={s.id})：{s.name}\n" + f"技能描述：{s.description}\n"
            for s in skills
        ]
        description = (
            f"加载技能文档(SKILLS.md)。当前可用技能: {'\n'.join(skill_desc_list)}"
        )

        return StructuredTool(
            name="load_skill",
            description=description,
            func=None,
            coroutine=load_skill,
            args_schema=LoadSkillInput,
        )

    @classmethod
    def get_tool_config(cls, node: FlowNode, config: "LlmToolConfig") -> bool:
        """将Skill节点配置添加到工具配置"""
        node_config = node.base_config or {}
        skill_ids = node_config.get("skill_ids", [])

        if not skill_ids:
            skill_id = node_config.get("skill_id")
            if skill_id:
                skill_ids = [skill_id]

        if skill_ids:
            config.skill_node_keys.append(node.node_key)
            config.skill_configs[node.node_key] = {
                "skill_ids": skill_ids,
                "name": node.node_name or "Skill调用",
                "description": node_config.get("description", "执行预定义的Skill"),
            }
            return True
        return False

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        return [{"name": "load_skill", "description": "加载技能文档(SKILLS.md)"}]
