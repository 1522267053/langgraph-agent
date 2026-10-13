"""
Shell命令执行节点处理器
提供受限的命令执行环境

工具实现已抽到 tools/shell_toolkit.py（ShellToolKit：5 个 shell 工具 + 后台任务
状态机）与 tools/file_toolkit.py（FileToolsKit：6 个通用文件工具）。
本文件保留：节点配置、Flow 入图执行路径（execute）、审批、工作目录解析、
system prompt 注入。
"""

import asyncio
import logging
import os
import platform
from pathlib import Path
from typing import Optional, Sequence

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import StreamWriter
from pydantic import Field

from app.agent_flow.execution_context import get_execution_context
from app.agent_flow.flow_context import FlowState
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.agent_flow.tools.common import validate_file_path as _validate_file_path
from app.agent_flow.tools.file_read import FileReadService
from app.agent_flow.tools.file_toolkit import FileToolsKit
from app.agent_flow.tools.shell_toolkit import (
    ShellToolKit,
    _create_subprocess,
    _decode_output,
    _force_kill_process_tree,
    validate_command,
)
from app.config.build_utils import BASE_DIR, get_agent_work_dir, get_temp_dir
from app.config.settings import settings
from app.models.flow_node import FlowNode

logger = logging.getLogger(__name__)


class ShellNodeConfig(BaseNodeConfig):
    command: str = ""
    # 工具模式（连接到 LLM）下的空闲超时：连续 N 秒无任何 stdout/stderr 输出才判超时。
    # 服务器/长驻进程持续吐日志永不误杀；真正卡死的无输出命令照样杀
    timeout: int = 300
    # 工具模式下的最大总时长硬上限（秒），默认 1800（半小时）；0/None=不限制。
    # 到点必杀（防进程泄漏），与空闲超时先到先触发。
    # flow 直跑模式不使用此配置（总时长语义不变）
    max_duration: Optional[int] = 1800
    async_wait: int = 8
    default_workdir: str = Field(
        "",
        description=(
            "默认工作目录（绝对路径，相对路径基于项目根目录解析）。"
            "优先级：会话工作目录（聊天页配置）> 此配置 > Agent 工作目录；"
            "每次调用 shell_executor 的 workdir 参数可临时覆盖此值"
        ),
    )
    output_variables: list[NodeVariable] = [
        NodeVariable(name="stdout", type="string"),
        NodeVariable(name="stderr", type="string"),
        NodeVariable(name="exit_code", type="number"),
    ]
    # 注：approval_required_tools / approval_required_patterns 字段已在 BaseNodeConfig 提供，
    # 本 Config 通过继承自动获得 default=[]。子类可在 get_config_schema() 输出中由基类一并展示。


@NodeHandlerRegistry.register("shell")
class ShellNodeHandler(BaseNodeHandler):
    """
    Shell命令执行节点处理器

    功能：
    1. 在受限环境中执行Shell命令
    2. 黑名单机制：禁止预定义的危险命令
    3. 危险操作检测
    4. 超时控制
    5. 捕获标准输出和标准错误
    """

    ConfigClass = ShellNodeConfig

    _working_dir: Optional[Path] = None
    # 会话级工作目录（llm_tool_executor 注入，用户在聊天页显式配置；未配置为 None）
    _session_work_dir: Optional[Path] = None
    # file_read 可自动注入的媒体类型集合（由 llm_tool_executor 按模型能力×适配器注入；空则媒体文件按普通文件处理）
    _media_caps: set = set()

    # 注：_writer / _session_id / _compiled_patterns / _resolve_context /
    # _ensure_patterns_compiled / _request_tool_approval 已下沉到 BaseNodeHandler，
    # 本类继承即可直接调用。

    def _resolve_working_dir(self) -> Optional[Path]:
        """解析默认层工作目录（未配置会话目录和节点 default_workdir 时的兜底）

        优先级：
        1. self._working_dir（由 llm_tool_executor 仅对 Agent 类型注入）
        2. ExecutionContext 中的 flow 信息（Shell 节点执行路径），仅 Agent 类型生效
        3. 返回 None（Flow 类型，保持默认 cwd）
        """
        if self._working_dir is not None:
            return self._working_dir
        ctx = get_execution_context()
        if ctx and ctx.expanded_flow is not None:
            flow_type = ctx.expanded_flow.flow_type
            flow_id = ctx.expanded_flow.id or ctx.flow_id
            if flow_type == "agent" and flow_id:
                return get_agent_work_dir(flow_id)
        return None

    def _configured_workdir(
        self, cfg: BaseNodeConfig
    ) -> tuple[Optional[Path], Optional[str]]:
        """解析节点级 default_workdir 配置

        Returns:
            (目录, 警告信息)。未配置返回 (None, None)；配置非法时返回 (None, 警告)，
            由调用方回退默认目录，警告仅透出到运行时提醒。
        """
        raw = (getattr(cfg, "default_workdir", "") or "").strip()
        if not raw:
            return None, None
        try:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = BASE_DIR / candidate
            candidate = candidate.resolve()
        except (OSError, RuntimeError):
            return None, f"default_workdir 无法解析: {raw}"
        if not candidate.is_dir():
            return None, f"default_workdir 目录不存在: {candidate}"
        is_valid, error_msg = _validate_file_path(str(candidate))
        if not is_valid:
            return None, f"default_workdir 校验失败: {error_msg}"
        return candidate, None

    def _effective_working_dir(self, cfg: BaseNodeConfig) -> Optional[Path]:
        """按优先级解析工作目录：会话级(前端配置) > 节点 default_workdir > Agent 注入/ExecutionContext"""
        if self._session_work_dir is not None:
            return self._session_work_dir
        configured_dir, _warn = self._configured_workdir(cfg)
        if configured_dir is not None:
            return configured_dir
        return self._resolve_working_dir()

    def _resolve_tool_working_dir(
        self, workdir: Optional[str], default_dir: Optional[Path] = None
    ) -> Optional[Path]:
        """解析单次 Shell 工具调用的工作目录。"""
        if default_dir is None:
            default_dir = self._resolve_working_dir()
        if workdir is None or not workdir.strip():
            return default_dir

        try:
            candidate = Path(workdir).expanduser()
            if not candidate.is_absolute():
                candidate = (default_dir or Path.cwd()) / candidate
            candidate = candidate.resolve()
        except (OSError, RuntimeError) as e:
            raise ValueError(f"工作目录解析失败: {e}") from e

        is_valid, error_msg = _validate_file_path(str(candidate))
        if not is_valid:
            raise ValueError(f"工作目录校验失败: {error_msg}")
        if not candidate.exists():
            raise ValueError(f"工作目录不存在: {candidate}")
        if not candidate.is_dir():
            raise ValueError(f"工作目录不是目录: {candidate}")
        return candidate

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        cfg = self._get_config(node)

        command_template = cfg.command
        timeout = cfg.timeout

        if not command_template:
            return state

        context = self._resolve_input_variables(cfg.input_variables, state)
        command = self._render_template(command_template, state, context)

        if not command:
            return state

        is_valid, error_msg = validate_command(command)
        if not is_valid:
            state.add_error(node.node_key, error_msg)
            return state

        try:
            result = await self._execute_shell(
                command, timeout, self._effective_working_dir(cfg)
            )
            output_names = self._resolve_output_var_names(node)
            stdout_name = output_names[0] if len(output_names) > 0 else "stdout"
            stderr_name = output_names[1] if len(output_names) > 1 else "stderr"
            exit_code_name = output_names[2] if len(output_names) > 2 else "exit_code"
            state.set_node_variable(node.node_key, stdout_name, result["stdout"])
            state.set_node_variable(node.node_key, stderr_name, result["stderr"])
            state.set_node_variable(
                node.node_key, exit_code_name, result["return_code"]
            )
        except asyncio.TimeoutError:
            state.add_error(node.node_key, f"命令执行超时（{timeout}秒）")
        except Exception as e:
            state.add_error(node.node_key, f"命令执行失败: {str(e)}")

        return state

    async def _execute_shell(
        self, command: str, timeout: float, cwd: Optional[Path] = None
    ) -> dict:
        """执行Shell命令（Flow 节点专用，stdin 重定向到 DEVNULL 防止交互阻塞）

        Windows 经 cmd.exe 单行执行，POSIX 经 sh。

        Args:
            command: Shell命令字符串
            timeout: 超时时间（秒）
            cwd: 工作目录（None 时继承服务进程目录）

        Returns:
            包含执行结果的字典
        """
        try:
            process = await _create_subprocess(
                command, stdin=asyncio.subprocess.DEVNULL, cwd=cwd
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await _force_kill_process_tree(process)
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (asyncio.TimeoutError, Exception):
                    pass
                raise

            return {
                "stdout": _decode_output(stdout),
                "stderr": _decode_output(stderr),
                "return_code": process.returncode,
                "success": process.returncode == 0,
                "command": command,
                "cwd": str(cwd) if cwd else os.getcwd(),
            }

        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "return_code": -1,
                "success": False,
                "command": command,
            }

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        if config is None:
            config = node.base_config or {}

        raw_command = config.get("command", "")
        if not raw_command:
            return None

        input_vars = config.get("input_variables", [])
        context = {}
        for var in input_vars:
            name = var.get("name", "")
            source = var.get("source", "")
            if name and source:
                context[name] = resolver.resolve_safe(source, state)

        return {
            "command": resolver.render_template(raw_command, state, context),
            "timeout": config.get("timeout", 30),
        }

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """Shell 节点使用固定工具名，不允许同一 LLM 连接多个 Shell 节点"""
        return False

    async def get_tool(self, node: FlowNode) -> Sequence[BaseTool]:
        """返回工具列表：组装 ShellToolKit（5 个 shell 工具）+ FileToolsKit（6 个
        文件工具）+ FileReadService（file_read），工具实现均在 tools/ 目录。"""
        cfg = self._get_config(node)

        # 工作目录优先级：会话级(前端配置) > 节点 default_workdir > Agent 工作目录，
        # 供所有工具闭包共用
        configured_dir, _configured_dir_warn = self._configured_workdir(cfg)
        base_working_dir = (
            self._session_work_dir
            if self._session_work_dir is not None
            else configured_dir
            if configured_dir is not None
            else self._resolve_working_dir()
        )

        def _approval(tool_name: str, tool_args: dict, content: str = ""):
            """工具审批统一入口（闭包固定 cfg / node_key，对齐 browser/ssh 模式）。

            Returns: None（未命中审批条件，放行）/ "approved" / "rejected" / "timeout"。
            """
            return self._check_and_request_approval(
                tool_name=tool_name,
                tool_args=tool_args,
                cfg=cfg,
                content_for_pattern=content,
                node_key=node.node_key,
            )

        shell_kit = ShellToolKit(
            cfg=cfg,
            node_key=node.node_key,
            approval_cb=_approval,
            base_working_dir=base_working_dir,
            timeout=cfg.timeout,
            async_wait=cfg.async_wait,
            system_info=f"当前系统: {platform.system()}",
        )
        file_kit = FileToolsKit(
            approval_cb=_approval,
            base_working_dir=base_working_dir,
        )

        tools: list[BaseTool] = []
        tools.extend(await shell_kit.build_tools(self._resolve_tool_working_dir))
        tools.extend(await file_kit.build_tools())

        # ---- file_read（实现在 tools/file_read.py，此处仅构造服务并注册）----
        file_read_service = FileReadService(self._media_caps)
        tools.append(file_read_service.build_tool())

        return tools

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        info = [
            {"name": "shell_executor", "description": "在受限环境中执行Shell命令"},
            {"name": "shell_task_status", "description": "查询后台Shell任务状态"},
            {"name": "shell_task_input", "description": "向后台Shell任务发送输入"},
            {"name": "shell_task_cancel", "description": "取消后台Shell任务"},
            {"name": "shell_wait", "description": "主动等待指定秒数（1~120秒）"},
            {
                "name": "file_read",
                "description": "读取文件内容（图片/音频/PDF 多模态注入，xlsx/docx 转文本）",
            },
            {"name": "text_editor", "description": "编辑文件内容"},
            {"name": "file_write", "description": "写入文件"},
            {
                "name": "file_delete",
                "description": "删除文件",
            },
            {"name": "file_search", "description": "搜索文件"},
            {"name": "list_files", "description": "按文件名查找文件"},
            {
                "name": "upload_to_file_manager",
                "description": "将生成的文件导入文件管理并返回下载链接",
            },
        ]
        return info

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """返回临时文件目录说明和文件工具使用指南，追加到 LLM system_prompt"""
        temp_dir = get_temp_dir()
        ps_compat_hint = (
            (
                "### Windows 命令环境（cmd.exe）\n"
                "- 命令必须为单行（2026-09-13 决定：所有平台硬拒绝多行）；多条独立命令用 && "
                "连成单行（前一条失败则不继续）；不看成败的顺序执行用单个 &。\n"
                '- 多行 Python 代码先用 file_write 写入 .py 文件，再 python <文件路径> 执行；单行可用 python -c "..."。\n'
                "- 环境变量用 %VAR%（不是 $env:VAR）；目录列表用 dir；禁止调用 powershell/pwsh（含链式与全路径写法，会被直接拦截），一律使用 cmd 等价命令。\n"
                "- 大量输出先过滤（findstr / 重定向到文件后用 file_read 分段读取），不要依赖 head/tail（cmd 没有）。\n"
                "- curl 接口响应常带 UTF-8 BOM：python 解析管道 JSON 禁止 json.load(sys.stdin)，改用 json.loads(sys.stdin.buffer.read().decode('utf-8-sig'))。\n"
                "- 返回的 exit_code 取自最后一个原生命令（如 python/curl/git）；内部命令（dir/echo 等）固定返回 0。\n"
            )
            if platform.system() == "Windows"
            else ""
        )
        filter_hint = (
            "| findstr / 或重定向到文件后用 file_read 读取"
            if platform.system() == "Windows"
            else "| head / | tail / grep"
        )

        # 媒体查看提示按模型实际能力（模型能力×适配器交集）分支：
        # 有能力时明确告知并引导主动查看（工具产出媒体后不再漏看）；
        # 无能力时明确禁止尝试，避免模型白试 file_read 报错后形成"看不了图"的错误经验
        media_caps = self._media_caps or set()
        _media_cap_labels = {"image": "图片", "audio": "音频", "pdf": "PDF"}
        supported_labels = "/".join(
            _media_cap_labels[c] for c in ("image", "audio", "pdf") if c in media_caps
        )
        media_hint = (
            (
                "### 查看多媒体（重要）\n"
                f"你具备查看本地{supported_labels}的能力：用 file_read 传入媒体文件的本地路径即可，"
                "内容会自动以多模态注入下一轮对话，不要声称无法查看图片/媒体\n"
                "- 工具执行产出媒体文件（截图、导出的 PDF 等）后，如需查看其内容，"
                "主动用 file_read 读取该文件路径\n"
                "- xlsx/docx 自动转文本读取；不支持网络 URL、视频和 .xls/.doc 旧格式，"
                "禁止用 file_read 读其他二进制文件\n"
            )
            if supported_labels
            else (
                "### 媒体查看（当前模型不支持）\n"
                "当前模型不支持查看图片/音频/PDF，用户要求查看媒体文件时如实说明，"
                "不要尝试用 file_read 读取媒体文件（会返回错误），也不要声称已查看媒体内容\n"
                "- xlsx/docx 自动转文本读取；不支持网络 URL、视频和 .xls/.doc 旧格式，"
                "禁止用 file_read 读其他二进制文件\n"
            )
        )

        lines = [
            "\n\n## Shell 与文件操作\n"
            "你已连接 Shell 执行节点。先用 file_search 在项目中搜索目标，再用 file_read 读取文件内容，用 text_editor 精确替换；创建新文件用 file_write\n"
            + media_hint
            + "### 输出控制（重要）\n"
            f"- 执行命令前先评估可能的输出量，大量输出务必先过滤（{filter_hint}），或重定向到文件后用 file_read 分段读取\n"
            "- 如果命令输出被截断（返回 _truncated 标记），完整内容已自动保存到临时文件，需要时用 file_read 读取\n"
            f"- file_read 单次最多读取 {settings.tool_output_max_lines} 行，大文件用 offset 参数分段读取，续读位置见返回的提示信息\n"
            "- file_search 搜索文件内容（正则匹配），支持目录递归或单个文件路径\n"
            "- list_files 按文件名 glob 匹配（如 **/*.py），用于查找文件或了解目录结构\n"
            "- 禁止用 cat 读取大文件，始终使用 file_read\n"
            "- Shell 每次调用都是独立进程；切换目录时传入 shell_executor 的 workdir 参数，不要依赖 cd 影响后续调用\n"
            "- 长时间任务超过等待秒数会转后台并返回 task_id：后台任务支持并发与多次长阻塞查询（wait_time 最长120秒），"
            "期间可继续执行其他工具调用\n"
            "- 需要等待时（后台任务推进、服务启动、文件生成、重试间隔）用 shell_wait 主动等待（1~120秒），"
            "不要用高频空转轮询消耗调用次数\n"
            "### 后台任务三件套（status / input / cancel）联动模式\n"
            "后台任务推荐按以下顺序使用三个工具：\n"
            "1. shell_task_status(task_id, wait_time)：查询进度。运行中（status=running）也会实时返回 stdout/stderr，"
            "**stdout 含交互提示时立即调 shell_task_input**（不要用更大的 wait_time 继续等）\n"
            "2. shell_task_input(task_id, input_text)：当 shell_task_status 返回的 stdout 含"
            "'Enter your name:'/'Password:'/'Continue? [y/n]'/'Press any key' 等交互提示时，"
            "用 LLM 根据上下文生成的回复文本作为 input_text 写入进程 stdin。input_text 自动追加换行符\n"
            "3. shell_task_cancel(task_id)：任务超时或用户要求中止时调用，"
            "整树清杀进程（不会留孤儿进程）\n"
            "**反模式**：用 shell_wait 轮询后台任务的进度——shell_wait 仅适合无 task_id 的等待场景；"
            "后台任务应始终用 shell_task_status 的 wait_time 阻塞等待\n"
            + ps_compat_hint
            + (
                f"\n临时文件输出目录: `{temp_dir}`（7天后自动清理，勿存放重要数据）。"
                "一次性验证/分析脚本（依赖检查、diff 对比、数据抽样等）一律用 file_write "
                "写到该目录（文件名建议 _check/_tmp 前缀），禁止写入项目源码目录或工作目录，"
                "用完无需手动删除"
            )
        ]
        return "\n".join(lines)

    async def get_runtime_reminder(self, node: FlowNode) -> Optional[str]:
        """返回运行时提醒，拼入消息层 <system-reminder>

        包含两部分：
        - 生效工作目录：会话级(聊天页配置) > 节点 default_workdir > Agent 默认目录，
          default_workdir 配置非法时附回退警告
        - 文件回退规则（行为约束）：file_* 工具的变更可随消息回退恢复，
          shell 命令的文件操作不可回退
        """
        cfg = self._get_config(node)
        configured_dir, configured_warn = self._configured_workdir(cfg)
        if self._session_work_dir is not None:
            source = "会话级配置，优先于节点 default_workdir"
            resolved = self._session_work_dir
        elif configured_dir is not None:
            source = "节点 default_workdir 配置，优先于 Agent 默认目录"
            resolved = configured_dir
        else:
            source = None
            resolved = self._resolve_working_dir()
        if resolved is None and not configured_warn:
            return None

        lines = []
        if resolved is not None:
            source_note = f"（{source}）" if source else ""
            lines.append(
                f"默认工作目录(项目路径): `{resolved}`{source_note}，"
                "Shell 未传 workdir 参数时在此目录下执行，文件操作优先使用此目录"
            )
        if configured_warn:
            lines.append(f"⚠️ {configured_warn}，已回退默认工作目录")
        lines.append(
            "对话回退只能自动恢复通过 file_write / text_editor / file_delete 产生的"
            "文件变更（创建/修改/删除均可回退）；用 shell 命令创建、修改或删除的文件"
            "无法回退，用户可能需要回退的文件操作务必使用这三个工具，"
            "shell 写文件仅用于临时输出/日志"
        )
        return "\n".join(lines)
