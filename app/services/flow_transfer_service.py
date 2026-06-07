"""
流程导入导出服务

提供流程/智能体的批量导出和导入功能。
导出包含：流程（含节点和边）、记忆、MCP 服务器、知识库、技能。
导入按依赖顺序：Skills → KnowledgeBases → MCPServers → Flows（拓扑序）→ Memories。
"""

import logging
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.flow import Flow, FlowType
from app.models.flow_node import FlowNode
from app.models.flow_edge import FlowEdge
from app.models.knowledge_base import KnowledgeBase
from app.models.mcp_server import McpServer
from app.models.memory import Memory
from app.models.skill import Skill
from app.services.flow_service import flow_service
from app.services.knowledge_base_service import knowledge_base_service
from app.services.mcp_server_service import mcp_server_service
from app.services.memory_service import memory_service
from app.services.skill_service import skill_service

logger = logging.getLogger(__name__)

EXPORT_VERSION = "1.0"

INTERNAL_NODE_FIELDS = frozenset(
    {
        "id",
        "flow_id",
        "creator_id",
        "creator_type",
        "creator_name",
        "create_time",
        "modifier_id",
        "modifier_type",
        "modifier_name",
        "modify_time",
        "is_delete",
    }
)

INTERNAL_FLOW_FIELDS = frozenset(
    {
        "id",
        "creator_id",
        "creator_type",
        "creator_name",
        "create_time",
        "modifier_id",
        "modifier_type",
        "modifier_name",
        "modify_time",
        "is_delete",
        "is_builtin",
    }
)

MEMORY_IMPORT_FIELDS = frozenset(
    {
        "memory_type",
        "category",
        "title",
        "content",
        "keywords",
        "importance",
        "peak_tier",
    }
)


class FlowTransferService:
    """流程导入导出服务"""

    # ---- 导出 ----

    async def export_flows(self, db: AsyncSession, flow_ids: list[int]) -> dict:
        """导出指定流程及其所有依赖（卡片引用、MCP、知识库、技能、记忆）"""
        flows = await self._collect_flows_recursive(db, flow_ids, set())
        sorted_flows = self._topological_sort(flows)
        flow_name_map = {f.id: f.name for f in sorted_flows}

        mcp_server_ids: set[int] = set()
        knowledge_base_ids: set[int] = set()
        skill_ids: set[int] = set()
        agent_ids: list[int] = []

        for flow in sorted_flows:
            for node in flow.nodes:
                config = node.base_config or {}
                if node.node_type == "mcp":
                    for sid in config.get("mcp_server_ids", []):
                        if isinstance(sid, int):
                            mcp_server_ids.add(sid)
                elif node.node_type == "knowledge":
                    kid = config.get("knowledge_base_id")
                    if isinstance(kid, int):
                        knowledge_base_ids.add(kid)
                elif node.node_type == "skill":
                    for sid in config.get("skill_ids", []):
                        if isinstance(sid, int):
                            skill_ids.add(sid)
                    legacy_sid = config.get("skill_id")
                    if isinstance(legacy_sid, int):
                        skill_ids.add(legacy_sid)
            if flow.flow_type == FlowType.AGENT.value:
                agent_ids.append(flow.id)

        mcp_servers = await self._collect_mcp_servers(db, mcp_server_ids)
        knowledge_bases = await self._collect_knowledge_bases(db, knowledge_base_ids)
        skills = await self._collect_skills(db, skill_ids)
        memories = await self._collect_memories(db, agent_ids)

        mcp_entities = await self._fetch_entity_list(db, McpServer, mcp_server_ids)
        kb_entities = await self._fetch_entity_list(
            db, KnowledgeBase, knowledge_base_ids
        )
        skill_entities = await self._fetch_entity_list(db, Skill, skill_ids)

        mcp_id_to_name = {s.id: s.name for s in mcp_entities}
        kb_id_to_name = {s.id: s.name for s in kb_entities}
        skill_id_to_name = {s.id: s.name for s in skill_entities}

        ref_maps = {
            "flow": flow_name_map,
            "mcp": mcp_id_to_name,
            "kb": kb_id_to_name,
            "skill": skill_id_to_name,
        }

        serialized_flows = []
        for flow in sorted_flows:
            sf = self._serialize_flow(flow, ref_maps)
            serialized_flows.append(sf)

        return {
            "version": EXPORT_VERSION,
            "export_time": datetime.now().isoformat(),
            "flows": serialized_flows,
            "memories": memories,
            "mcp_servers": mcp_servers,
            "knowledge_bases": knowledge_bases,
            "skills": skills,
        }

    async def _collect_flows_recursive(
        self, db: AsyncSession, flow_ids: list[int], visited: set[int]
    ) -> list[Flow]:
        """递归收集流程及其卡片引用的子流程"""
        result = []
        for fid in flow_ids:
            if fid in visited:
                continue
            visited.add(fid)
            flow = await flow_service.get_with_nodes_and_edges(db, fid)
            if not flow:
                continue
            result.append(flow)

            child_ids = []
            for node in flow.nodes:
                if node.node_type == "card" and node.ref_flow_id:
                    child_ids.append(node.ref_flow_id)
                config = node.base_config or {}
                if node.node_type == "card":
                    ref_id = config.get("ref_flow_id")
                    if ref_id and isinstance(ref_id, int) and ref_id not in visited:
                        child_ids.append(ref_id)

            if child_ids:
                result.extend(
                    await self._collect_flows_recursive(db, child_ids, visited)
                )
        return result

    def _topological_sort(self, flows: list[Flow]) -> list[Flow]:
        """拓扑排序：被引用的卡片流程排在前面"""
        flow_map = {f.id: f for f in flows}
        ref_map: dict[int, set[int]] = defaultdict(set)

        for f in flows:
            for node in f.nodes:
                ref_id = node.ref_flow_id
                if ref_id and ref_id in flow_map:
                    ref_map[f.id].add(ref_id)

        in_degree: dict[int, int] = {f.id: 0 for f in flows}
        children: dict[int, list[int]] = defaultdict(list)
        for fid, refs in ref_map.items():
            in_degree[fid] = len(refs)
            for ref_id in refs:
                children[ref_id].append(fid)

        queue = deque(fid for fid, deg in in_degree.items() if deg == 0)
        result = []
        while queue:
            fid = queue.popleft()
            if fid in flow_map:
                result.append(flow_map[fid])
            for child in children[fid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        remaining = [
            flow_map[fid] for fid in flow_map if fid not in {f.id for f in result}
        ]
        return result + remaining

    def _serialize_flow(self, flow: Flow, ref_maps: dict[str, dict]) -> dict:
        """序列化单个流程"""
        flow_data = {}
        for col in flow.__table__.columns:
            key = col.name
            if key in INTERNAL_FLOW_FIELDS:
                continue
            val = getattr(flow, key, None)
            if val is not None:
                flow_data[key] = val

        nodes = []
        for node in flow.nodes:
            nodes.append(self._serialize_node(node, ref_maps))
        edges = []
        for edge in flow.edges:
            edges.append(self._serialize_edge(edge))

        flow_data["nodes"] = nodes
        flow_data["edges"] = edges
        return flow_data

    def _serialize_node(self, node: FlowNode, ref_maps: dict[str, dict]) -> dict:
        """序列化节点，转换外部 ID 引用为名称"""
        flow_name_map = ref_maps["flow"]
        mcp_id_to_name = ref_maps["mcp"]
        kb_id_to_name = ref_maps["kb"]
        skill_id_to_name = ref_maps["skill"]

        data = {}
        for col in node.__table__.columns:
            key = col.name
            if key in INTERNAL_NODE_FIELDS:
                continue
            val = getattr(node, key, None)
            if key == "ref_flow_id" and val is not None:
                data["ref_flow_name"] = flow_name_map.get(val)
                continue
            if val is not None:
                data[key] = val

        config = dict(data.get("base_config") or {})

        config = self._transform_node_config_refs(
            config, flow_name_map, mcp_id_to_name, kb_id_to_name, skill_id_to_name
        )
        config = self._strip_api_keys(config, node.node_type)

        if config:
            data["base_config"] = config
        return data

    def _transform_node_config_refs(
        self,
        config: dict,
        flow_name_map: dict[int, str],
        mcp_id_to_name: dict[int, str],
        kb_id_to_name: dict[int, str],
        skill_id_to_name: dict[int, str],
    ) -> dict:
        """将节点 base_config 中的外部 ID 引用转换为名称"""
        if "mcp_server_ids" in config:
            config["mcp_server_names"] = [
                mcp_id_to_name[sid]
                for sid in config["mcp_server_ids"]
                if sid in mcp_id_to_name
            ]
            del config["mcp_server_ids"]

        if "knowledge_base_id" in config:
            kid = config.pop("knowledge_base_id")
            if kid in kb_id_to_name:
                config["knowledge_base_name"] = kb_id_to_name[kid]

        if "skill_ids" in config:
            config["skill_names"] = [
                skill_id_to_name[sid]
                for sid in config["skill_ids"]
                if sid in skill_id_to_name
            ]
            del config["skill_ids"]
        if "skill_id" in config:
            legacy = config.pop("skill_id")
            if legacy in skill_id_to_name:
                config.setdefault("skill_names", [])
                config["skill_names"].append(skill_id_to_name[legacy])

        ref_id = config.get("ref_flow_id")
        if ref_id is not None:
            config["ref_flow_name"] = flow_name_map.get(ref_id)
            del config["ref_flow_id"]

        return config

    def _strip_api_keys(self, config: dict, node_type: Optional[str]) -> dict:
        """移除 LLM 和 MediaGen 节点中的 api_key 和 base_url"""
        if node_type == "llm":
            config.pop("api_key", None)
            config.pop("base_url", None)
        elif node_type == "media_gen":
            for media_key in ("image", "audio", "video"):
                media_cfg = config.get(media_key)
                if isinstance(media_cfg, dict):
                    media_cfg.pop("api_key", None)
                    media_cfg.pop("base_url", None)
        return config

    def _serialize_edge(self, edge: FlowEdge) -> dict:
        """序列化边"""
        data = {}
        for col in edge.__table__.columns:
            key = col.name
            if key in INTERNAL_NODE_FIELDS:
                continue
            val = getattr(edge, key, None)
            if val is not None:
                data[key] = val
        return data

    async def _collect_mcp_servers(
        self, db: AsyncSession, server_ids: set[int]
    ) -> list[dict]:
        """收集 MCP 服务器完整数据"""
        if not server_ids:
            return []
        result = []
        for sid in server_ids:
            server = await mcp_server_service.get_by_id(db, sid, raise_not_found=False)
            if not server:
                continue
            configs = await mcp_server_service.get_configs(db, sid)
            tools = await mcp_server_service.get_tools_cache(db, sid)

            parsed_configs = {}
            for k, v in configs.items():
                if v is not None:
                    try:
                        import ast

                        if k in ("args", "env", "headers", "timeout"):
                            parsed_configs[k] = ast.literal_eval(v)
                        else:
                            parsed_configs[k] = v
                    except (ValueError, SyntaxError):
                        parsed_configs[k] = v

            result.append(
                {
                    "name": server.name,
                    "description": server.description,
                    "transport": server.transport,
                    "is_enabled": server.is_enabled,
                    "keep_alive": server.keep_alive,
                    "configs": parsed_configs,
                    "tools_cache": [
                        {
                            "tool_name": t.tool_name,
                            "description": t.description,
                            "tool_schema": t.tool_schema,
                            "is_enabled": t.is_enabled,
                        }
                        for t in tools
                    ],
                }
            )
        return result

    async def _collect_knowledge_bases(
        self, db: AsyncSession, kb_ids: set[int]
    ) -> list[dict]:
        """收集知识库元数据（不含向量和文档）"""
        if not kb_ids:
            return []
        result = []
        for kid in kb_ids:
            kb = await knowledge_base_service.get_by_id(db, kid, raise_not_found=False)
            if not kb:
                continue
            result.append(
                {
                    "name": kb.name,
                    "description": kb.description,
                    "status": kb.status,
                }
            )
        return result

    async def _collect_skills(
        self, db: AsyncSession, skill_ids: set[int]
    ) -> list[dict]:
        """收集技能数据（含 SKILL.md 文件内容）"""
        if not skill_ids:
            return []
        result = []
        for sid in skill_ids:
            skill = await skill_service.get_by_id(db, sid, raise_not_found=False)
            if not skill:
                continue

            skill_content = None
            if skill.skill_path:
                try:
                    file_path = Path(settings.get_absolute_path(skill.skill_path))
                    if file_path.exists():
                        skill_content = file_path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(f"读取 Skill 文件失败: {skill.skill_path}, {e}")

            result.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "category": skill.category,
                    "tags": skill.tags,
                    "icon": skill.icon,
                    "is_enabled": skill.is_enabled,
                    "skill_content": skill_content,
                }
            )
        return result

    async def _collect_memories(
        self, db: AsyncSession, agent_ids: list[int]
    ) -> list[dict]:
        """收集 Agent 记忆数据"""
        result = []
        for agent_id in agent_ids:
            flow = await flow_service.get_by_id(db, agent_id, raise_not_found=False)
            if not flow:
                continue
            memories = await memory_service.get_by_agent(db, agent_id)
            if not memories:
                continue
            items = []
            for m in memories:
                item = {}
                for field in MEMORY_IMPORT_FIELDS:
                    val = getattr(m, field, None)
                    if val is not None:
                        item[field] = val
                items.append(item)
            result.append(
                {
                    "flow_name": flow.name,
                    "memories": items,
                }
            )
        return result

    # ---- 导入 ----

    async def import_flows(
        self, db: AsyncSession, import_data: dict
    ) -> tuple[list[dict], list[str]]:
        """导入流程及所有依赖"""
        version = import_data.get("version")
        if version != EXPORT_VERSION:
            raise ValueError(f"不支持的版本: {version}，当前支持: {EXPORT_VERSION}")

        warnings: list[str] = []
        created: list[dict] = []

        flows_data = import_data.get("flows", [])
        if not flows_data:
            raise ValueError("导入数据中没有流程")

        self._check_circular_refs(flows_data)

        skill_name_map = await self._import_skills(
            db, import_data.get("skills", []), warnings
        )
        kb_name_map = await self._import_knowledge_bases(
            db, import_data.get("knowledge_bases", []), warnings
        )
        mcp_name_map = await self._import_mcp_servers(
            db, import_data.get("mcp_servers", []), warnings
        )

        flow_name_map: dict[str, int] = {}
        for flow_data in flows_data:
            item = await self._import_flow(
                db,
                flow_data,
                flow_name_map,
                skill_name_map,
                kb_name_map,
                mcp_name_map,
                warnings,
            )
            if item:
                created.append(item)
                flow_name_map[flow_data["name"]] = item["id"]

        await self._import_memories(
            db, import_data.get("memories", []), flow_name_map, warnings
        )

        return created, warnings

    async def _import_skills(
        self, db: AsyncSession, skills_data: list[dict], warnings: list[str]
    ) -> dict[str, int]:
        """导入技能，返回 {原始名称: new_id}"""
        name_map: dict[str, int] = {}
        for s in skills_data:
            try:
                original_name = s["name"]
                unique_name = await self._ensure_unique_name(db, Skill, original_name)

                skill_content = s.get("skill_content") or (
                    f"---\nname: {unique_name}\ndescription: {s.get('description', '')}\n---\n"
                )
                skill_dir = Path(
                    settings.get_absolute_path(
                        f"{settings.upload_dir}/skills/{unique_name}"
                    )
                )
                skill_dir.mkdir(parents=True, exist_ok=True)
                skill_file = skill_dir / "SKILL.md"
                skill_file.write_text(skill_content, encoding="utf-8")

                skill_path = f"{settings.upload_dir}/skills/{unique_name}/SKILL.md"
                skill_obj = Skill(
                    name=unique_name,
                    description=s.get("description", ""),
                    category=s.get("category"),
                    tags=s.get("tags"),
                    icon=s.get("icon"),
                    is_enabled=s.get("is_enabled", 1),
                    skill_path=skill_path,
                    is_delete=0,
                )
                db.add(skill_obj)
                await db.commit()
                await db.refresh(skill_obj)
                name_map[original_name] = skill_obj.id
                if unique_name != original_name:
                    warnings.append(
                        f"技能「{original_name}」已存在，已创建副本「{unique_name}」"
                    )
            except Exception as e:
                warnings.append(f"导入技能「{s.get('name', '?')}」失败: {e}")
        return name_map

    async def _import_knowledge_bases(
        self, db: AsyncSession, kb_data: list[dict], warnings: list[str]
    ) -> dict[str, int]:
        """导入知识库，返回 {原始名称: new_id}"""
        name_map: dict[str, int] = {}
        for kb in kb_data:
            try:
                original_name = kb["name"]
                unique_name = await self._ensure_unique_name(
                    db, KnowledgeBase, original_name
                )
                kb_obj = KnowledgeBase(
                    name=unique_name,
                    description=kb.get("description"),
                    status=kb.get("status", 1),
                    is_delete=0,
                )
                db.add(kb_obj)
                await db.commit()
                await db.refresh(kb_obj)
                name_map[original_name] = kb_obj.id
                if unique_name != original_name:
                    warnings.append(
                        f"知识库「{original_name}」已存在，已创建副本「{unique_name}」"
                    )
            except Exception as e:
                warnings.append(f"导入知识库「{kb.get('name', '?')}」失败: {e}")
        return name_map

    async def _import_mcp_servers(
        self, db: AsyncSession, mcp_data: list[dict], warnings: list[str]
    ) -> dict[str, int]:
        """导入 MCP 服务器，返回 {原始名称: new_id}"""
        name_map: dict[str, int] = {}
        for mcp in mcp_data:
            try:
                original_name = mcp["name"]
                unique_name = await self._ensure_unique_name(
                    db, McpServer, original_name
                )
                server_obj = McpServer(
                    name=unique_name,
                    description=mcp.get("description"),
                    transport=mcp.get("transport", "stdio"),
                    is_enabled=mcp.get("is_enabled", 1),
                    keep_alive=mcp.get("keep_alive", 1),
                    is_delete=0,
                )
                db.add(server_obj)
                await db.commit()
                await db.refresh(server_obj)
                name_map[original_name] = server_obj.id

                configs = mcp.get("configs", {})
                if configs:
                    await mcp_server_service.save_configs(db, server_obj.id, configs)

                tools = mcp.get("tools_cache", [])
                if tools:
                    await mcp_server_service.save_tools_cache(db, server_obj.id, tools)

                if unique_name != original_name:
                    warnings.append(
                        f"MCP 服务器「{original_name}」已存在，已创建副本「{unique_name}」"
                    )
            except Exception as e:
                warnings.append(f"导入 MCP 服务器「{mcp.get('name', '?')}」失败: {e}")
        return name_map

    async def _import_flow(
        self,
        db: AsyncSession,
        flow_data: dict,
        flow_name_map: dict[str, int],
        skill_name_map: dict[str, int],
        kb_name_map: dict[str, int],
        mcp_name_map: dict[str, int],
        warnings: list[str],
    ) -> Optional[dict]:
        """导入单个流程"""
        try:
            original_name = flow_data["name"]
            unique_name = await self._ensure_unique_name(db, Flow, original_name)

            ai_nodes = []
            for n in flow_data.get("nodes", []):
                node_dict = self._resolve_node_refs(
                    n,
                    flow_name_map,
                    skill_name_map,
                    kb_name_map,
                    mcp_name_map,
                    warnings,
                )
                ai_nodes.append(node_dict)

            await self._fill_default_llm_config(db, ai_nodes)

            ai_edges = flow_data.get("edges", [])

            new_flow = await flow_service.generate_flow(
                db=db,
                name=unique_name,
                flow_type=flow_data.get("flow_type", "flow"),
                description=flow_data.get("description"),
                input_schema=flow_data.get("input_schema"),
                output_schema=flow_data.get("output_schema"),
                ai_nodes=ai_nodes,
                ai_edges=ai_edges,
            )

            if unique_name != original_name:
                warnings.append(
                    f"流程「{original_name}」已存在，已创建副本「{unique_name}」"
                )

            return {
                "id": new_flow.id,
                "name": unique_name,
                "flow_type": new_flow.flow_type,
            }
        except Exception as e:
            warnings.append(f"导入流程「{flow_data.get('name', '?')}」失败: {e}")
            logger.exception(f"导入流程失败: {flow_data.get('name')}")
            return None

    async def _fill_default_llm_config(
        self, db: AsyncSession, nodes: list[dict]
    ) -> None:
        """为缺少 api_key 的 LLM 节点填充本地默认配置"""
        from app.services.global_config_service import global_config_service

        default_config = await global_config_service.get_default_llm_config(db)
        if not default_config.get("api_key"):
            return
        for node in nodes:
            if node.get("node_type") == "llm":
                config = node.get("base_config") or {}
                if not config.get("api_key"):
                    config["api_key"] = default_config["api_key"]
                if not config.get("base_url") and default_config.get("base_url"):
                    config["base_url"] = default_config["base_url"]
                if not config.get("provider") and default_config.get("provider"):
                    config["provider"] = default_config["provider"]
                if not config.get("model") and default_config.get("model"):
                    config["model"] = default_config["model"]
                if not config.get("context_length") and default_config.get(
                    "context_length"
                ):
                    config["context_length"] = default_config["context_length"]
                node["base_config"] = config

    def _resolve_node_refs(
        self,
        node: dict,
        flow_name_map: dict[str, int],
        skill_name_map: dict[str, int],
        kb_name_map: dict[str, int],
        mcp_name_map: dict[str, int],
        warnings: list[str],
    ) -> dict:
        """解析节点中的名称引用为 ID"""
        result = dict(node)
        config = dict(result.get("base_config") or {})

        if "ref_flow_name" in result:
            ref_name = result.pop("ref_flow_name")
            if ref_name and ref_name in flow_name_map:
                result["ref_flow_id"] = flow_name_map[ref_name]
                config["ref_flow_id"] = flow_name_map[ref_name]
            elif ref_name:
                warnings.append(f"卡片节点引用的流程「{ref_name}」未找到")

        config_ref_name = config.pop("ref_flow_name", None)
        if config_ref_name:
            if config_ref_name in flow_name_map:
                config["ref_flow_id"] = flow_name_map[config_ref_name]
                result["ref_flow_id"] = flow_name_map[config_ref_name]
            else:
                warnings.append(f"卡片节点引用的流程「{config_ref_name}」未找到")

        mcp_names = config.pop("mcp_server_names", None)
        if mcp_names:
            ids = []
            for name in mcp_names:
                if name in mcp_name_map:
                    ids.append(mcp_name_map[name])
                else:
                    warnings.append(f"MCP 服务器「{name}」未在导入数据中找到")
            config["mcp_server_ids"] = ids

        kb_name = config.pop("knowledge_base_name", None)
        if kb_name:
            if kb_name in kb_name_map:
                config["knowledge_base_id"] = kb_name_map[kb_name]
            else:
                warnings.append(f"知识库「{kb_name}」未在导入数据中找到")

        skill_names = config.pop("skill_names", None)
        if skill_names:
            ids = []
            for name in skill_names:
                if name in skill_name_map:
                    ids.append(skill_name_map[name])
                else:
                    warnings.append(f"技能「{name}」未在导入数据中找到")
            config["skill_ids"] = ids

        if config:
            result["base_config"] = config
        return result

    async def _import_memories(
        self,
        db: AsyncSession,
        memories_data: list[dict],
        flow_name_map: dict[str, int],
        warnings: list[str],
    ) -> None:
        """导入记忆数据"""
        for entry in memories_data:
            flow_name = entry.get("flow_name", "")
            agent_id = flow_name_map.get(flow_name)
            if not agent_id:
                warnings.append(
                    f"记忆所属流程「{flow_name}」未找到，跳过 {len(entry.get('memories', []))} 条记忆"
                )
                continue

            saved_memories: list[Memory] = []
            for i, m in enumerate(entry.get("memories", [])):
                try:
                    memory = await memory_service.save_memory(
                        db,
                        agent_id=agent_id,
                        title=m.get("title", ""),
                        content=m.get("content", ""),
                        memory_type=m.get("memory_type", "cold"),
                        category=m.get("category", "event"),
                        importance=m.get("importance", 3),
                        keywords=m.get("keywords"),
                        skip_decay=True,
                        skip_vectorize=True,
                    )
                    saved_memories.append(memory)
                except Exception as e:
                    warnings.append(f"导入记忆「{m.get('title', '?')}」失败: {e}")

            if saved_memories:
                try:
                    await memory_service._vectorize_memories_batch(
                        saved_memories, db=db
                    )
                except Exception as e:
                    warnings.append(f"记忆向量化部分失败: {e}")

    # ---- 工具方法 ----

    def _check_circular_refs(self, flows_data: list[dict]) -> None:
        """检查卡片引用是否存在循环"""
        name_set = {f["name"] for f in flows_data}
        graph: dict[str, list[str]] = defaultdict(list)

        for f in flows_data:
            for node in f.get("nodes", []):
                if node.get("node_type") != "card":
                    continue
                ref_name = node.get("ref_flow_name")
                if ref_name and ref_name in name_set:
                    graph[f["name"]].append(ref_name)

        visited: set[str] = set()
        path: set[str] = set()

        def dfs(node: str) -> None:
            if node in path:
                raise ValueError(f"检测到循环卡片引用: {node}")
            if node in visited:
                return
            path.add(node)
            for child in graph.get(node, []):
                dfs(child)
            path.remove(node)
            visited.add(node)

        for f in flows_data:
            dfs(f["name"])

    async def _ensure_unique_name(
        self, db: AsyncSession, model_class: type, name: str
    ) -> str:
        """确保名称唯一，冲突时添加 (副本) 后缀"""
        candidate = name
        num = 1
        while await self._name_exists(db, model_class, candidate):
            num += 1
            candidate = f"{name} (副本{num if num > 2 else ''})"
            if num > 100:
                candidate = f"{name} ({datetime.now().strftime('%Y%m%d%H%M%S')})"
                break
        return candidate

    async def _name_exists(
        self, db: AsyncSession, model_class: type, name: str
    ) -> bool:
        """检查指定名称是否已存在"""
        stmt = select(model_class.id).where(
            model_class.name == name, model_class.is_delete == 0
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _fetch_entity_list(
        self, db: AsyncSession, model_class: type, ids: set[int]
    ) -> list:
        """根据 ID 集合获取实体列表"""
        if not ids:
            return []
        stmt = select(model_class).where(
            model_class.id.in_(ids), model_class.is_delete == 0
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


flow_transfer_service = FlowTransferService()
