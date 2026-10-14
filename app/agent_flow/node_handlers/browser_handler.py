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
from typing import Any, Optional, Sequence

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
        description='连接模式: launch（默认，每流程独立实例）或 remote（高级，连接远程主机已开调试端口的浏览器）。仅 flow_action="start" 的节点生效',
    )
    browser_path: str = Field(
        "",
        description=(
            "浏览器可执行文件路径（仅 launch 模式）。"
            "留空=自动探测（注册表→常见安装路径→PATH 搜索 Chrome/Edge）；配置则直接启动。"
            '仅 flow_action="start" 的节点生效'
        ),
    )
    user_data_path: str = Field(
        "",
        description=(
            "浏览器数据保存位置（仅 launch 模式，可选）。"
            "留空=使用临时目录（浏览器退出自动清理，登录态不保留）；"
            "配置则按流程派生子目录持久化，登录态/Cookie 跨运行保留。"
            '仅 flow_action="start" 的节点生效'
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
            "配置具体端口（如 9333）则固定使用，便于外部 DevTools/CDP 客户端接入调试。"
            '仅 flow_action="start" 的节点生效'
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
            "false=弹出真实浏览器窗口，适合调试观察自动化过程。"
            '仅 flow_action="start" 的节点生效'
        ),
    )
    window_size: str = Field(
        "800,600",
        description=(
            "浏览器窗口大小（宽,高），如 1920,1080。headless 模式下视口即此尺寸，"
            "决定截图分辨率；launch 启动参数 --window-size。"
            '仅 flow_action="start" 的节点生效'
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

    # 各 flow_action 的参数 schema（唯一事实源：前端经 GET /flow/browser-actions
    # 动态拉取本表渲染参数表单，勿在前端再维护副本）
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
            },
            {
                "name": "width",
                "type": "number",
                "description": "临时窗口宽度（像素），控制截图分辨率；不传=当前窗口宽",
                "required": False,
            },
            {
                "name": "height",
                "type": "number",
                "description": "临时窗口高度（像素），控制截图分辨率；不传=当前窗口高",
                "required": False,
            },
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
        "tabs": [
            {
                "name": "tab_action",
                "type": "string",
                "description": "标签页操作: new（新建并激活）/ list（列标签页）/ select（切换）/ close（关闭标签页）",
                "required": True,
            },
            {
                "name": "tab_id",
                "type": "string",
                "description": "目标标签页 id（CDP targetId 字符串，list 返回的 tab_id，生命周期内稳定）：select=切换目标，close=关闭目标（不传=活动标签页）",
                "required": False,
            },
            {
                "name": "index",
                "type": "number",
                "description": "（已废弃，改用 tab_id）旧版下标方式 select 时的标签页索引（最新在前，0 起）",
                "required": False,
            },
        ],
        "console_logs": [
            {
                "name": "level",
                "type": "string",
                "description": "按级别过滤: all（默认）/ error / warning / info / debug",
                "required": False,
            },
            {
                "name": "include_preserved",
                "type": "boolean",
                "description": "是否包含前几次导航的日志（默认 false 仅当前页面）",
                "required": False,
            },
            {
                "name": "limit",
                "type": "number",
                "description": "最多返回条数（默认 50）",
                "required": False,
            },
        ],
        "network": [
            {
                "name": "network_action",
                "type": "string",
                "description": "网络查看操作: list（列已捕获请求简表）/ get（按 reqid 查详情含响应体）/ start（覆盖监听过滤）/ stop（暂停收集）/ performance（资源时间线兜底）",
                "required": True,
            },
            {
                "name": "reqid",
                "type": "number",
                "description": "network_action=get 时的请求编号（来自 list 返回的 reqid）",
                "required": False,
            },
            {
                "name": "resource_types",
                "type": "string",
                "description": "list 时按资源类型过滤（逗号分隔如 XHR,Fetch，空=默认 Document/XHR/Fetch）；start 时覆盖监听过滤（空=全部）",
                "required": False,
            },
            {
                "name": "url_filter",
                "type": "string",
                "description": "list 时按 URL 子串过滤；start 时覆盖监听 URL 过滤（空=全部）",
                "required": False,
            },
            {
                "name": "include_preserved",
                "type": "boolean",
                "description": "list 时是否包含前几次导航的请求（默认 false 仅当前页面）",
                "required": False,
            },
            {
                "name": "limit",
                "type": "number",
                "description": "list 时最多返回条数（默认 50）",
                "required": False,
            },
        ],
    }

    # 各 flow_action 的中文 label（前端动作下拉展示文案，与参数表同源下发）
    FLOW_ACTION_LABELS: dict[str, str] = {
        "start": "开启浏览器 (start)",
        "navigate": "访问页面 (navigate)",
        "click": "点击元素 (click)",
        "input": "输入文本 (input)",
        "get_content": "获取页面文本 (get_content)",
        "snapshot": "元素快照 (snapshot)",
        "screenshot": "页面截图 (screenshot)",
        "scroll": "滚动页面 (scroll)",
        "wait_for": "等待文本出现 (wait_for)",
        "evaluate": "执行 JS (evaluate)",
        "tabs": "标签页管理 (tabs)",
        "console_logs": "控制台日志 (console_logs)",
        "network": "网络请求监听 (network)",
        "close": "关闭浏览器 (close)",
    }

    @classmethod
    def get_action_meta(cls) -> list[dict]:
        """合成动作元数据 [{action, label, params}]（纯静态，无 IO）

        Flow 模式前端配置面板的单一事实源：GET /flow/browser-actions 下发，
        前端渲染动作下拉 + 逐参数表单，与 MCP 节点 parameters 同构。
        """
        return [
            {
                "action": action,
                "label": cls.FLOW_ACTION_LABELS.get(action, action),
                "params": cls.FLOW_ACTION_PARAMS[action],
            }
            for action in cls.FLOW_ACTION_PARAMS
        ]

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
                "get_content/snapshot/screenshot/scroll/wait_for/evaluate/tabs/"
                "console_logs/network/close）",
            )
            return state

        # 1. 解析输入变量 → flow_args 深度插值（MCP tool_args 同款）
        context = self._resolve_input_variables(cfg.input_variables or [], state)
        args = self._resolver.resolve_config(cfg.flow_args or {}, state, context)
        args = args if isinstance(args, dict) else {}

        # 1.5 browser_path/user_data_path 同样支持 {{变量}} 插值（如浏览器路径
        #     / 登录态目录来自上游节点输出）。对副本插值，不污染原配置对象；
        #     连接配置仅 start 节点生效，插值也只对 start 有意义
        resolved_cfg = cfg
        raw_path = (cfg.browser_path or "").strip()
        raw_data_dir = (cfg.user_data_path or "").strip()
        if action == "start" and ("{{" in raw_path or "{{" in raw_data_dir):
            resolved_cfg = cfg.model_copy(
                update={
                    "browser_path": self._resolver.render_template(
                        raw_path, state, context
                    ),
                    "user_data_path": self._resolver.render_template(
                        raw_data_dir, state, context
                    ),
                }
            )

        def arg(name: str, default: Any = "") -> Any:
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

        # 会话语义：连接配置（connect_mode/browser_path/user_data_path/
        # headless/debug_port）仅 start 动作节点生效——start 是唯一建会话
        # 入口（get_or_create）；其他动作只复用已有会话（reuse），缺会话
        # 报错引导加 start 节点，不再隐式首建（避免"谁先跑谁配置生效"的
        # 漂移歧义）。close 无会话时幂等跳过。
        session = None
        try:
            if action == "close":
                session = browser_session_manager.get_existing(flow_id)
                if session is None:
                    result = {"note": "浏览器已关闭（无活动会话）"}
                    output_names = self._resolve_output_var_names(node)
                    state.set_node_variable(node.node_key, output_names[0], result)
                    return state
            elif action == "start":
                session = await browser_session_manager.get_or_create(
                    resolved_cfg, flow_id
                )
            else:
                session = browser_session_manager.reuse(flow_id)
                if session is None:
                    state.add_error(
                        node.node_key,
                        '浏览器未启动：请在流程中先添加 flow_action="start" 的浏览器节点'
                        "（浏览器连接配置仅在 start 节点生效）",
                    )
                    return state
        except Exception as e:
            state.add_error(node.node_key, f"浏览器启动/连接失败: {e}")
            return state

        def _run() -> dict:
            if action == "start":
                return {
                    "note": "浏览器已开启",
                    "url": session.active_tab.url if session.active_tab else "",
                }
            if action == "close":
                return {"note": "浏览器已关闭"}
            if session is None:
                raise RuntimeError("浏览器实例没有初始化")
            tab = session.active_tab or session.browser.latest_tab
            session.active_tab = tab
            result: dict = {"url": tab.url, "title": tab.title}

            if action == "navigate":
                url = str(arg("url", "")).strip()
                if not url:
                    raise ValueError("navigate 需要配置 url 参数")
                # 宽容处理：裸域名（www.baidu.com）自动补 https://，否则
                # DrissionPage 4.x 会直接报「无效的url」拒绝执行
                if "://" not in url:
                    url = f"https://{url}"
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
                # width/height 临时视口覆盖控制截图分辨率（截完恢复，与 Agent 工具同语义）
                _w = arg("width", None)
                _h = arg("height", None)
                old_metrics = None
                if _w or _h:
                    vp = tab.rect.viewport_size
                    old_metrics = (vp[0], vp[1])
                    tab.run_cdp(
                        "Emulation.setDeviceMetricsOverride",
                        width=int(_w or old_metrics[0]),
                        height=int(_h or old_metrics[1]),
                        deviceScaleFactor=1,
                        mobile=False,
                    )
                try:
                    result["screenshot_bytes"] = tab.get_screenshot(
                        as_bytes="png", full_page=bool(arg("full_page", False))
                    )
                finally:
                    if old_metrics:
                        tab.run_cdp(
                            "Emulation.setDeviceMetricsOverride",
                            width=int(old_metrics[0]),
                            height=int(old_metrics[1]),
                            deviceScaleFactor=1,
                            mobile=False,
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
            elif action == "tabs":
                # 标签页管理（语义对齐 toolkit.browser_tabs）：new/list/select/close
                # tab_id = CDP targetId 字符串（标签页生命周期内稳定，关其他页不漂移）
                # new/select 返回 tab_id 写入输出变量，下游节点可 {{nodes.key.result.tab_id}} 插值
                tab_action = str(arg("tab_action", "list")).strip()
                browser = session.browser

                def _current_tab_id():
                    tab = session.active_tab
                    if tab is None:
                        return None
                    try:
                        return tab.tab_id
                    except Exception:
                        return None

                def _find_by_ref(ref):
                    """按 targetId 查找标签页；纯数字兼容旧下标（最新在前）"""
                    if ref is None or str(ref).strip() == "":
                        return None
                    tabs_now = browser.get_tabs()
                    for t in tabs_now:
                        try:
                            if t.tab_id == ref or str(t.tab_id) == str(ref):
                                return t
                        except Exception:
                            continue
                    s = str(ref).strip()
                    if s.isdigit():
                        idx = int(s)
                        if 0 <= idx < len(tabs_now):
                            return tabs_now[idx]
                    return None

                if tab_action == "new":
                    new_tab = browser.new_tab()
                    session.active_tab = new_tab
                    result.update(
                        tab_action="new",
                        url=new_tab.url,
                        tab_id=_current_tab_id(),
                        note="新标签页已创建并激活",
                    )
                elif tab_action == "select":
                    sel_ref = arg("tab_id", None)
                    target = _find_by_ref(sel_ref)
                    if target is None:
                        # 兼容旧 index 下标用法
                        tabs_now = browser.get_tabs()
                        idx = int(arg("index", 0))
                        if not 0 <= idx < len(tabs_now):
                            raise ValueError(
                                f"tab_id={sel_ref} 不存在（可用 list 查看当前标签页）"
                            )
                        target = tabs_now[idx]
                    session.active_tab = target
                    result.update(
                        tab_action="select",
                        tab_id=_current_tab_id(),
                        url=target.url,
                        note=(
                            "已切换标签页；元素定位符只对切换前页面有效，"
                            "建议后续接 snapshot 重新获取"
                        ),
                    )
                elif tab_action == "list":
                    result.update(
                        tab_action="list",
                        tabs=[
                            {"tab_id": t.tab_id, "url": t.url, "title": t.title}
                            for t in browser.get_tabs()
                        ],
                    )
                elif tab_action == "close":
                    # 传 tab_id（targetId）关指定标签页；不传关活动标签页。
                    # 注意关掉活动页自身后 active_tab 成死对象
                    # （后续 CDP 调用抛 PageDisconnectedError），需同步置空
                    close_ref = arg("tab_id", None)
                    if close_ref is not None and str(close_ref).strip() != "":
                        closing = _find_by_ref(close_ref)
                        if closing is None:
                            raise ValueError(
                                f"tab_id={close_ref} 不存在（可用 list 查看当前标签页）"
                            )
                        closing.close()
                        if session.active_tab is closing:
                            session.active_tab = None
                        result.update(
                            tab_action="close",
                            closed_tab_id=str(close_ref),
                            note="标签页已关闭",
                        )
                    else:
                        session.active_tab.close()
                        session.active_tab = None
                        result.update(tab_action="close", note="活动标签页已关闭")
                else:
                    raise ValueError(
                        f"未知 tab_action: {tab_action}（支持 new/list/select/close）"
                    )
            elif action == "console_logs":
                # 控制台日志：drain 进分段缓冲后按级别过滤（Agent 工具同语义，
                # 常驻收集，默认仅当前导航段）
                session.drain_console(tab)
                lv = str(arg("level", "all")).strip().lower()
                items = session.collected_console(bool(arg("include_preserved", False)))
                if lv and lv != "all":
                    items = [m for m in items if (m["level"] or "").lower() == lv]
                items = items[-max(1, int(arg("limit", 50))) :]
                result.update(
                    logs=items,
                    total=len(items),
                    note=f"共返回 {len(items)} 条（level={lv}）",
                )
            elif action == "network":
                # 网络查看：list/get/start/stop/performance（Agent 工具同语义，
                # 建会话即常驻收集默认 Document/XHR/Fetch，跨导航持续）
                from app.agent_flow.tools.browser_toolkit import (
                    _NETWORK_BODY_MAX,
                    _PERFORMANCE_JS,
                    _packet_summary,
                )

                network_action = str(arg("network_action", "list")).strip()
                if network_action == "get":
                    packet = session.find_network_packet(int(arg("reqid", 0) or 0))
                    if packet is None:
                        raise ValueError(
                            f"reqid={arg('reqid', 0)} 不存在：先用 network_action=list 获取有效编号"
                        )
                    detail = dict(_packet_summary(packet))
                    try:
                        if packet.is_failed:
                            detail["fail_reason"] = getattr(
                                packet.fail_info, "errorText", None
                            )
                        else:
                            body = packet.response.raw_body
                            if body is None:
                                detail["body"] = "<not available anymore>"
                            elif not isinstance(body, str):
                                detail["body"] = "<binary data>"
                            elif len(body) > _NETWORK_BODY_MAX:
                                detail["body"] = (
                                    body[:_NETWORK_BODY_MAX] + "... <truncated>"
                                )
                            else:
                                detail["body"] = body
                    except Exception as e:
                        detail["body"] = f"<parse error: {e}>"
                    result.update(network_action="get", request=detail)
                elif network_action == "start":
                    # 覆盖监听过滤（显式全量传参：DP start() 空参数不重置过滤）
                    url_filter = str(arg("url_filter", "")).strip()
                    rt = str(arg("resource_types", "")).strip()
                    tab.listen.start(
                        targets=url_filter or True,
                        method=str(arg("method", "")).strip().upper() or True,
                        res_type=(
                            [t.strip().upper() for t in rt.split(",") if t.strip()]
                            or True
                        ),
                    )
                    result.update(
                        network_action="start",
                        note=f"监听过滤已覆盖: url={url_filter or '全部'} type={rt or '全部'}",
                    )
                elif network_action == "stop":
                    tab.listen.stop()
                    result.update(
                        network_action="stop",
                        note="网络收集已暂停（缓冲存量仍可 list/get；恢复用 start）",
                    )
                elif network_action == "performance":
                    raw = tab.run_js(_PERFORMANCE_JS)
                    entries = json.loads(raw) if isinstance(raw, str) else (raw or [])
                    result.update(
                        network_action="performance",
                        resources=entries,
                        total=len(entries),
                        note="资源时间线（全量 URL+耗时，无状态码；仅当前文档）",
                    )
                elif network_action == "list":
                    session.drain_network(tab)
                    items = session.collected_network(
                        bool(arg("include_preserved", False))
                    )
                    rt = {
                        t.strip().upper()
                        for t in str(arg("resource_types", "")).split(",")
                        if t.strip()
                    }
                    if rt:
                        items = [
                            it
                            for it in items
                            if (it["summary"].get("resource_type") or "").upper() in rt
                        ]
                    uf = str(arg("url_filter", "")).strip()
                    if uf:
                        items = [it for it in items if uf in it["summary"]["url"]]
                    base = session.collected_network(
                        bool(arg("include_preserved", False))
                    )
                    offset_map = {id(it["packet"]): i + 1 for i, it in enumerate(base)}
                    out = []
                    for it in items[-max(1, int(arg("limit", 50))) :]:
                        out.append(
                            it["summary"]
                            | {"reqid": offset_map.get(id(it["packet"]), 0)}
                        )
                    result.update(
                        network_action="list",
                        requests=out,
                        total=len(items),
                        note=f"共 {len(items)} 条；network_action=get + reqid 查详情（含响应体）",
                    )
                else:
                    raise ValueError(
                        f"未知 network_action: {network_action}"
                        "（支持 list/get/start/stop/performance）"
                    )
            else:
                raise ValueError(
                    f"未知 flow_action: {action}（支持 start/navigate/click/input/"
                    "get_content/snapshot/screenshot/scroll/wait_for/evaluate/tabs/"
                    "console_logs/network/close）"
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
        """Agent 工具集：组装 BrowserToolKit（14 个工具，实现见 tools/browser_toolkit.py）"""
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
