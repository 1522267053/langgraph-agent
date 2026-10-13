"""
浏览器操作节点处理器（DrissionPage / CDP，chrome-devtools snapshot 范式）

双形态：
1. Agent 工具（agent_tool=True）：挂 LLM tools 边，12 个工具实现已抽到
   tools/browser_toolkit.py（BrowserToolKit + BrowserSessionManager）；
2. Flow 主节点（flow=True）：配置驱动单步动作（url+action+selector），结果写输出变量

连接模式（connect_mode）：
- launch（默认）：每流程独立浏览器实例。显式空闲端口（socket 探测，不用 auto_port——
  源码实锤 auto_port 会强制覆盖 user_data_path，二者互斥）+ 数据目录：
  user_data_path 配置 → {user_data_path}/flow_{flow_id}/（登录态按流程持久化）；
  未配置 → workspace/temp/browser_data/flow_{flow_id}/（临时，quit(del_data=True) 清理）。
  flow 结束（close 动作）时自动 quit；空闲超过 600s 的会话由后台协程
  自动回收（防 Agent 忘调 close 时 Chromium 进程泄漏）。
- remote（高级）：连接远程主机已开调试端口的浏览器（ip:port），CDP 无认证，
  同地址多流程并发时告警（共享浏览器会互相干扰）。
"""

import asyncio
import json
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
from app.agent_flow.tools.browser_toolkit import (
    SNAPSHOT_JSON_JS,
    BrowserToolKit,
    _ELE_FIND_TIMEOUT,
    _normalize_locator,
    browser_session_manager,
    get_flow_id,
)
from app.models.flow_node import FlowNode


class BrowserNodeConfig(BaseNodeConfig):
    """浏览器节点配置"""

    connect_mode: str = Field(
        "launch",
        description="连接模式: launch（默认，每流程独立实例）或 remote（高级，连接远程主机已开调试端口的浏览器）",
    )
    browser_path: str = Field(
        "",
        description=(
            "浏览器可执行文件路径（仅 launch 模式）。"
            "留空=自动探测（注册表→常见安装路径→PATH 搜索 Chrome/Edge）；配置则直接启动"
        ),
    )
    user_data_path: str = Field(
        "",
        description=(
            "浏览器数据保存位置（仅 launch 模式，可选）。"
            "留空=使用临时目录（浏览器退出自动清理，登录态不保留）；"
            "配置则按流程派生子目录持久化，登录态/Cookie 跨运行保留"
        ),
    )
    remote_host: str = Field(
        "",
        description="远程主机地址（仅 remote 模式，如 192.168.1.100）",
    )
    remote_port: int = Field(
        9222,
        description="远程调试端口（仅 remote 模式，默认 9222；远程主机需以 --remote-debugging-port 启动浏览器）",
    )
    debug_port: int = Field(
        0,
        description=(
            "本地调试端口（仅 launch 模式）。0=自动探测空闲端口（默认，推荐）；"
            "配置具体端口（如 9333）则固定使用，便于外部 DevTools/CDP 客户端接入调试"
        ),
    )
    action_timeout: int = Field(
        30,
        description="单次浏览器操作超时（秒）",
    )
    headless: bool = Field(
        True,
        description=(
            "无头模式（仅 launch 模式）：true=后台运行不弹窗口（默认）；"
            "false=弹出真实浏览器窗口，适合调试观察自动化过程"
        ),
    )
    # Flow 主节点动作配置：一个节点执行一个具体动作，同流程多个浏览器节点
    # 串联时按 flow_id 复用同一浏览器实例（start → navigate → click → ...）。
    # 参数经 flow_args 逐参数绑定（支持 {{变量}} 插值），与 MCP 节点 tool_args 同模式
    flow_action: str = Field(
        "navigate",
        description=(
            "Flow 模式动作: start（开启浏览器）/ navigate（访问页面）/ click（点击元素）/ "
            "input（输入文本）/ get_content（取页面文本）/ snapshot（元素快照 JSON）/ screenshot（截图）/ "
            "scroll（滚动）/ wait_for（等待文本）/ evaluate（执行 JS）/ close（关闭浏览器）"
        ),
    )
    flow_args: dict = Field(
        default_factory=dict,
        description="动作参数（逐参数绑定值，支持 {{变量}} 插值），键随 flow_action 变化",
    )
    # 显式声明默认输出变量（MCP 同款）：动作结果 dict 整体写入 result，
    # 下游用 nodes.节点key.result.url / .content / .note 等子路径取字段
    output_variables: list[NodeVariable] = [NodeVariable(name="result", type="object")]


@NodeHandlerRegistry.register("browser")
class BrowserNodeHandler(BaseNodeHandler):
    """
    浏览器操作节点处理器

    Agent 工具（snapshot 范式）+ Flow 主节点（配置驱动单步动作）
    """

    ConfigClass = BrowserNodeConfig

    def _resolve_working_dir(self) -> Optional[Path]:
        ctx = get_execution_context()
        if ctx and ctx.expanded_flow is not None:
            return None
        return None

    # 各 flow_action 的参数 schema（供前端动态渲染参数表单，MCP parameters 同结构）
    FLOW_ACTION_PARAMS: dict[str, list[dict]] = {
        "start": [],
        "close": [],
        "navigate": [
            {
                "name": "url",
                "type": "string",
                "description": "要访问的页面 URL",
                "required": True,
            }
        ],
        "click": [
            {
                "name": "selector",
                "type": "string",
                "description": "元素定位符：CSS 选择器或 DrissionPage 文本定位符（如 t:登录）",
                "required": True,
            }
        ],
        "input": [
            {
                "name": "selector",
                "type": "string",
                "description": "输入框定位符：CSS 选择器或文本定位符",
                "required": True,
            },
            {
                "name": "text",
                "type": "string",
                "description": "要输入的文本",
                "required": True,
            },
            {
                "name": "clear",
                "type": "boolean",
                "description": "输入前是否清空已有内容（默认 true）",
                "required": False,
            },
        ],
        "get_content": [
            {
                "name": "selector",
                "type": "string",
                "description": "可选元素定位符，留空取整个页面文本",
                "required": False,
            }
        ],
        "snapshot": [],
        "screenshot": [
            {
                "name": "full_page",
                "type": "boolean",
                "description": "是否截取整个页面（默认 false 只截可视区域）",
                "required": False,
            }
        ],
        "scroll": [
            {
                "name": "direction",
                "type": "string",
                "description": "滚动方向: down（默认）/ up",
                "required": False,
            },
            {
                "name": "amount",
                "type": "number",
                "description": "滚动像素（默认 600）",
                "required": False,
            },
        ],
        "wait_for": [
            {
                "name": "text",
                "type": "string",
                "description": "要等待出现的页面文本",
                "required": True,
            },
            {
                "name": "timeout",
                "type": "number",
                "description": "超时秒数（默认 10）",
                "required": False,
            },
        ],
        "evaluate": [
            {
                "name": "script",
                "type": "string",
                "description": "要执行的 JavaScript（用 return 返回值）",
                "required": True,
            }
        ],
    }

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        """Flow 主节点：按 flow_action 执行单个浏览器动作

        同流程多个浏览器节点串联时按 flow_id 复用同一浏览器实例：
        start → navigate → input → click → get_content → ... → close
        参数经 flow_args 逐参数绑定（{{变量}} 插值），与 MCP 节点 tool_args 同模式。
        """
        cfg: BrowserNodeConfig = self._get_config(node)
        flow_id = get_flow_id()
        action = (cfg.flow_action or "navigate").strip()

        # 0. 动作与参数校验前置（缺参数时不应白白启动浏览器）
        if action not in self.FLOW_ACTION_PARAMS:
            state.add_error(
                node.node_key,
                f"未知 flow_action: {action}（支持 start/navigate/click/input/"
                "get_content/snapshot/screenshot/scroll/wait_for/evaluate/close）",
            )
            return state

        # 1. 解析输入变量 → flow_args 深度插值（MCP tool_args 同款）
        context = self._resolve_input_variables(cfg.input_variables or [], state)
        args = self._resolver.resolve_config(cfg.flow_args or {}, state, context)
        args = args if isinstance(args, dict) else {}

        def arg(name: str, default=""):
            v = args.get(name, default)
            return default if v is None else v

        # 逐参数校验必填项（schema 驱动，避免逐动作手写漏参数）
        missing = [
            p["name"]
            for p in self.FLOW_ACTION_PARAMS[action]
            if p["required"] and not str(arg(p["name"], "")).strip()
        ]
        if missing:
            state.add_error(
                node.node_key, f"动作「{action}」缺少必填参数: {', '.join(missing)}"
            )
            return state

        # start/close 不需要提前建会话（close 可能无会话；start 在动作内建）
        if action not in ("start", "close"):
            try:
                session = await browser_session_manager.get_or_create(cfg, flow_id)
            except Exception as e:
                state.add_error(node.node_key, f"浏览器启动/连接失败: {e}")
                return state

        def _run() -> dict:
            if action == "start":
                return {"note": "浏览器已开启"}
            if action == "close":
                return {"note": "浏览器已关闭"}

            tab = session.active_tab or session.browser.latest_tab
            session.active_tab = tab
            result: dict = {"url": tab.url, "title": tab.title}

            if action == "navigate":
                url = str(arg("url", "")).strip()
                if not url:
                    raise ValueError("navigate 需要配置 url 参数")
                tab.get(url)
                result.update(url=tab.url, title=tab.title, note=f"已访问 {tab.url}")
            elif action == "click":
                sel = str(arg("selector", "")).strip()
                if not sel:
                    raise ValueError("click 需要配置 selector 参数")
                ele = tab.ele(_normalize_locator(sel), timeout=_ELE_FIND_TIMEOUT)
                if not ele:
                    raise ValueError(f"未找到元素: {sel}")
                ele.click()
                result["note"] = f"已点击 {sel}"
            elif action == "input":
                sel = str(arg("selector", "")).strip()
                if not sel:
                    raise ValueError("input 需要配置 selector 参数")
                ele = tab.ele(_normalize_locator(sel), timeout=_ELE_FIND_TIMEOUT)
                if not ele:
                    raise ValueError(f"未找到元素: {sel}")
                text = str(arg("text", ""))
                if bool(arg("clear", True)):
                    ele.clear()
                ele.input(text)
                result["note"] = f"已向 {sel} 输入文本"
            elif action == "get_content":
                sel = _normalize_locator(str(arg("selector", "")))
                content = (
                    ele.text
                    if sel and (ele := tab.ele(sel, timeout=_ELE_FIND_TIMEOUT))
                    else tab.ele("t:body").text
                )
                result["content"] = content
            elif action == "snapshot":
                # Flow 模式输出 JSON elements（Agent 工具模式才输出层级文本）
                raw = tab.run_js(SNAPSHOT_JSON_JS)
                result["elements"] = (
                    json.loads(raw) if isinstance(raw, str) else (raw or [])
                )
            elif action == "screenshot":
                result["screenshot_bytes"] = tab.get_screenshot(
                    as_bytes="png", full_page=bool(arg("full_page", False))
                )
            elif action == "scroll":
                direction = str(arg("direction", "down"))
                amount = int(arg("amount", 600))
                delta = amount if direction == "down" else -amount
                tab.run_js(f"window.scrollBy(0, {delta}); return 'scrolled'")
                result["note"] = f"已向{direction}滚动 {amount}px"
            elif action == "wait_for":
                text = str(arg("text", "")).strip()
                if not text:
                    raise ValueError("wait_for 需要配置 text 参数")
                timeout = int(arg("timeout", 10))
                found = tab.wait.ele_displayed(f"text={text}", timeout=timeout)
                result["found"] = bool(found)
                result["note"] = (
                    f"等待文本「{text}」: {'已出现' if found else '超时未出现'}"
                )
            elif action == "evaluate":
                script = str(arg("script", "")).strip()
                if not script:
                    raise ValueError("evaluate 需要配置 script 参数")
                if "return" not in script:
                    script = f"return ({script})"
                result["result"] = tab.run_js(script)
            else:
                raise ValueError(
                    f"未知 flow_action: {action}（支持 start/navigate/click/input/"
                    "get_content/snapshot/screenshot/scroll/wait_for/evaluate/close）"
                )
            return result

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=cfg.action_timeout * 3
            )
        except Exception as e:
            state.add_error(node.node_key, f"浏览器动作「{action}」执行失败: {e}")
            return state

        # close 动作：执行成功后释放会话（quit 浏览器进程）
        if action == "close":
            await browser_session_manager.release(flow_id)

        # 截图走 media_file 标准链路（前端可预览）
        if "screenshot_bytes" in result:
            from app.utils.media_file import save_media_bytes

            media = await save_media_bytes(
                result.pop("screenshot_bytes"),
                "image/png",
                source_type="tool_generated",
                flow_id=flow_id,
                original_name="browser_screenshot.png",
            )
            result["screenshot"] = media

        # 结果 dict 整体写入输出变量 result（MCP 同款，下游用子路径取字段）
        output_names = self._resolve_output_var_names(node)
        state.set_node_variable(node.node_key, output_names[0], result)
        return state

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        """固定工具名，不允许同一 LLM 连接多个浏览器节点"""
        return False

    async def get_tool(self, node: FlowNode) -> Sequence[BaseTool]:
        """Agent 工具集：组装 BrowserToolKit（12 个工具，实现见 tools/browser_toolkit.py）"""
        cfg: BrowserNodeConfig = self._get_config(node)
        flow_id = get_flow_id()

        def _approval(tool_name: str, tool_args: dict, content: str = ""):
            return self._check_and_request_approval(
                tool_name=tool_name,
                tool_args=tool_args,
                cfg=cfg,
                content_for_pattern=content,
                node_key=node.node_key,
            )

        kit = BrowserToolKit(cfg=cfg, flow_id=flow_id, approval_cb=_approval)
        return list(await kit.build_tools())
