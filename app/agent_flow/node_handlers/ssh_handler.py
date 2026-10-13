"""
SSH 远程执行节点处理器（LLM 工具型，同 Shell 节点模式）

无入图执行路径，仅通过工具边连接 LLM，工具实现已抽到 tools/ssh_toolkit.py
（SshToolKit：ssh_executor / ssh_upload / ssh_download / ssh_list_dir /
ssh_set_config）。
本文件保留：节点配置、本地路径解析（上传源/下载目标）、system prompt 注入。

连接策略：每次工具调用独立建立 SSH 连接（无状态），用完即关；
paramiko 为同步实现，统一经 asyncio.to_thread 包装，避免阻塞事件循环。
"""

import logging
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
    BaseNodeConfig,
    BaseNodeHandler,
    NodeVariable,
)
from app.agent_flow.tools.common import (
    MAX_FILE_SIZE,
    validate_file_path,
    validate_writable_path,
)
from app.agent_flow.tools.ssh_toolkit import SshToolKit
from app.config.build_utils import get_agent_work_dir, get_temp_dir
from app.models.flow_node import FlowNode

logger = logging.getLogger(__name__)


class SshNodeConfig(BaseNodeConfig):
    """SSH 节点配置"""

    host: str = Field("", description="远程主机地址（IP 或域名）")
    port: int = Field(22, description="SSH 端口", ge=1, le=65535)
    username: str = Field("", description="登录用户名")
    auth_type: str = Field(
        "password",
        description="认证方式: password（密码认证）或 private_key（私钥认证）",
    )
    password: str = Field("", description="登录密码（auth_type=password 时必填）")
    private_key: str = Field(
        "",
        description="私钥 PEM 内容（auth_type=private_key 时与 private_key_path 二选一，优先使用本字段）",
    )
    private_key_path: str = Field(
        "", description="私钥文件路径（本机绝对路径），未提供私钥内容时使用"
    )
    passphrase: str = Field("", description="私钥口令，无私钥口令时留空")
    connect_timeout: int = Field(10, description="SSH 连接超时秒数", ge=1, le=120)
    command_timeout: int = Field(300, description="默认命令执行超时秒数", ge=1, le=3600)
    max_transfer_mb: int = Field(
        50, description="SFTP 单文件传输大小上限（MB）", ge=1, le=2048
    )
    output_variables: list[NodeVariable] = [
        NodeVariable(name="stdout", type="string"),
        NodeVariable(name="stderr", type="string"),
        NodeVariable(name="exit_code", type="number"),
    ]
    # 注：approval_required_tools / approval_required_patterns 字段已在 BaseNodeConfig 提供，
    # 本 Config 通过继承自动获得 default=[]。


@NodeHandlerRegistry.register("ssh")
class SshNodeHandler(BaseNodeHandler):
    """
    SSH 远程执行节点处理器

    功能：
    1. 远程命令执行（双通道流式读取，超时保护）
    2. SFTP 上传/下载（大小限制 + 本地路径安全校验）
    3. 远程目录列表
    4. 每次调用独立建连，无状态复用
    """

    ConfigClass = SshNodeConfig

    # 由 llm_tool_executor 仅对 Agent 类型注入，用作下载默认目录
    _working_dir: Optional[Path] = None

    # 注：_writer / _session_id / _compiled_patterns / _resolve_context /
    # _ensure_patterns_compiled / _request_tool_approval 已下沉到 BaseNodeHandler，
    # 本类继承即可直接调用。

    def _resolve_working_dir(self) -> Optional[Path]:
        """解析本地默认工作目录：注入值优先，回退 Agent 工作目录（非 Agent 流程返回 None）"""
        if self._working_dir is not None:
            return self._working_dir
        ctx = get_execution_context()
        if ctx and ctx.expanded_flow is not None:
            flow_type = ctx.expanded_flow.flow_type
            flow_id = ctx.expanded_flow.id or ctx.flow_id
            if flow_type == "agent" and flow_id:
                return get_agent_work_dir(flow_id)
        return None

    def _resolve_local_source(self, raw_path: str, limit_bytes: int):
        """解析上传源文件（相对路径基于工作目录），校验安全性/存在性/大小上限

        Returns:
            (绝对路径, 错误消息)，成功时错误消息为 None
        """
        candidate = Path(raw_path.strip()).expanduser()
        if not candidate.is_absolute():
            base = self._resolve_working_dir() or Path.cwd()
            candidate = base / candidate
        try:
            candidate = candidate.resolve()
        except (OSError, RuntimeError) as e:
            return None, f"本地路径解析失败: {e}"
        is_valid, error_msg = validate_file_path(str(candidate))
        if not is_valid:
            return None, f"本地路径校验失败: {error_msg}"
        if not candidate.is_file():
            return None, f"本地文件不存在: {candidate}"
        size_mb = candidate.stat().st_size / 1024 / 1024
        if candidate.stat().st_size > limit_bytes:
            return None, (
                f"文件大小 {size_mb:.1f}MB 超过传输上限（{limit_bytes // 1024 // 1024}MB）"
            )
        return candidate, None

    def _resolve_local_target(self, raw_path: str, default_name: str):
        """解析下载目标路径（缺省目录时用工作目录/临时目录），校验可写安全性

        Returns:
            (绝对路径, 错误消息)，成功时错误消息为 None
        """
        base = self._resolve_working_dir() or Path(get_temp_dir())
        candidate = (
            Path(raw_path.strip()).expanduser() if raw_path else base / default_name
        )
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            candidate = candidate.resolve()
        except (OSError, RuntimeError) as e:
            return None, f"本地保存路径解析失败: {e}"
        is_valid, error_msg = validate_writable_path(str(candidate))
        if not is_valid:
            return None, f"本地保存路径校验失败: {error_msg}"
        return candidate, None

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """SSH 节点不参与入图执行（仅工具提供者），直接返回状态"""
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """固定工具名，不允许同一 LLM 连接多个 SSH 节点"""
        return False

    async def get_tool(self, node: FlowNode) -> Sequence[BaseTool]:
        """返回工具列表：组装 SshToolKit（5 个工具，实现见 tools/ssh_toolkit.py）"""
        cfg: SshNodeConfig = self._get_config(node)
        transfer_limit_bytes = min(MAX_FILE_SIZE, cfg.max_transfer_mb * 1024 * 1024)

        def _approval(tool_name: str, tool_args: dict, content: str = ""):
            """工具审批统一入口（闭包固定 cfg / node_key，对齐 shell/browser 模式）。

            Returns: None（未命中审批条件，放行）/ "approved" / "rejected" / "timeout"。
            """
            return self._check_and_request_approval(
                tool_name=tool_name,
                tool_args=tool_args,
                cfg=cfg,
                content_for_pattern=content,
                node_key=node.node_key,
            )

        kit = SshToolKit(
            cfg=cfg,
            node=node,
            approval_cb=_approval,
            transfer_limit_bytes=transfer_limit_bytes,
            resolve_local_source_cb=self._resolve_local_source,
            resolve_local_target_cb=self._resolve_local_target,
        )
        return list(await kit.build_tools())

    @classmethod
    def get_tool_info(cls, node: FlowNode) -> list[dict]:
        return [
            {"name": "ssh_executor", "description": "在远程主机上执行 Shell 命令"},
            {"name": "ssh_upload", "description": "将本地文件通过 SFTP 上传到远程主机"},
            {
                "name": "ssh_download",
                "description": "从远程主机下载文件到本地并生成下载链接",
            },
            {"name": "ssh_list_dir", "description": "列出远程主机目录内容"},
            {
                "name": "ssh_set_config",
                "description": "设置或更新 SSH 节点的连接配置（主机/凭据），持久化到节点",
            },
        ]

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:
        """向 LLM 注入 SSH 工具使用说明（含连接目标、输出控制）"""
        cfg = self._get_config(node)
        target = f"{cfg.username}@{cfg.host}:{cfg.port}"

        if (cfg.host or "").strip():
            target_line = (
                f"你已连接 SSH 节点，目标主机: `{target}`（auth_type={cfg.auth_type}）；"
                "如需更换目标主机可先调用 ssh_set_config 更新"
            )
        else:
            target_line = (
                "SSH 节点尚未预设连接信息，请先调用 ssh_set_config 提供主机与凭据"
                "（host/username/password 或 private_key），配置会持久化到节点"
            )

        lines = [
            "\n\n## SSH 远程操作\n"
            f"{target_line}。\n"
            "- ssh_executor 在远端用户默认 Shell 中执行命令；每次调用独立建连，cd 不影响后续调用\n"
            "- 大量输出先过滤（| head -100、| grep xxx 等），否则结果会被自动截断\n"
            f"- 命令默认超时 {cfg.command_timeout} 秒，长耗时可传更大的 timeout 参数（上限3600秒）；"
            "启动服务/编译等长任务建议 nohup ... & 后台化再用后续命令查日志\n"
            "- ssh_list_dir 查看远程目录结构；ssh_upload/ssh_download 用 SFTP 传文件（"
            f"单文件上限 {min(50, cfg.max_transfer_mb)}MB），下载的文件会自动获得 download_url\n"
            "- 远程路径是 POSIX 风格绝对路径；上传远程父目录不存在时会自动创建\n"
            "- 远程操作是真实系统变更：rm/chmod/systemctl restart 类高危命令，务必先向用户说明并获得确认\n"
            "- 远端命令与本地 shell 同样经过危险模式审计（rm -rf /、format、Format-Volume、"
            "dd 写入设备、curl|sh 等被直接拒绝）；命令必须为单行，多行命令将被拒绝\n"
        ]
        return "\n".join(lines)
