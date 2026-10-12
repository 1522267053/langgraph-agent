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

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.config.build_utils import get_internal_dir
from app.models.flow import Flow, FlowType
from app.models.flow_node import FlowNode, NodeType
from app.models.skill import Skill
from app.services.flow_service import flow_service, DEFAULT_AGENT_INPUT_SCHEMA
from app.services.global_config_service import global_config_service
from app.utils.node_config_helper import fill_node_defaults
from app.schemas.flow_schema import FlowCreate
from app.schemas.flow_node_schema import FlowNodeCreate
from app.schemas.flow_edge_schema import FlowEdgeCreate

logger = logging.getLogger(__name__)

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_CAPABILITY_KEYS = ("image", "video", "audio", "pdf")


def _has_any_capability(caps: Optional[dict]) -> bool:
    """capabilities 是否已启用任意多模态能力（全 False/缺失则视为未初始化）"""
    return bool(caps) and any(caps.get(k) for k in _CAPABILITY_KEYS)


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


BUILTIN_AGENT_SUGGESTED_PROMPTS = [
    # 建流程/智能体（对应 agent-manager 技能）
    "帮我做一个能回答公司制度问题的问答助手",
    "每天下午 6 点把当天的工作记录汇总成表格",
    # 定时任务与提醒（scheduled-task-manager / agenda）
    "每周五下午 5 点提醒我写周报",
    "半个小时后提醒我看远方 20 秒",
    # 知识库（knowledge-manager）
    "把这份 PDF 加入知识库并做向量化",
    # 技能扩展（skill-creator）
    "帮我封装一个新技能，让它能调用我们的内部接口",
    # 自动化与本地操作（python / shell / file 工具）
    "写一段 Python 脚本，把桌面所有截图按日期归档",
    "查看 D 盘下载目录里最占空间的 10 个文件",
    "看一下服务器上 /data 目录还有多少剩余空间",
    # 视频生成（byted-ark-seedance-skill）
    "用一段描述生成 5 秒的视频",
    # 记忆偏好（memory 工具）
    "记住我写代码用 Python、注释写中文",
]

BUILTIN_AGENT_SYSTEM_PROMPT = """你是 AI Agent OS 的内置助手，是用户与系统交互的主要入口。既能直接对话和操作用户环境，也能帮用户创建、修改、运维本系统的各类资源。

## 能力总览

按用户意图先 `load_skill(<技能名>)` 加载对应技能文档，再按文档中的 API 说明操作：

| 用户想做的事 | 先加载技能 |
|---|---|
| 搭建/修改智能体（Agent）、工作流（Workflow）、子 Agent、节点编排 | `agent-manager` |
| 创建/管理定时任务（Cron 周期、单次、周期提醒） | `scheduled-task-manager` |
| 创建/管理 WebSocket 网关、注册远程工具 | `ws-gateway-manager` |
| 添加/配置/测试 MCP 服务器与工具 | `mcp-manager` |
| 创建知识库、上传/向量化文档、知识沉淀 | `knowledge-manager` |
| 创建/修改/审计技能（SKILL.md、上传 ZIP、重载） | `skill-creator` |
| 生成视频（豆包 Seedance） | `byted-ark-seedance-skill` |

用户意图不在上表时（如读写文件、写代码并运行命令、记日程、记偏好等），直接用你已挂载的工具完成，无需加载技能。技能清单与目录树以 `load_skill` 实际返回为准，实际可用工具以运行时提供的工具列表为准（系统会随版本与用户配置增删，不要凭记忆假设名称）。

## 操作方式

- **load_skill** — 加载技能文档（SKILL.md + 目录树），技能文档内含完整 API 说明、节点配置详情与强制规则。
- **API 调用工具** — 内置 API 工具节点的工具名形如 `api_call_tool_<node_key>`，按技能文档中的说明调用后端 REST API（基址 `http://localhost:8000/api`）创建/查询/修改/删除资源。实际可用工具清单以运行时提供的工具列表为准。

## 关键约束（务必遵守）

1. **先定类型再澄清需求**：创建流程或智能体前，先加载 `agent-manager` 技能，按其「选择类型」章节判定 `flow` 还是 `agent`（拿不准时把两类特征讲给用户确认），再了解用户目标、输入输出与外部依赖，然后才动手创建。
2. **先查 Schema 再写配置**：创建或配置节点前调用 `GET /ai/flow/node-types/{node_type}/config-schema`，不得凭记忆猜字段。
3. **先读后改**：修改已有流程前先 `GET /ai/flow/{id}/detail`，保留无关节点、边和配置；配置更新是字段级合并。
4. **节点字段分两层**：`node_name`、`position_x`、`position_y` 必须放在节点对象顶层（与 `node_type`/`node_key`/`base_config` 平级），不要塞进 `base_config`，否则会被忽略。
5. **必须实跑验证**：创建或修改后实际执行一次，确认 SSE 出现 `flow_done` 且 `data.status="success"`，不能只凭 API 返回成功就认为完成。
6. **不泄露密钥**：响应中的 `api_key`、`password`、`private_key` 等敏感字段均为掩码展示，仅原样保留回传，不在回复或日志中显示明文。
7. **模型配置可留空**：创建用户智能体时 LLM 节点的 `api_key`/`provider`/`model` 可留空，系统会自动注入全局默认配置。

详细规则、节点清单与完整流程见对应技能文档，不要一次加载全部参考文件，按需读取。"""


# ---- 内置 Agent 模板模型（对齐 flow_template_service 的声明式风格） ----


class BuiltinAgentNode(BaseModel):
    """内置 Agent 模板节点"""

    node_type: str = Field(..., description="节点类型")
    node_key: str = Field(..., description="节点标识")
    node_name: str = Field(..., description="节点名称")
    position_x: int = Field(0, description="X坐标")
    position_y: int = Field(0, description="Y坐标")
    base_config: dict = Field(default_factory=dict, description="节点配置")


class BuiltinAgentEdge(BaseModel):
    """内置 Agent 模板边"""

    source_node_key: str = Field(..., description="源节点")
    target_node_key: str = Field(..., description="目标节点")
    source_handle: str = Field(default="default", description="源handle")
    target_handle: str = Field(default="default", description="目标handle")


class BuiltinAgentTemplate:
    """内置 Agent 拓扑模板

    静态拓扑声明为常量，动态值（skills / LLM 配置）通过方法参数注入：
    - Start → LLM → End 主链路 + 10 个工具节点（skill 有则挂）
    - 全部工具节点以 tools->tools 边挂到 LLM
    """

    # ---- 工具节点静态声明：(node_key, node_type, node_name, x, y, node_type_for_defaults) ----
    # node_type_for_defaults 与 node_type 相同，仅为 fill_node_defaults 参数显式化；
    # skill_tool 因依赖 skills 参数单独构造，不在此表
    _TOOL_NODES: tuple[tuple[str, str, str, int, int], ...] = (
        ("api_tool", NodeType.API.value, "API 工具", 300, 450),
        ("todo_tool", NodeType.TODO.value, "任务计划", 500, 450),
        ("python_tool", NodeType.PYTHON.value, "Python 工具", 700, 450),
        ("shell_tool", NodeType.SHELL.value, "Shell 工具", 900, 450),
        ("ssh_tool", NodeType.SSH.value, "SSH 工具", 100, 600),
        ("memory_tool", NodeType.MEMORY.value, "记忆管理", 300, 600),
        ("agenda_tool", NodeType.AGENDA.value, "日程管理", 500, 600),
        ("mcp_tool", NodeType.MCP.value, "MCP 工具", 700, 600),
        ("sub_agent_tool", NodeType.SUB_AGENT.value, "子 Agent", 900, 600),
        ("question_tool", NodeType.QUESTION.value, "问题反问", 100, 750),
    )

    @classmethod
    def nodes(
        cls,
        skills: list[dict],
        llm_config: dict,
        capabilities: dict,
        context_length: int,
    ) -> list[BuiltinAgentNode]:
        """构建全部节点（主链路 + 工具节点）

        Args:
            skills: 已注册内置 Skill 列表 [{id, name}, ...]，非空时挂 skill_tool
            llm_config: 全局 LLM 配置（provider/model/api_key/base_url）
            capabilities: 模型多模态能力（由模型元数据推导）
            context_length: 上下文窗口（全局配置优先，模型元数据兜底）
        """
        nodes = [
            BuiltinAgentNode(
                node_type=NodeType.START.value,
                node_key="start",
                node_name="开始",
                position_x=100,
                position_y=200,
                base_config={
                    "input_variables": [
                        {
                            "name": "message",
                            "type": "string",
                            "description": "用户消息",
                            "required": True,
                        },
                        {
                            "name": "files",
                            "type": "file_list",
                            "description": "上传文件",
                            "multiple": True,
                            "max_size": 20,
                            "required": False,
                        },
                    ]
                },
            ),
            BuiltinAgentNode(
                node_type=NodeType.LLM.value,
                node_key="llm",
                node_name="AI 助手",
                position_x=350,
                position_y=200,
                base_config=fill_node_defaults(
                    "llm",
                    {
                        "provider": llm_config.get("provider", ""),
                        "model": llm_config.get("model", ""),
                        "api_key": llm_config.get("api_key", ""),
                        "base_url": llm_config.get("base_url", ""),
                        "context_length": context_length,
                        "capabilities": capabilities,
                        "system_prompt": BUILTIN_AGENT_SYSTEM_PROMPT,
                        "user_prompt": "{{message}}",
                        "max_tool_iterations": 99999,
                        "input_variables": [
                            {
                                "name": "message",
                                "source": "input.message",
                                "type": "string",
                            }
                        ],
                    },
                ),
            ),
            BuiltinAgentNode(
                node_type=NodeType.END.value,
                node_key="end",
                node_name="结束",
                position_x=600,
                position_y=200,
                base_config={
                    "output_variables": [
                        {"name": "res", "source": "nodes.llm.result", "type": "string"}
                    ]
                },
            ),
        ]

        if skills:
            nodes.append(
                BuiltinAgentNode(
                    node_type=NodeType.SKILL.value,
                    node_key="skill_tool",
                    node_name="技能",
                    position_x=100,
                    position_y=450,
                    base_config=fill_node_defaults(
                        "skill", {"skill_ids": [s["id"] for s in skills]}
                    ),
                )
            )

        for key, ntype, name, x, y in cls._TOOL_NODES:
            nodes.append(
                BuiltinAgentNode(
                    node_type=ntype,
                    node_key=key,
                    node_name=name,
                    position_x=x,
                    position_y=y,
                    base_config=fill_node_defaults(ntype),
                )
            )
        return nodes

    @classmethod
    def edges(cls, tool_keys: list[str]) -> list[BuiltinAgentEdge]:
        """构建全部边：主链路 start→llm→end + 工具节点 tools->tools 挂到 llm"""
        edges = [
            BuiltinAgentEdge(source_node_key="start", target_node_key="llm"),
            BuiltinAgentEdge(source_node_key="llm", target_node_key="end"),
        ]
        edges.extend(
            BuiltinAgentEdge(
                source_node_key=key,
                target_node_key="llm",
                source_handle="tools",
                target_handle="tools",
            )
            for key in tool_keys
        )
        return edges


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
            # 补偿：旧版本创建的内置 Agent 可能没有 input_schema / suggested_prompts
            changed = False
            if not builtin_flow.input_schema:
                builtin_flow.input_schema = DEFAULT_AGENT_INPUT_SCHEMA.model_dump()
                changed = True
            if not builtin_flow.suggested_prompts:
                builtin_flow.suggested_prompts = BUILTIN_AGENT_SUGGESTED_PROMPTS.copy()
                changed = True
            # 补偿：修复前创建的 LLM 节点 capabilities 全 False，
            # 且 context_length 未按模型 limits 填充
            node_changed = await self._compensate_llm_node(db, builtin_flow.id)
            if node_changed:
                changed = True
            if changed:
                await db.commit()
                logger.info("已为内置 Agent 补充缺失字段: id=%d", builtin_flow.id)
            return builtin_flow.id

        skills = await self._ensure_skill(db)
        flow = await self._create(db, skills)
        logger.info("内置 Agent 创建成功: id=%d", flow.id)
        return flow.id

    async def _compensate_llm_node(self, db: AsyncSession, flow_id: int) -> bool:
        """
        补偿存量内置 Agent 的 LLM 节点：按已配置模型的元数据补齐派生字段

        - capabilities 全 False（或缺失）时，按模型 modalities 重新推导
        - context_length 为空时，按模型 limits.context 填充

        Returns:
            是否有字段被修改
        """
        node_query = select(FlowNode).where(
            FlowNode.flow_id == flow_id,
            FlowNode.node_type == NodeType.LLM.value,
            FlowNode.is_delete == 0,
        )
        node_result = await db.execute(node_query)
        llm_node = node_result.scalar_one_or_none()
        if not llm_node or not llm_node.base_config:
            return False

        config = llm_node.base_config if isinstance(llm_node.base_config, dict) else {}
        provider = config.get("provider")
        model = config.get("model")
        if not provider or not model:
            return False

        metadata = await self._get_model_metadata(db, provider, model)
        changed = False

        if not _has_any_capability(config.get("capabilities")):
            config["capabilities"] = metadata["capabilities"]
            changed = True
        if not config.get("context_length") and metadata["context_length"]:
            config["context_length"] = metadata["context_length"]
            changed = True

        if changed:
            llm_node.base_config = config
        return changed

    async def sync_llm_config(self, db: AsyncSession) -> None:
        """
        全局配置变更后，同步更新内置 Agent 的 LLM 节点配置

        读取全局配置，查找内置 Agent 的 LLM 节点，
        将空的 provider/model/api_key/base_url 回填为全局配置值，
        并按已配置模型的元数据联动派生 capabilities/context_length。
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
            config["provider"] = global_llm.get("provider", "")
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

        # 模型已就位时，联动派生 capabilities / context_length
        if config.get("provider") and config.get("model"):
            metadata = await self._get_model_metadata(
                db, config["provider"], config["model"]
            )
            if not _has_any_capability(config.get("capabilities")):
                config["capabilities"] = metadata["capabilities"]
                updated = True
            if not config.get("context_length") and metadata["context_length"]:
                config["context_length"] = metadata["context_length"]
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

        global_llm = await global_config_service.get_default_llm_config(db)

        flow_data = FlowCreate(
            name="AI 助手",
            description="系统内置 AI 助手，支持通用对话、创建智能体与工作流、调用各类技能",
            flow_type=FlowType.AGENT.value,
            input_schema=DEFAULT_AGENT_INPUT_SCHEMA,
            suggested_prompts=BUILTIN_AGENT_SUGGESTED_PROMPTS,
        )
        flow = await flow_service.create(db, flow_data)

        flow.is_builtin = 1
        await db.commit()
        await db.refresh(flow)

        await self._build_nodes_and_edges(db, flow, skills, global_llm)
        new_flow = await flow_service.get_with_nodes_and_edges(db, flow.id)
        if not new_flow:
            raise ValueError(f"创建内置智能体失败: flow_id={flow.id} 查询不到流程")
        return new_flow

    async def reset(self, db: AsyncSession) -> int:
        """恢复内置 Agent 出厂设置：删除节点和边，用模板重新构建"""
        query = select(Flow).where(
            Flow.is_builtin == 1,
            Flow.is_delete == 0,
            Flow.flow_type == FlowType.AGENT.value,
        )
        result = await db.execute(query)
        flow = result.scalar_one_or_none()
        if not flow:
            raise ValueError("内置 Agent 不存在")

        # 重置 flow 元数据
        flow.name = "AI 助手"
        flow.description = (
            "系统内置 AI 助手，支持通用对话、创建智能体与工作流、调用各类技能"
        )
        flow.input_schema = DEFAULT_AGENT_INPUT_SCHEMA.model_dump()
        flow.suggested_prompts = BUILTIN_AGENT_SUGGESTED_PROMPTS.copy()
        await db.commit()

        # 获取现有节点并按 key 批量删除（级联删除边）
        existing_nodes = await flow_service.get_flow_nodes(db, flow.id)
        node_keys = [n.node_key for n in existing_nodes]
        if node_keys:
            await flow_service.batch_delete_nodes_by_keys(db, flow.id, node_keys)

        # 重新构建节点和边

        global_llm = await global_config_service.get_default_llm_config(db)
        skills = await self._ensure_skill(db)
        await self._build_nodes_and_edges(db, flow, skills, global_llm)
        logger.info("内置 Agent 已恢复出厂设置: id=%d", flow.id)
        return flow.id

    async def _get_model_metadata(
        self, db: AsyncSession, provider: str, model: str
    ) -> dict:
        """从模型元数据推导 LLM 节点可自动填充的配置

        返回:
            {
                "capabilities": 多模态能力开关（公共函数按 modalities.input 推导），
                "context_length": 上下文窗口（limits.context），无则 0，
            }
        """
        from app.models.ai_model import AIModel
        from app.utils.node_config_helper import derive_model_capabilities

        caps = await derive_model_capabilities(db, provider, model)
        metadata = {"capabilities": caps, "context_length": 0}
        if not provider or not model:
            return metadata

        query = select(AIModel).where(
            AIModel.provider_id == provider,
            AIModel.model_id == model,
            AIModel.is_delete == 0,
        )
        result = await db.execute(query)
        ai_model = result.scalar_one_or_none()
        if not ai_model:
            return metadata

        limits = ai_model.limits or {}
        if isinstance(limits, dict):
            metadata["context_length"] = limits.get("context") or 0
        return metadata

    async def _build_nodes_and_edges(
        self, db: AsyncSession, flow: Flow, skills: list[dict], global_llm: dict
    ) -> None:
        """构建内置 Agent 的节点和边（Start → LLM → End + 全部工具节点）

        编排层：解析 LLM 动态配置 → BuiltinAgentTemplate 声明拓扑 → 批量落库。
        拓扑细节见 BuiltinAgentTemplate。
        """
        provider_name = global_llm.get("provider", "")
        model = global_llm.get("model", "")
        base_url = global_llm.get("base_url", "")
        model_meta = await self._get_model_metadata(db, provider_name, model)
        capabilities = model_meta["capabilities"]
        context_length = (
            global_llm.get("context_length") or model_meta["context_length"] or 0
        )

        if not base_url:
            from app.services.ai_provider_service import ai_provider_service

            provider = await ai_provider_service.get_by_provider_id(db, provider_name)
            base_url = provider.api_url if provider and provider.api_url else ""

        nodes = BuiltinAgentTemplate.nodes(
            skills=skills,
            llm_config=global_llm,
            capabilities=capabilities,
            context_length=context_length,
        )
        tool_keys = [
            n.node_key for n in nodes if n.node_key not in ("start", "llm", "end")
        ]
        edges = BuiltinAgentTemplate.edges(tool_keys)

        node_creates = [
            FlowNodeCreate(
                flow_id=flow.id,
                node_type=n.node_type,
                node_key=n.node_key,
                node_name=n.node_name,
                position_x=n.position_x,
                position_y=n.position_y,
                base_config=n.base_config,
            )
            for n in nodes
        ]
        await flow_service.batch_create_nodes(db, flow.id, node_creates)

        edge_creates = [
            FlowEdgeCreate(
                flow_id=flow.id,
                source_node_key=e.source_node_key,
                target_node_key=e.target_node_key,
                source_handle=e.source_handle,
                target_handle=e.target_handle,
            )
            for e in edges
        ]
        await flow_service.batch_create_edges(db, flow.id, edge_creates)


builtin_agent_service = BuiltinAgentService()
