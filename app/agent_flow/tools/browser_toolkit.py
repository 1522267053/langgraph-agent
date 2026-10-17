"""
浏览器操作工具集（BrowserToolKit，DrissionPage / CDP，chrome-devtools snapshot 范式）

从 browser_handler 抽出：会话管理器（BrowserSessionManager，按 flow_id 缓存）+
14 个 Agent 工具（browser_start/snapshot/navigate/click/input/screenshot/
get_content/evaluate/tabs/scroll/wait_for/console_logs/network/close）。

工作循环 = browser_snapshot（提取可交互元素+ref 编号）→ click/input(按 ref) →
snapshot 确认。

线程模型：DrissionPage 为同步 API，统一经 asyncio.to_thread 包装（paramiko
同款先例），禁止直接在事件循环调用。

依赖方向：tools/* → app/utils/*；审批通过注入回调（approval_cb），本模块不
import node_handlers。browser_handler 保留 Flow 编排执行路径（execute）。
"""

import asyncio
import json
import logging
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Sequence

from DrissionPage import Chromium
from DrissionPage.items import ChromiumElement, MixTab
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent_flow.execution_context import get_execution_context
from app.agent_flow.tools.base_abs_tool import BaseToolKit
from app.agent_flow.tool_output_truncate import smart_truncate_output
from app.config.build_utils import get_temp_dir
if TYPE_CHECKING:
    from app.agent_flow.node_handlers.browser_handler import BrowserNodeConfig


logger = logging.getLogger(__name__)

# 审批回调签名：(tool_name, tool_args, content_for_pattern) -> None/"approved"/"rejected"/"timeout"
ApprovalCallback = Callable[[str, dict, Optional[str]], Awaitable[Optional[str]]]

# 单元素文本最大长度
_SNAPSHOT_TEXT_MAX = 60
# 浏览器连接/操作默认超时（秒）
_BROWSER_CONNECT_TIMEOUT = 30
# 截图超时（秒）：全页截图在重页面上会挂死 CDP 截图通道，限时返回引导
_SCREENSHOT_TIMEOUT = 30
# 元素查找超时（秒）：ele() 未命中时等满默认 10s 才返回 NoneElement，
# click/input 的定位符分支显式缩短，避免"点击很久才报未找到"
_ELE_FIND_TIMEOUT = 3
# 浏览器空闲回收（秒）：超过该时长无任何工具调用的会话被后台协程自动 quit，
# 防 Chromium 进程泄漏（Agent 忘调 close 时兜底）
_BROWSER_IDLE_TIMEOUT = 600
# 调试收集（console/network）按导航分段常驻缓冲，保留最近 N 次导航
# （对齐 chrome-devtools-mcp PageCollector.maxNavigationSaved=3 语义）
_NAV_SEGMENTS_MAX = 3
# 单段 console/网络条数上限（防单页请求爆炸撑爆内存）
_CONSOLE_SEG_MAX = 200
_NETWORK_SEG_MAX = 200
# 建会话自动网络监听的默认资源类型：主文档+接口（排除图片/字体/CSS 降开销，
# DP Listener 对命中包自动拉响应体）；显式 start 可覆盖为任意范围
_NETWORK_DEFAULT_RES_TYPES = ("DOCUMENT", "XHR", "FETCH")
# 网络详情响应体内联上限（对齐 chrome-devtools-mcp BODY_CONTEXT_SIZE_LIMIT）
_NETWORK_BODY_MAX = 10000


def _normalize_locator(sel: str) -> str:
    """裸定位器归一化：像 CSS/XPath 的自动加 DrissionPage 前缀。

    DrissionPage ele() 对裸字符串按自有语法解释：纯字符串=文本匹配、
    # 开头=id 定位，导致 get_content/click/input 传标准 CSS 选择器
    （如 h1、#b_results li.b_algo h2）静默失配。已带前缀或明显的
    文本/id 定位器则原样返回。
    """
    s = sel.strip()
    if not s:
        return s
    # 已是 DrissionPage 显式语法（css:/xpath:/text:/t:/@/#/tag: 等）→ 不动
    if ":" in s:
        head = s.split(":", 1)[0]
        if head in ("css", "xpath", "text", "t", "tag", "text:", "@") or head in (
            "id",
            "class",
        ):
            return s
    # XPath：/ 或 // 或 ( 开头
    if s.startswith(("/", "(")):
        return f"xpath:{s}"
    # CSS：.class、tag[attr]、tag.class、tag#id、*、伪类等特征
    if s.startswith((".", "*")) or ("[" in s and "]" in s) or " " in s:
        return f"css:{s}"
    # tag#id / tag.class（无空格单标签）
    if "#" in s or "." in s:
        return f"css:{s}"
    # 纯 ASCII 单词（h1、button、li）：DP 会按文本匹配，语义歧义 → 按 CSS 标签处理。
    # 注意 isidentifier() 对中文也返回 True，须限定 ASCII，否则破坏文本匹配
    if s.isascii() and s.isidentifier():
        return f"css:{s}"
    return s


def _packet_summary(p) -> dict:
    """DataPacket → 轻量摘要（不碰响应体，get 详情时按需取）。

    状态三态对齐 chrome-devtools-mcp：有响应=状态码 / 失败=errorText /
    都没有=pending。任何字段解析失败都降级占位，不让单包异常毁掉整批收集。
    """
    item = {"url": "", "method": "", "status": "pending", "failed": False}
    try:
        item["url"] = (p.url or "")[:500]
        item["method"] = p.method or ""
        if p.is_failed:
            item["failed"] = True
            item["status"] = getattr(p.fail_info, "errorText", None) or "failed"
        else:
            item["status"] = p.response.status
            item["resource_type"] = p.resourceType
    except Exception as e:
        item["status"] = f"pending({e.__class__.__name__})"
    return item


# performance 资源时间线 JS（action=performance 轻量兜底：全量 URL+耗时，
# 无状态码；只反映当前文档，导航后清空）。必须用 return 语句形态。
_PERFORMANCE_JS = """
var entries = performance.getEntriesByType('resource');
var out = [];
for (var i = 0; i < entries.length; i++) {
    var e = entries[i];
    out.push({
        url: String(e.name).slice(0, 500),
        type: e.initiatorType,
        duration_ms: Math.round(e.duration),
        size_kb: e.transferSize ? Math.round(e.transferSize / 1024) : 0
    });
}
return JSON.stringify(out);
"""


# snapshot 提取 JS：必须用 return 语句形态（DrissionPage 4.x run_js 只认 return）
_SNAPSHOT_JS = f"""
var out = [];
var els = document.querySelectorAll('a, button, input, select, textarea, [contenteditable="true"], [role="button"], [onclick]');
for (var i = 0; i < els.length; i++) {{
    var el = els[i];
    var r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) continue;
    var tag = el.tagName.toLowerCase();
    var text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim();
    if (text.length > {_SNAPSHOT_TEXT_MAX}) text = text.slice(0, {_SNAPSHOT_TEXT_MAX}) + '…';
    var refNum = out.length + 1;
    var item = {{ref: refNum, tag: tag, text: text}};
    el.setAttribute('data-agent-ref', String(refNum));
    if (tag === 'input') {{
        item.type = el.type || 'text';
        item.value = (el.value || '').slice(0, 100);
    }}
    if (tag === 'a' && el.href) item.href = el.href.slice(0, 120);
    out.push(item);
}}
return JSON.stringify(out);
"""

# 层级文本快照 JS（Agent 工具模式，对齐 chrome-devtools take_snapshot 的 a11y 树风格）：
# 递归遍历 body，输出「缩进 + ref=N 角色 "文本" 附加属性」行；每个可交互元素
# 同步锚定 data-agent-ref（快照内自增编号，与 click/input 的属性查找同源）。
# 必须用 return 语句形态（DrissionPage 4.x run_js 只认 return）。
_SNAPSHOT_TEXT_JS = f"""
var lines = [];
var refCounter = 0;

var ROLE_MAP = {{
  A: 'link', BUTTON: 'button', SELECT: 'combobox', TEXTAREA: 'textbox',
  LABEL: 'LabelText', IMG: 'image', H1: 'heading', H2: 'heading',
  H3: 'heading', H4: 'heading', H5: 'heading', H6: 'heading',
  NAV: 'navigation', HEADER: 'banner', FOOTER: 'contentinfo',
  MAIN: 'main', ASIDE: 'complementary', FORM: 'form', TABLE: 'table',
  OPTION: 'option', UL: 'list', OL: 'list', LI: 'listitem'
}};

function roleOf(el) {{
  var role = el.getAttribute('role');
  if (role) return role;
  var tag = el.tagName.toUpperCase();
  if (ROLE_MAP[tag]) return ROLE_MAP[tag];
  if (tag === 'INPUT') {{
    var t = (el.type || 'text').toLowerCase();
    if (t === 'checkbox') return 'checkbox';
    if (t === 'radio') return 'radio';
    if (t === 'search') return 'searchbox';
    if (t === 'submit' || t === 'button') return 'button';
    return 'textbox';
  }}
  return 'generic';
}}

function textOf(el) {{
  var t = (el.getAttribute('aria-label') || el.innerText || el.value
    || el.placeholder || '').trim().replace(/\\s+/g, ' ');
  if (t.length > {_SNAPSHOT_TEXT_MAX}) t = t.slice(0, {_SNAPSHOT_TEXT_MAX}) + '…';
  return t;
}}

function walk(el, depth) {{
  if (depth > 30) return;
  if (el.nodeType !== 1) return;
  var tag = el.tagName;
  // 隐藏元素整枝跳过
  var st = window.getComputedStyle(el);
  if (st.display === 'none' || st.visibility === 'hidden') return;
  var r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return;
  if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'SVG') return;

  var role = roleOf(el);
  var text = textOf(el);
  var interactive = (tag === 'A' || tag === 'BUTTON' || tag === 'SELECT'
    || tag === 'TEXTAREA' || tag === 'INPUT'
    || el.getAttribute('contenteditable') === 'true'
    || el.getAttribute('role') === 'button' || el.hasAttribute('onclick')
    || el.isContentEditable);

  var isLeaf = !el.firstElementChild;
  var meaningful = interactive || isLeaf || role !== 'generic';

  if (meaningful) {{
    var refNum = null;
    if (interactive) {{
      refCounter++;
      refNum = refCounter;
      el.setAttribute('data-agent-ref', String(refNum));
    }}
    var indent = '';
    for (var i = 0; i < depth; i++) indent += '  ';
    var line = indent + (refNum !== null ? ('ref=' + refNum + ' ') : '') + role;
    if (text) line += ' "' + text + '"';
    if (tag === 'A' && el.href) line += ' url="' + el.href.slice(0, 120) + '"';
    if (tag === 'INPUT' && role !== 'button') {{
      line += ' type=' + (el.type || 'text');
      if (el.value) line += ' value="' + String(el.value).slice(0, 80) + '"';
    }}
    if (el.disabled) line += ' disabled';
    if (el.required) line += ' required';
    if (el.getAttribute('aria-expanded') === 'true') line += ' expanded';
    if (el.getAttribute('aria-selected') === 'true') line += ' selected';
    if (tag.match(/^H[1-6]$/)) line += ' level=' + tag.slice(1);
    lines.push(line);
    if (refNum !== null) return;  // 可交互元素不递归子级（文本已带出）
  }}

  var children = el.children;
  for (var j = 0; j < children.length; j++) {{
    walk(children[j], meaningful ? depth + 1 : depth);
  }}
}}

walk(document.body, 0);
return lines.join('\\n');
"""

# Flow execute 模式的 JSON 快照导出（browser_handler 的 snapshot 动作使用）
SNAPSHOT_TEXT_JS = _SNAPSHOT_TEXT_JS
SNAPSHOT_JSON_JS = _SNAPSHOT_JS

# ---- a11y 树快照（抄 chrome-devtools-mcp：CDP Accessibility.getFullAXTree +
# SnapshotFormatter 同款序列化规则）----
# 参考：ChromeDevTools/chrome-devtools-mcp src/TextSnapshot.ts / SnapshotFormatter.ts

# 布尔属性名映射（SnapshotFormatter.booleanPropertyMap 同款）
_AX_BOOLEAN_PROP_MAP = {
    "disabled": "disableable",
    "expanded": "expandable",
    "focused": "focusable",
    "selected": "selectable",
}

# 序列化时排除的节点字段（SnapshotFormatter.excludedAttributes 同款）
_AX_EXCLUDED_ATTRS = {
    "id",
    "role",
    "name",
    "elementHandle",
    "children",
    "backendNodeId",
    "loaderId",
    # CDP 原始字段（chrome-devtools-mcp 经 Puppeteer 序列化后不含这些）
    "nodeId",
    "parentId",
    "childIds",
    "chromeRole",
    "ignored",
    "ignoredReasons",
    "frameId",
}


def _ax_val(field: Optional[dict]) -> object:
    """取 CDP AX 值字段（{"type": ..., "value": ...}）的 value"""
    if not isinstance(field, dict):
        return None
    return field.get("value")


def _build_ax_snapshot(tab, max_nodes: int = 400) -> str:
    """从 CDP a11y 树生成层级文本快照（SnapshotFormatter.toString 同款格式）。

    行格式：`uid=N 角色 "名称" attr="value"`，缩进 2 空格/层；
    role=none 输出 ignored；布尔属性按 booleanPropertyMap 映射；true 只出键名。
    可交互节点（link/button/textbox/combobox/checkbox/radio/searchbox）同步
    锚定 data-agent-ref（与 click/input 的属性查找同源），uid 即 ref 编号。

    Args:
        tab: DrissionPage ChromiumTab（用 run_cdp 调 Accessibility domain）
        max_nodes: 序列化节点上限（超出截断，防超大页面撑爆上下文）
    """
    resp = tab.run_cdp("Accessibility.getFullAXTree")
    nodes = resp.get("nodes", []) or []
    by_node_id = {n.get("nodeId"): n for n in nodes if n.get("nodeId")}

    # 组树：root = 无 parentId 或 parentId 不在表中的节点（RootWebArea）
    children_map: dict = {}
    roots: list[dict] = []
    for n in nodes:
        pid = n.get("parentId")
        if pid and pid in by_node_id:
            children_map.setdefault(pid, []).append(n)
        else:
            roots.append(n)

    lines: list[str] = []
    ref_counter = 0
    ref_to_backend: dict[int, int] = {}

    # 可交互角色集合（这些节点锚定 data-agent-ref 供 click/input 引用）
    INTERACTIVE_ROLES = {
        "link",
        "button",
        "textbox",
        "searchbox",
        "combobox",
        "checkbox",
        "radio",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "slider",
        "switch",
        "tab",
    }

    def serialize(node: dict, depth: int) -> None:
        nonlocal ref_counter
        if len(lines) >= max_nodes:
            return
        role = _ax_val(node.get("role")) or "generic"
        name = _ax_val(node.get("name"))
        ignored = bool(node.get("ignored"))

        if not ignored:
            uid = None
            if role in INTERACTIVE_ROLES:
                backend_id = node.get("backendDOMNodeId")
                if backend_id is not None:
                    ref_counter += 1
                    uid = ref_counter
                    ref_to_backend[uid] = backend_id

            indent = "  " * depth
            parts = [
                f"uid={uid}" if uid is not None else None,
                "ignored" if role == "none" else role,
            ]
            if name:
                text = str(name).strip()
                if len(text) > _SNAPSHOT_TEXT_MAX:
                    text = text[:_SNAPSHOT_TEXT_MAX] + "…"
                parts.append(f'"{text}"')

            # 属性序列化（SnapshotFormatter.#getAttributes 同款规则）
            props: dict = {}
            for p in node.get("properties", []) or []:
                pname = p.get("name")
                pval = _ax_val(p.get("value"))
                if pname and pname not in _AX_EXCLUDED_ATTRS:
                    props[pname] = pval
            for attr in sorted(props):
                mapped = _AX_BOOLEAN_PROP_MAP.get(attr)
                val = props[attr]
                if mapped and val:
                    parts.append(mapped)
                if val is True:
                    parts.append(attr)
                elif isinstance(val, (str, int, float)):
                    parts.append(f'{attr}="{val}"')

            # link 的 url 在 value 字段（CDP AXLink 的 value 即 href）
            if role == "link":
                href = _ax_val(node.get("value"))
                if href:
                    parts.append(f'url="{str(href)[:120]}"')
            # 输入框当前值
            if role in ("textbox", "searchbox", "combobox"):
                val = _ax_val(node.get("value"))
                if val:
                    parts.append(f'value="{str(val)[:80]}"')

            lines.append(indent + " ".join(p for p in parts if p))

        for child in children_map.get(node.get("nodeId"), []):
            serialize(child, depth + 1 if not ignored else depth)

    for root in roots:
        serialize(root, 0)

    snapshot_text = "\n".join(lines)

    # data-agent-ref 锚定：CDP 一次拿全量 DOM 节点建立 backendId→nodeId 映射，
    # 再逐 uid DOM.setAttributeValue 注入（JS 无法按 nodeId 定位元素，只能走 CDP）
    if ref_to_backend:
        try:
            all_nodes = tab.run_cdp(
                "DOM.getFlattenedDocument", depth=-1, pierce=True
            ).get("nodes", [])
            backend_to_node_id = {
                n["backendNodeId"]: n["nodeId"]
                for n in all_nodes
                if n.get("backendNodeId") and n.get("nodeId")
            }
            for uid, backend_id in ref_to_backend.items():
                node_id = backend_to_node_id.get(backend_id)
                if node_id is None:
                    continue
                try:
                    tab.run_cdp(
                        "DOM.setAttributeValue",
                        nodeId=node_id,
                        name="data-agent-ref",
                        value=str(uid),
                    )
                except Exception:
                    continue
        except Exception as e:
            logger.warning("data-agent-ref 锚定失败（快照仍可用）: %s", e)

    return snapshot_text


# ---- 工具输入 schema（StructuredTool 必需 args_schema；无参工具用空模型） ----

# ---- 描述单源（唯一维护点；tool_info 与 build_tools 共用）----
# name 与 build_tools 注册的 StructuredTool 一一对应，description 为完整
# 描述（延迟域索引存完整描述，保 search 召回；展示端截断）
BROWSER_TOOL_DESCRIPTIONS: dict[str, str] = {
    "browser_start": (
        "开启浏览器（每流程独立实例，幂等：已开启则直接复用）。"
        "操作浏览器的第一步；不调用直接用其他工具也会自动启动。"
        "本机未装 Chrome 或自动探测失败时，可传 browser_path 指定"
        "浏览器可执行文件路径（如 Edge 的 msedge.exe）"
    ),
    "browser_snapshot": (
        "Take a text snapshot of the current page based on the a11y tree. "
        "The snapshot lists page elements along with a unique identifier "
        "(uid). Always use the latest snapshot. Prefer taking a snapshot "
        "over taking a screenshot. 用 uid 引用元素进行 browser_click/browser_input。"
    ),
    "browser_navigate": "导航到 URL（action: goto/back/forward/reload）",
    "browser_click": "点击页面元素。ref 为 browser_snapshot 返回的编号或 CSS/文本定位符",
    "browser_input": (
        "向输入框输入文本。ref 为 snapshot 编号或定位符；clear=True 先清空。"
        "写操作同流程串行执行；输入后如需校验，请等本工具返回再单独调"
        "browser_evaluate（并行发 evaluate 读到的是输入前的旧值）"
    ),
    "browser_screenshot": (
        "截取当前页面（full_page=True 全页），返回可预览的图片链接。"
        "width/height 可临时调整窗口控制截图分辨率（如 1920x1080 高清截图），"
        "仅本次截图生效。注意：全页截图在内容较重的页面经常超时，"
        "优先用默认视口截图，需要长页面时用 browser_scroll 滚动后分段截图"
    ),
    "browser_get_content": "获取页面文本内容（selector 可选，留空取整个 body）",
    "browser_evaluate": "在页面执行 JavaScript（逃生舱）。脚本用 return 返回值",
    "browser_tabs": (
        "标签页管理（action: list/select/new/close，tab_id 为 CDP targetId 字符串，"
        "生命周期内稳定；close/select 传 tab_id 指定目标）"
    ),
    "browser_scroll": "滚动页面（direction: up/down，amount 像素）",
    "browser_wait_for": (
        "等待包含指定文本的元素出现并显示（包含匹配，无需全文本一致；"
        "超时返回 found=false）"
    ),
    "browser_close": "关闭浏览器（临时数据自动清理；配置了数据目录则登录态保留）",
    "browser_console_logs": (
        "查看页面控制台（console）日志：error/warning/info 等，"
        "浏览器启动后自动全程捕获（含页面加载期报错）。"
        "action: list（默认，仅当前导航）/ clear；level 过滤级别，"
        "include_preserved=true 回看最近 3 次导航"
    ),
    "browser_network": (
        "查看页面网络请求（Network 面板）。浏览器启动后自动捕获"
        "（默认 Document/XHR/Fetch，跨导航持续）。action: list"
        "（简表，默认当前导航，含 reqid）/ get（reqid 查详情含响应体）/ "
        "start（覆盖过滤条件）/ stop（暂停）/ performance（资源时间线"
        "全量 URL+耗时兜底）。排查接口报错：list 找 reqid → get 看响应体"
    ),
}


class _NoArgsInput(BaseModel):
    """无参工具的空 schema（browser_close/snapshot）"""


class StartInput(BaseModel):
    """browser_start 输入"""

    browser_path: Optional[str] = Field(
        None,
        description=(
            "浏览器可执行文件路径（如 C:\\Program Files (x86)\\Microsoft\\Edge\\"
            "Application\\msedge.exe）。不传=节点配置或自动探测 Chrome/Edge；"
            "仅在首次启动浏览器时生效，会话已存在时忽略"
        ),
    )


class NavigateInput(BaseModel):
    """browser_navigate 输入"""

    url: str = Field("", description="要访问的 URL（action=goto 时必填）")
    action: str = Field(
        "goto", description="导航动作: goto（默认）/ back / forward / reload"
    )
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


class RefInput(BaseModel):
    """browser_click 输入"""

    ref: str = Field(
        ...,
        description=(
            "browser_snapshot 返回的元素 uid 编号（如 1 或 uid=1），或定位符：CSS 选择器须加 css: 前缀"
            "（如 css:button.submit），文本匹配用 text:xxx；裸写 h1/#id 会失配"
        ),
    )
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


class InputInput(BaseModel):
    """browser_input 输入"""

    ref: str = Field(
        ...,
        description=(
            "browser_snapshot 返回的输入框 uid 编号（如 1 或 uid=1），或定位符：CSS 选择器须加 css: 前缀"
            "（如 css:#kw），文本匹配用 text:xxx；裸写 h1/#id 会失配"
        ),
    )
    text: str = Field(..., description="要输入的文本")
    clear: bool = Field(True, description="输入前是否清空已有内容，默认清空")
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


class ScreenshotInput(BaseModel):
    """browser_screenshot 输入"""

    full_page: bool = Field(False, description="是否截取整个页面（默认只截可视区域）")
    width: Optional[int] = Field(
        None, description="临时窗口宽度（像素），控制截图分辨率；不传=当前窗口宽"
    )
    height: Optional[int] = Field(
        None, description="临时窗口高度（像素），控制截图分辨率；不传=当前窗口高"
    )
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


class GetContentInput(BaseModel):
    """browser_get_content 输入"""

    selector: str = Field(
        "",
        description=(
            "可选元素定位器，留空取整个 body 文本。规则：CSS 选择器必须加 css: 前缀"
            "（如 css:h1、css:#b_results li.b_algo h2）；按可见文本匹配用 text:xxx；"
            "裸写 h1/#id 会被误当文本/id 解释而失配"
        ),
    )
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


class EvaluateInput(BaseModel):
    """browser_evaluate 输入"""

    script: str = Field(..., description="要执行的 JavaScript，用 return 返回值")
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


class TabsInput(BaseModel):
    """browser_tabs 输入"""

    action: str = Field("list", description="动作: list（默认）/ select / new / close")
    tab_id: Optional[str] = Field(
        None,
        description=(
            "目标标签页 id（CDP targetId 字符串，list 返回的 tab_id）。"
            "close=关闭该标签页（不传关活动页）；select=切换到该标签页（替代旧 index）"
        ),
    )
    index: int = Field(
        0,
        description="（已废弃，改用 tab_id）旧版下标方式 select 时的标签页索引（最新在前）",
    )


class ScrollInput(BaseModel):
    """browser_scroll 输入"""

    direction: str = Field("down", description="滚动方向: down（默认）/ up")
    amount: int = Field(600, description="滚动像素距离，默认 600")
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


class WaitForInput(BaseModel):
    """browser_wait_for 输入"""

    text: str = Field(..., description="要等待出现的页面文本")
    timeout: int = Field(
        10,
        description=(
            "超时秒数（1~120，默认 10）。注意单位是秒不是毫秒——"
            "传 5 表示 5 秒；超过 120 会被钳制到 120"
        ),
    )
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


class ConsoleInput(BaseModel):
    """browser_console_logs 输入"""

    action: str = Field(
        "list", description="操作: list（返回捕获的控制台日志）/ clear（清空缓冲）"
    )
    level: str = Field(
        "all", description="按级别过滤: all（默认）/ error / warning / info / debug 等"
    )
    include_preserved: bool = Field(
        False,
        description="是否包含前几次导航的日志（默认 false 仅当前页面；true 回看最近 3 次导航）",
    )
    page_size: int = Field(50, description="每页条数（默认 50）")
    page_idx: int = Field(0, description="页码（0 起，默认第一页）")
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


class NetworkInput(BaseModel):
    """browser_network 输入"""

    action: str = Field(
        "list",
        description=(
            "操作: list（列已捕获请求简表）/ get（按 reqid 查单条详情，含响应体）/ "
            "start（覆盖监听过滤条件）/ stop（暂停收集）/ performance"
            "（读资源时间线：全量 URL+耗时，无状态码的轻量兜底）"
        ),
    )
    reqid: Optional[int] = Field(
        None,
        description="action=get 时的请求编号（来自 list 返回的 reqid，1 起）",
    )
    resource_types: str = Field(
        "",
        description=(
            "action=list 时按资源类型过滤（逗号分隔如 XHR,Fetch，空=默认 "
            "Document/XHR/Fetch）；action=start 时覆盖监听过滤（空=全部类型）"
        ),
    )
    url_filter: str = Field(
        "", description="action=list 时按 URL 子串过滤（空=不过滤）"
    )
    method: str = Field(
        "", description="action=list 时按 HTTP 方法过滤（如 GET/POST，空=全部）"
    )
    include_preserved: bool = Field(
        False,
        description="action=list 时是否包含前几次导航的请求（默认 false 仅当前页面；true 回看最近 3 次导航）",
    )
    page_size: int = Field(50, description="action=list 时每页条数（默认 50）")
    page_idx: int = Field(0, description="action=list 时页码（0 起）")
    tab_id: Optional[str] = Field(
        None,
        description="目标标签页 id（CDP targetId 字符串，browser_tabs/snapshot 返回的 tab_id，生命周期内稳定），不传=当前活动标签页",
    )


# ---- 会话管理器（按 flow_id 缓存，解决多流程并发互不干扰） ----


class BrowserSession:
    """单个流程的浏览器会话（launch 模式持有独立 Chromium 实例）"""

    def __init__(
        self,
        browser,
        port: Optional[int],
        data_dir: Optional[Path],
        persistent: bool,
        flow_id: int = 0,
        launch_cfg: Optional[dict] = None,
    ):
        self.browser = browser
        self.port = port
        self.data_dir = data_dir
        self.persistent = persistent  # True=用户配置的数据目录（quit 不删）；False=临时目录（quit 删）
        self.flow_id = flow_id
        # 首建会话的启动配置快照（headless/debug_port/browser_path/user_data_path）：
        # 同流程后续节点的同类配置不一致时告警（会话按 flow_id 复用，后到配置不生效）
        self.launch_cfg: dict = launch_cfg or {}
        self.active_tab: Optional[MixTab] = None
        # 空闲回收基准：每次工具调用刷新（monotonic，不受系统时钟跳变影响）
        self.last_used = time.monotonic()
        # 写操作互斥锁（同流程内 navigate/click/input/tabs/scroll 串行）：
        # CDP 底层对同一浏览器/标签页的写操作并发不安全（实测 input+evaluate
        # 竞态、截图通道卡死连带）。只读工具（snapshot/get_content/screenshot/
        # wait_for/evaluate）不加锁，保持可并行。
        self.write_lock = asyncio.Lock()
        # 调试收集缓冲（console/network 常驻，参考 chrome-devtools-mcp
        # PageCollector 的按导航分段语义）：[{url, items}]，最新段在前，
        # 最多 _NAV_SEGMENTS_MAX 段；drain 时惰性比对 tab.url 封段开新段
        # （DP 无公开导航事件，不挂私有 _driver 回调）。network 段 items
        # 存 {summary, packet}（packet 保留引用供 action=get 按需取详情）
        self.nav_segments: list[dict] = [{"url": "", "console": [], "network": []}]

    def _ensure_segment(self, tab) -> dict:
        """返回当前导航段；tab.url 与段 url 不一致时封段开新段（超限截断）"""
        try:
            cur_url = (tab.url or "")[:500]
        except Exception:
            cur_url = ""
        seg = self.nav_segments[0]
        if cur_url and seg["url"] and cur_url != seg["url"]:
            self.nav_segments.insert(0, {"url": cur_url, "console": [], "network": []})
            self.nav_segments[:] = self.nav_segments[:_NAV_SEGMENTS_MAX]
            seg = self.nav_segments[0]
        elif not seg["url"]:
            seg["url"] = cur_url
        return seg

    def drain_console(self, tab) -> list[dict]:
        """把 tab.console 队列已捕获日志排空进当前导航段（未监听则自动开启）

        返回本次新收集的条目。坑：DP console.messages 是 drain 语义
        （取出即清空队列），必须由本方法统一收进分段缓冲。
        """
        new_items: list[dict] = []
        try:
            if not tab.console.listening:
                tab.console.start()
            for m in tab.console.messages:
                new_items.append(
                    {
                        "level": m.level or "info",
                        "text": str(m.text)[:2000],
                        "source": m.source,
                        "url": (m.url or "")[:300],
                        "line": m.line,
                    }
                )
            if new_items:
                seg = self._ensure_segment(tab)
                seg["console"].extend(new_items)
                seg["console"][:] = seg["console"][-_CONSOLE_SEG_MAX:]
        except Exception as e:
            logger.warning("收集 console 日志失败: %s", e)
        return new_items

    def drain_network(self, tab, timeout: float = 0.3) -> list[dict]:
        """非阻塞排空 tab.listen 已捕获的网络包进当前导航段

        每包转轻量摘要 + 保留 DataPacket 引用（action=get 按需取详情，
        响应体不在此处内联）。未监听时自动按默认资源类型开启（常驻收集）。

        坑1：listen.steps() 无 timeout 是无限阻塞生成器，不能用于排空。
        坑2：wait(count=1) 命中时返回单个 DataPacket 而非列表（DP 源码
        `if count == 1: return get_nowait()`），须循环逐个取直到超时返回 False。
        """
        try:
            if not tab.listen.listening:
                tab.listen.start(res_type=list(_NETWORK_DEFAULT_RES_TYPES))
        except Exception as e:
            logger.warning("开启网络监听失败: %s", e)
            return []
        packets = []
        try:
            while True:
                r = tab.listen.wait(count=1, timeout=timeout, fit_count=False)
                if not r:
                    break
                if isinstance(r, list):
                    packets.extend(r)
                    break
                packets.append(r)
        except Exception as e:
            logger.warning("收集网络包失败: %s", e)
        new_items: list[dict] = []
        for p in packets:
            new_items.append({"summary": _packet_summary(p), "packet": p})
        if new_items:
            seg = self._ensure_segment(tab)
            seg["network"].extend(new_items)
            seg["network"][:] = seg["network"][-_NETWORK_SEG_MAX:]
        return new_items

    def collected_console(self, include_preserved: bool) -> list[dict]:
        """收集缓冲里的 console 条目（默认仅当前导航段）"""
        segs = self.nav_segments if include_preserved else self.nav_segments[:1]
        items: list[dict] = []
        for seg in reversed(segs):  # 旧段在前，时间序输出
            items.extend(seg["console"])
        return items

    def collected_network(self, include_preserved: bool) -> list[dict]:
        """收集缓冲里的网络条目（默认仅当前导航段），带全局递增 reqid"""
        segs = self.nav_segments if include_preserved else self.nav_segments[:1]
        items: list[dict] = []
        for seg in reversed(segs):  # 旧段在前，时间序输出
            items.extend(seg["network"])
        return items

    def find_network_packet(self, reqid: int):
        """按 reqid（collected_network 的 1-based 序号）找 DataPacket"""
        items = self.collected_network(include_preserved=True)
        if 1 <= reqid <= len(items):
            return items[reqid - 1]["packet"]
        return None

    def quit(self) -> None:
        try:
            if self.active_tab is not None:
                try:
                    self.active_tab.listen.stop()
                except Exception:
                    pass
                try:
                    self.active_tab.console.stop()
                except Exception:
                    pass
            self.browser.quit(del_data=not self.persistent)
        except Exception as e:
            logger.warning("浏览器会话退出异常: %s", e)


class BrowserSessionManager:
    """按 flow_id 管理浏览器会话：并发流程各自独立实例，互不干扰。

    空闲回收：后台协程定期扫描，超过 _BROWSER_IDLE_TIMEOUT 无工具调用的
    会话自动 quit（防 Chromium 进程泄漏——Agent 忘调 close 时兜底）。
    """

    def __init__(self):
        self._sessions: dict[int, BrowserSession] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None

    def _ensure_reaper(self) -> None:
        """惰性启动空闲回收协程（首次 get_or_create 时触发；事件循环内调用）"""
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def _reaper_loop(self) -> None:
        """空闲会话回收循环：每 60s 扫描一次，超时会话 quit 并移除"""
        while True:
            await asyncio.sleep(60)
            try:
                await self._reap_idle_sessions()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("浏览器空闲回收扫描异常: %s", e)

    async def _reap_idle_sessions(self) -> None:
        now = time.monotonic()
        expired_sessions: list[BrowserSession] = []
        async with self._lock:
            for flow_id, session in self._sessions.items():
                if now - session.last_used >= _BROWSER_IDLE_TIMEOUT:
                    expired_sessions.append(session)
            for session in expired_sessions:
                self._sessions.pop(session.flow_id, None)
        # quit 在锁外执行（同步阻塞调用，避免长时间持锁）
        for session in expired_sessions:
            logger.info(
                "浏览器会话空闲超时（%ss），自动回收 (flow_id=%s)",
                _BROWSER_IDLE_TIMEOUT,
                session.flow_id,
            )
            await asyncio.to_thread(session.quit)

    async def get_or_create(self, cfg, flow_id: int) -> BrowserSession:
        async with self._lock:
            self._ensure_reaper()
            session = self._sessions.get(flow_id)
            if session is not None:
                session.last_used = time.monotonic()
                self._warn_cfg_drift(session, cfg, flow_id)
                return session
            session = await asyncio.to_thread(self._create_session_sync, cfg, flow_id)
            self._sessions[flow_id] = session
            return session

    def get_existing(self, flow_id: int) -> Optional[BrowserSession]:
        """取已存在会话（不创建、不刷新 last_used，供 close 动作判空）"""
        return self._sessions.get(flow_id)

    def reuse(self, flow_id: int) -> Optional[BrowserSession]:
        """取已存在会话并刷新 last_used（不创建）；未存在返回 None。

        Flow 编排非 start 动作专用：连接配置仅 start 节点生效（唯一建会话
        入口是 get_or_create），后续动作只复用，缺会话由调用方报错引导。
        """
        session = self._sessions.get(flow_id)
        if session is not None:
            session.last_used = time.monotonic()
        return session

    @staticmethod
    def _warn_cfg_drift(session: BrowserSession, cfg, flow_id: int) -> None:
        """后续节点启动类配置与会话首建配置不一致时告警（后到配置不生效）"""
        if not session.launch_cfg:
            return
        current = {
            "headless": bool(getattr(cfg, "headless", True)),
            "debug_port": int(getattr(cfg, "debug_port", 0) or 0),
            "browser_path": (getattr(cfg, "browser_path", "") or "").strip(),
            "user_data_path": (getattr(cfg, "user_data_path", "") or "").strip(),
            "window_size": (getattr(cfg, "window_size", "") or "").strip(),
        }
        diff = [k for k, v in current.items() if v != session.launch_cfg.get(k)]
        if diff:
            logger.warning(
                "浏览器节点配置与会话首建配置不一致（flow_id=%s，会话按 flow_id 复用，"
                "本次配置不生效）: 字段=%s 首建=%s 当前节点=%s",
                flow_id,
                diff,
                session.launch_cfg,
                current,
            )

    def _find_free_port(self) -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _create_session_sync(self, cfg:"BrowserNodeConfig", flow_id: int) -> BrowserSession:
        from DrissionPage import Chromium, ChromiumOptions

        if cfg.connect_mode == "remote":
            address = f"{cfg.remote_host or '127.0.0.1'}:{cfg.remote_port}"
            co = ChromiumOptions(read_file=False)
            co.set_address(address)
            co.set_timeouts(base=_BROWSER_CONNECT_TIMEOUT)
            logger.info("浏览器节点连接远程浏览器: %s (flow_id=%s)", address, flow_id)
            browser = Chromium(co)
            session = BrowserSession(
                browser,
                port=cfg.remote_port,
                data_dir=None,
                persistent=True,
                flow_id=flow_id,
            )
            _start_debug_collectors(session)
            return session

        # launch 模式：调试端口（0=自动探测空闲端口；配置具体端口则固定使用）
        # + 按流程派生数据目录
        if cfg.debug_port and cfg.debug_port > 0:
            port = cfg.debug_port
        else:
            port = self._find_free_port()
        if cfg.user_data_path.strip():
            data_dir = Path(cfg.user_data_path.strip()) / f"flow_{flow_id}"
            persistent = True
        else:
            data_dir = get_temp_dir() / "browser_data" / f"flow_{flow_id}"
            persistent = False
        data_dir.mkdir(parents=True, exist_ok=True)

        co = ChromiumOptions(read_file=False)
        co.set_local_port(port)
        co.set_user_data_path(str(data_dir))
        # 无头模式按节点配置（默认 True 后台运行；False 弹出真实窗口便于调试观察）
        co.headless(bool(getattr(cfg, "headless", True)))
        # 窗口大小（默认 800,600）：headless 下视口即此尺寸，决定视口截图分辨率；
        # 解析失败静默用浏览器默认（配置字段是自由字符串，容错优先）。
        # 注意 --window-size 是窗口外框尺寸（headless 下视口会因边框略小），
        # 截图时的精确分辨率由 Emulation.setDeviceMetricsOverride 保证
        win_size = (getattr(cfg, "window_size", "") or "").strip()
        parts = (
            win_size.replace("×", ",").replace("*", ",").replace("x", ",").split(",")
        )
        if (
            len(parts) == 2
            and parts[0].strip().isdigit()
            and parts[1].strip().isdigit()
        ):
            co.set_argument("--window-size", f"{parts[0].strip()},{parts[1].strip()}")
        elif win_size:
            logger.warning(
                "window_size 格式无效（期望 宽,高）: %r，使用浏览器默认", win_size
            )
        co.set_timeouts(base=_BROWSER_CONNECT_TIMEOUT)
        if cfg.browser_path.strip():
            co.set_browser_path(cfg.browser_path.strip())
        logger.info(
            "浏览器节点启动独立实例: port=%s data_dir=%s (flow_id=%s)",
            port,
            data_dir,
            flow_id,
        )
        browser = Chromium(co)
        session = BrowserSession(
            browser,
            port=port,
            data_dir=data_dir,
            persistent=persistent,
            flow_id=flow_id,
            launch_cfg={
                "headless": bool(getattr(cfg, "headless", True)),
                "debug_port": port,
                "browser_path": (cfg.browser_path or "").strip(),
                "user_data_path": (cfg.user_data_path or "").strip(),
                "window_size": (getattr(cfg, "window_size", "") or "").strip(),
            },
        )
        # 建会话即开启 console+network 常驻收集（参考 chrome-devtools-mcp
        # PageCollector 语义）：否则页面加载期（首次工具调用前）的 JS 报错
        # 与初始化请求全部丢失（enable 之前产生的事件收不到）
        _start_debug_collectors(session)
        return session

    async def release(self, flow_id: int) -> None:
        """flow 结束时释放会话（quit 浏览器进程）"""
        async with self._lock:
            session = self._sessions.pop(flow_id, None)
        if session is not None:
            await asyncio.to_thread(session.quit)

    async def release_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            try:
                await asyncio.to_thread(s.quit)
            except Exception:
                pass


def _start_debug_collectors(session: "BrowserSession") -> None:
    """建会话时对初始 tab 开启 console+network 常驻收集（失败不阻断建会话）"""
    try:
        session.browser.latest_tab.console.start()
    except Exception as e:
        logger.warning("开启 console 收集失败（console_logs 将无数据）: %s", e)
    try:
        session.browser.latest_tab.listen.start(
            res_type=list(_NETWORK_DEFAULT_RES_TYPES)
        )
    except Exception as e:
        logger.warning("开启 network 收集失败（可用 action=start 重试）: %s", e)


# 模块级单例：Flow 编排与 Agent 工具共享同一会话表（按 flow_id 隔离）
browser_session_manager = BrowserSessionManager()


def get_flow_id() -> int:
    """从执行上下文取 flow_id，取不到返回 0（共享默认会话）"""
    ctx = get_execution_context()
    if ctx and ctx.expanded_flow is not None:
        return ctx.expanded_flow.id or ctx.flow_id or 0
    return 0


def _parse_ref(ref: str) -> Optional[int]:
    """解析 snapshot 元素引用为 1-based 编号；非引用形态返回 None。

    兼容两种形态：snapshot 输出的「uid=N」（LLM 常原样回传）与裸数字「N」。
    """
    s = ref.strip()
    if s.isdigit():
        return int(s)
    low = s.lower()
    if low.startswith("uid=") and low[4:].isdigit():
        return int(low[4:])
    return None


def _current_tab_id(session: "BrowserSession") -> Optional[str]:
    """当前活动标签页的 tab_id（CDP targetId 字符串，标签页生命周期内稳定）。

    各工具返回 dict 里带 tab_id 字段，让 LLM 始终知道操作发生在哪个标签页
    （uid 只对当前 tab 有效，切 tab 后需重新 snapshot）。
    死对象坑：active_tab 被关闭后访问 .tab_id 抛 PageDisconnectedError，
    兜底返回 None。
    """
    tab = session.active_tab
    if tab is None:
        return None
    try:
        return tab.tab_id
    except Exception:
        return None


def _find_tab_by_ref(session: "BrowserSession", tab_id) -> Optional["MixTab"]:
    """按 tab_id（CDP targetId 字符串）查找标签页对象；不改变 active_tab。

    targetId 在标签页生命周期内恒定，关闭其他标签页不漂移。
    兼容期：纯数字入参按旧版 get_tabs() 下标解释（最新在前，已废弃；
    targetId 恒为 32 位 hex，与短数字下标无歧义）。找不到返回 None。
    tab 对象已断连时访问 .tab_id 抛异常，跳过该对象。
    """
    if tab_id is None or tab_id == "":
        return None
    browser:Chromium = session.browser
    tabs = browser.get_tabs()
    for t in tabs:
        try:
            if t.tab_id == tab_id:
                return t
        except Exception:
            continue
    s = str(tab_id)
    if s.isdigit():
        idx = int(s)
        if 0 <= idx < len(tabs):
            return tabs[idx]
    return None


def _resolve_tab(session: "BrowserSession", tab_id: Optional[str]) -> Optional[MixTab]:
    """按工具入参 tab_id 定位标签页（None=当前活动 tab）。

    tab_id 为 CDP targetId 字符串（browser_tabs list 返回的 tab_id），
    合法时切换 session.active_tab 并返回该 tab；非法返回 None
    （由调用方报错）。LLM 可直接传 snapshot/browser_tabs 返回的 tab_id
    操作指定标签页，无需先 browser_tabs(select)。
    """
    browser:Chromium = session.browser
    tabs = browser.get_tabs()
    if tab_id is None or tab_id == "":
        return session.active_tab or (tabs[-1] if tabs else None)
    tab = _find_tab_by_ref(session, tab_id)
    if tab is not None:
        session.active_tab = tab
    return tab


class BrowserToolKit(BaseToolKit):
    """浏览器 Agent 工具集（14 个工具，snapshot 范式）

    由 browser_handler.get_tool 构造，注入 cfg / flow_id / 审批回调。
    """

    def __init__(
        self,
        cfg,
        flow_id: int,
        approval_cb: ApprovalCallback,
    ):
        self._cfg = cfg
        self._flow_id = flow_id
        self._approval = approval_cb

    async def build_tools(self) -> Sequence[StructuredTool]:
        cfg = self._cfg
        flow_id = self._flow_id
        _approval = self._approval

        async def _get_session() -> BrowserSession:
            return await browser_session_manager.get_or_create(cfg, flow_id)

        # ---- browser_snapshot ----

        async def browser_snapshot() -> str | dict:
            approval = await _approval("browser_snapshot", {}, "")
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = session.active_tab or session.browser.latest_tab
                    session.active_tab = tab
                    return _build_ax_snapshot(tab)

                snapshot_text = await asyncio.to_thread(run)

                def _tab_id() -> dict:
                    tabs = session.browser.get_tabs()
                    current = session.active_tab
                    try:
                        cur_id = current.tab_id if current is not None else None
                    except Exception:
                        cur_id = None
                    return {
                        "tab_id": cur_id,
                        "tab_count": len(tabs),
                        "tab_urls": [{"tab_id": t.tab_id, "url": t.url} for t in tabs],
                    }

                tab_info = await asyncio.to_thread(_tab_id)
                tab_line = (
                    f"tab_id: {tab_info['tab_id']}（共 {tab_info['tab_count']} 个标签页，"
                    "browser_tabs(action=select, tab_id='...') 切换）"
                )
                other_tabs = [
                    t for t in tab_info["tab_urls"] if t["tab_id"] != tab_info["tab_id"]
                ]
                if other_tabs:
                    tab_line += "\n其他标签页: " + ", ".join(
                        f"tab_id={t['tab_id']} {t['url'][:60]}" for t in other_tabs
                    )
                active_tab = session.active_tab
                if active_tab is None:
                    raise RuntimeError("session.active_tab为空")
                url = await asyncio.to_thread(lambda: active_tab.url)
                title = await asyncio.to_thread(lambda: active_tab.title)
                return (
                    "## Latest page snapshot（a11y 树，uid=N 为可交互元素编号）\n"
                    f"{tab_line}\n"
                    f"url: {url}\n"
                    f"title: {title}\n\n"
                    f"{snapshot_text}\n\n"
                    "用 uid=N 引用元素进行 browser_click/browser_input；"
                    "uid 只对当前 tab_id 的页面有效，切换标签页后需重新 snapshot；"
                    "页面导航后 uid 失效需重新 snapshot"
                )
            except Exception as e:
                return {"error": f"快照失败: {e}", "success": False}

        # ---- browser_navigate ----

        async def browser_navigate(
            url: str, action: str = "goto", tab_id: Optional[str] = None
        ) -> str | dict:
            approval = await _approval(
                "browser_navigate", {"url": url, "tab_id": tab_id}, url
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    session.active_tab = tab
                    if action == "back":
                        tab.back()
                    elif action == "forward":
                        tab.forward()
                    elif action == "reload":
                        tab.refresh()
                    else:
                        tab.get(url)
                    return {
                        "success": True,
                        "url": tab.url,
                        "title": tab.title,
                        "tab_id": _current_tab_id(session),
                    }

                # 写操作：同流程内串行（写锁），跨流程并行
                async with session.write_lock:
                    return await asyncio.to_thread(run)
            except Exception as e:
                return {"error": f"导航失败: {e}", "success": False}

        # ---- browser_click ----

        async def browser_click(ref: str, tab_id: Optional[str] = None) -> str | dict:
            approval = await _approval(
                "browser_click", {"ref": ref, "tab_id": tab_id}, ref
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    raw = ref.strip()
                    tabs_before = len(session.browser.get_tabs())
                    ref_num = _parse_ref(raw)
                    if ref_num is not None:
                        idx = ref_num - 1
                        # 优先按 snapshot 锚定的 data-agent-ref 属性精确查找
                        # （与 DOM 顺序变化解耦，动态页面不再"ref 不存在"）；
                        # 属性未命中（旧快照/页面重渲染）回退按下标对齐。
                        ok = tab.run_js(
                            "return (function(){"
                            f"var el = document.querySelector('[data-agent-ref=\\\"{idx + 1}\\\"]');"
                            "if (!el) {"
                            'var els = document.querySelectorAll(\'a, button, input, select, textarea, [contenteditable="true"], [role="button"], [onclick]\');'
                            "var vis = []; for (var i=0;i<els.length;i++) { var r=els[i].getBoundingClientRect(); if (r.width>0&&r.height>0) vis.push(els[i]); }"
                            f"el = vis[{idx}];"
                            "}"
                            "if (!el) return 'ref 不存在，请重新 browser_snapshot';"
                            "el.click(); return 'clicked';})()"
                        )
                        if ok != "clicked":
                            return {"error": str(ok), "success": False}
                    else:
                        ele = tab.ele(
                            _normalize_locator(raw), timeout=_ELE_FIND_TIMEOUT
                        )
                        if not ele:
                            return {"error": f"未找到元素: {raw}", "success": False}
                        if isinstance(ele,ChromiumElement):
                            ele.click()

                    result = {
                        "success": True,
                        "note": "点击完成，建议 browser_snapshot 确认结果",
                        "tab_id": _current_tab_id(session),
                    }
                    # target=_blank 点击会在新标签页打开（当前页 URL 不变），
                    # 实测坑：看起来"点击无效"，实际目标页在 0 号位新标签页。
                    # 检测到标签页增加时自动切换到新页，避免 LLM 误判失败。
                    time.sleep(0.5)
                    tabs_after = session.browser.get_tabs()
                    if len(tabs_after) > tabs_before:
                        new_tab = tabs_after[-1]
                        session.active_tab = new_tab
                        result["note"] = (
                            "点击完成；目标在新标签页打开（target=_blank），"
                            "已自动切换到新标签页"
                        )
                        result["new_tab_url"] = new_tab.url
                        result["tab_id"] = _current_tab_id(session)
                    return result

                # 写操作：同流程内串行（写锁），跨流程并行
                async with session.write_lock:
                    return await asyncio.to_thread(run)
            except Exception as e:
                return {"error": f"点击失败: {e}", "success": False}

        # ---- browser_input ----

        async def browser_input(
            ref: str, text: str, clear: bool = True, tab_id: Optional[str] = None
        ) -> str | dict:
            approval = await _approval(
                "browser_input", {"ref": ref, "text": text, "tab_id": tab_id}, text
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    raw = ref.strip()
                    ref_num = _parse_ref(raw)
                    if ref_num is not None:
                        idx = ref_num - 1
                        # 优先按 data-agent-ref 属性精确查找（与 snapshot 锚定一致，
                        # 修复枚举选择器不一致导致的"ref 不存在"/错位聚焦）；
                        # 属性未命中回退按全量可交互元素下标对齐。
                        result = tab.run_js(
                            "return (function(){"
                            f"var el = document.querySelector('[data-agent-ref=\\\"{idx + 1}\\\"]');"
                            "if (!el) {"
                            'var els = document.querySelectorAll(\'a, button, input, select, textarea, [contenteditable="true"], [role="button"], [onclick]\');'
                            "var vis = []; for (var i=0;i<els.length;i++) { var r=els[i].getBoundingClientRect(); if (r.width>0&&r.height>0) vis.push(els[i]); }"
                            f"el = vis[{idx}];"
                            "}"
                            "if (!el) return 'ref 不存在，请重新 browser_snapshot';"
                            "el.focus(); return 'focused';})()"
                        )
                        if result != "focused":
                            return {"error": str(result), "success": False}
                        if clear:
                            tab.actions.key_down("Control").key_down("a").key_up(
                                "a"
                            ).key_up("Control")
                            tab.actions.type(" ")
                            tab.actions.key_down("Backspace").key_up("Backspace")
                        tab.actions.type(text)
                    else:
                        ele = tab.ele(
                            _normalize_locator(raw), timeout=_ELE_FIND_TIMEOUT
                        )
                        if not ele:
                            return {"error": f"未找到元素: {raw}", "success": False}
                        if isinstance(ele,ChromiumElement):
                            ele.clear() if clear else None
                            ele.input(text)
                    return {
                        "success": True,
                        "note": "输入完成",
                        "tab_id": _current_tab_id(session),
                    }

                # 写操作：同流程内串行（写锁），跨流程并行
                async with session.write_lock:
                    return await asyncio.to_thread(run)
            except Exception as e:
                return {"error": f"输入失败: {e}", "success": False}

        # ---- browser_screenshot ----

        async def browser_screenshot(
            full_page: bool = False,
            width: Optional[int] = None,
            height: Optional[int] = None,
            tab_id: Optional[str] = None,
        ) -> str | dict:
            approval = await _approval(
                "browser_screenshot",
                {
                    "full_page": full_page,
                    "width": width,
                    "height": height,
                    "tab_id": tab_id,
                },
                "",
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run() -> dict[str,Any] | str | bytes:
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    session.active_tab = tab
                    # 临时视口覆盖控制截图分辨率（Emulation.setDeviceMetricsOverride，
                    # 精确像素；--window-size/Browser.setWindowBounds 是外框语义，
                    # 视口会因边框略小）。仅本次截图生效，截完恢复，避免影响元素定位
                    old_metrics = None
                    if width or height:
                        vp = tab.rect.viewport_size
                        old_metrics = (vp[0], vp[1])
                        new_w = width or old_metrics[0]
                        new_h = height or old_metrics[1]
                        tab.run_cdp(
                            "Emulation.setDeviceMetricsOverride",
                            width=int(new_w),
                            height=int(new_h),
                            deviceScaleFactor=1,
                            mobile=False,
                        )
                    try:
                        png = tab.get_screenshot(as_bytes="png", full_page=full_page)
                    finally:
                        if old_metrics:
                            tab.run_cdp(
                                "Emulation.setDeviceMetricsOverride",
                                width=int(old_metrics[0]),
                                height=int(old_metrics[1]),
                                deviceScaleFactor=1,
                                mobile=False,
                            )
                    return png

                try:
                    # 实测坑：全页截图（captureBeyondViewport）在内容重的页面
                    # （Bing 结果页等）30s 拿不到结果，且一次超时后 CDP 截图
                    # 通道会短暂卡死（连带下一次也超时，但 evaluate 仍正常）。
                    # 这里限时 30s，超时返回明确引导而非挂死工具调用。
                    png = await asyncio.wait_for(
                        asyncio.to_thread(run), timeout=_SCREENSHOT_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    return {
                        "error": (
                            "截图超时（30秒）。全页截图（full_page=True）在内容较重的页面上"
                            "经常超时：建议改用 full_page=False（仅视口）+ browser_scroll 分段截图；"
                            "若连续超时，CDP 截图通道可能已卡住，等几秒重试或 browser_close 后重启"
                        ),
                        "success": False,
                        "error_type": "screenshot_timeout",
                    }
                if not isinstance(png,bytes):
                    raise RuntimeError("png不是bytes类型")
                media = await save_media_bytes(
                    png,
                    "image/png",
                    source_type="tool_generated",
                    flow_id=flow_id,
                    original_name="browser_screenshot.png",
                )
                return {
                    "success": True,
                    "preview_url": media.get("preview_url"),
                    "download_url": media.get("download_url"),
                    "file_id": media.get("file_id"),
                    "note": "截图已保存，可在界面预览或下载",
                    "tab_id": _current_tab_id(session),
                }
            except Exception as e:
                return {"error": f"截图失败: {e}", "success": False}

        # ---- browser_get_content ----

        async def browser_get_content(
            selector: str = "", tab_id: Optional[str] = None
        ) -> str | dict:
            approval = await _approval(
                "browser_get_content",
                {"selector": selector, "tab_id": tab_id},
                selector,
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    session.active_tab = tab
                    sel = _normalize_locator(selector)
                    if sel:
                        ele = tab.ele(sel, timeout=_ELE_FIND_TIMEOUT)
                        if not ele:
                            return {
                                "success": False,
                                "content": "",
                                "error": f"未找到元素: {selector}",
                                "tab_id": _current_tab_id(session),
                            }
                        return {
                            "success": True,
                            "content": ele.text,
                            "url": tab.url,
                            "tab_id": _current_tab_id(session),
                        }
                    return {
                        "success": True,
                        "content": tab.ele("t:body").text,
                        "url": tab.url,
                        "tab_id": _current_tab_id(session),
                    }

                result = await asyncio.to_thread(run)
                return json.loads(
                    smart_truncate_output(result, prefix="browser_content")
                )
            except Exception as e:
                return {"error": f"取内容失败: {e}", "success": False}

        # ---- browser_evaluate ----

        async def browser_evaluate(
            script: str, tab_id: Optional[str] = None
        ) -> str | dict:
            approval = await _approval(
                "browser_evaluate", {"script": script, "tab_id": tab_id}, script
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    session.active_tab = tab
                    body = script if "return" in script else f"return ({script})"
                    return {
                        "success": True,
                        "result": tab.run_js(body),
                        "tab_id": _current_tab_id(session),
                    }

                return await asyncio.to_thread(run)
            except Exception as e:
                return {"error": f"JS 执行失败: {e}", "success": False}

        # ---- browser_tabs ----

        async def browser_tabs(
            action: str = "list", index: int = 0, tab_id: Optional[str] = None
        ) -> str | dict:
            approval = await _approval(
                "browser_tabs", {"action": action, "index": index, "tab_id": tab_id}, ""
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    browser = session.browser
                    if action == "new":
                        tab = browser.new_tab()
                        session.active_tab = tab
                        return {
                            "success": True,
                            "url": tab.url,
                            "note": "新标签页已创建并激活",
                            "tab_id": _current_tab_id(session),
                        }
                    if action == "close":
                        # 传 tab_id（targetId）关指定标签页；不传关活动标签页。
                        # 注意关掉活动页自身后 active_tab 成死对象
                        # （后续 CDP 调用抛 PageDisconnectedError），需同步置空
                        if tab_id is not None:
                            closing = _find_tab_by_ref(session, tab_id)
                            if closing is None:
                                return {
                                    "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                                    "success": False,
                                }
                            closing.close()
                            if session.active_tab is closing:
                                session.active_tab = None
                            return {
                                "success": True,
                                "closed_tab_id": tab_id,
                                "note": "标签页已关闭",
                            }
                        if session.active_tab is not None:
                            session.active_tab.close()
                            session.active_tab = None
                        return {"success": True, "note": "活动标签页已关闭"}
                    tabs = browser.get_tabs()
                    # tab_id = CDP targetId（标签页生命周期内稳定，关其他页不漂移）
                    items = [
                        {"tab_id": t.tab_id, "url": t.url, "title": t.title}
                        for t in tabs
                    ]
                    if action == "select":
                        target = None
                        if tab_id:
                            target = _find_tab_by_ref(session, tab_id)
                            if target is None:
                                return {
                                    "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                                    "success": False,
                                }
                        elif 0 <= index < len(tabs):
                            target = tabs[index]  # 兼容旧 index 用法（最新在前）
                        else:
                            return {
                                "error": f"索引超出范围 0~{len(tabs) - 1}",
                                "success": False,
                            }
                        session.active_tab = target
                        return {
                            "success": True,
                            "selected": {
                                "tab_id": target.tab_id,
                                "url": target.url,
                                "title": target.title,
                            },
                            "tab_id": _current_tab_id(session),
                            "note": (
                                "已切换标签页；uid 只对切换前页面有效，"
                                "请重新 browser_snapshot"
                            ),
                        }
                    return {"success": True, "tabs": items}

                # new/close 是写操作（list/select 只读，低频统一走锁简化语义）
                async with session.write_lock:
                    return await asyncio.to_thread(run)
            except Exception as e:
                return {"error": f"标签页操作失败: {e}", "success": False}

        # ---- browser_scroll ----

        async def browser_scroll(
            direction: str = "down",
            amount: int = 600,
            tab_id: Optional[str] = None,
        ) -> str | dict:
            approval = await _approval(
                "browser_scroll", {"direction": direction, "tab_id": tab_id}, ""
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    session.active_tab = tab
                    delta = amount if direction == "down" else -amount
                    tab.run_js(f"return window.scrollBy(0, {delta}); 'scrolled'")
                    return {
                        "success": True,
                        "note": "已滚动",
                        "tab_id": _current_tab_id(session),
                    }

                # 写操作：同流程内串行（写锁），跨流程并行
                async with session.write_lock:
                    return await asyncio.to_thread(run)
            except Exception as e:
                return {"error": f"滚动失败: {e}", "success": False}

        # ---- browser_wait_for ----

        async def browser_wait_for(
            text: str, timeout: int = 10, tab_id: Optional[str] = None
        ) -> str | dict:
            # 入参钳制：模型常按毫秒直觉传 5000（想表达 5 秒），而 DP 的
            # timeout 单位是秒——不钳制会挂 5000 秒（两段串行最坏 2 倍）。
            # 上限 120 秒兜底，下限 1 秒防误传 0/负数
            timeout = min(max(1, int(timeout)), 120)
            approval = await _approval(
                "browser_wait_for", {"text": text, "tab_id": tab_id}, text
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    session.active_tab = tab
                    # 实测坑：text= 是精确全文本匹配（XPath text()="..."），
                    # 元素文本为「点击登录」时等「登录」永远匹配不上 → found=false。
                    # 改用 text:（包含匹配）；先查存在性再查显示性，避免
                    # ele_displayed 把超时全耗在定位上导致可见性检查零时间失败。
                    # 两段共享预算：存在性检查用一半，给可见性检查留时间
                    locator = f"text:{text}"
                    if not tab.ele(locator, timeout=max(1, timeout // 2)):
                        return {
                            "success": False,
                            "found": False,
                            "text": text,
                            "note": "超时未找到包含该文本的元素（包含匹配）",
                        }
                    found = tab.wait.ele_displayed(locator, timeout=timeout)
                    return {
                        "success": bool(found),
                        "found": bool(found),
                        "text": text,
                        "tab_id": _current_tab_id(session),
                    }

                return await asyncio.to_thread(run)
            except Exception as e:
                return {"error": f"等待失败: {e}", "success": False}

        # ---- browser_start ----

        async def browser_start(browser_path: Optional[str] = None) -> str | dict:
            approval = await _approval(
                "browser_start", {"browser_path": browser_path or ""}, ""
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                # AI 探测到非默认浏览器时直接传 browser_path（如 Edge 路径）：
                # 对节点配置副本覆写后建会话，不污染原 cfg；会话已存在时忽略
                start_cfg = cfg
                override = (browser_path or "").strip()
                if override:
                    start_cfg = cfg.model_copy(update={"browser_path": override})
                # get_or_create 幂等：已存在直接复用（返回已就绪状态）
                session = await browser_session_manager.get_or_create(
                    start_cfg, flow_id
                )

                def run():
                    tab = session.active_tab or session.browser.latest_tab
                    session.active_tab = tab
                    return {
                        "success": True,
                        "url": tab.url,
                        "title": tab.title,
                        "mode": cfg.connect_mode,
                        "tab_id": _current_tab_id(session),
                    }

                result = await asyncio.to_thread(run)
                result["note"] = (
                    "浏览器已就绪。后续用 browser_navigate 访问页面，"
                    "browser_snapshot 查看元素，browser_click/browser_input 操作"
                )
                return result
            except Exception as e:
                return {"error": f"浏览器启动失败: {e}", "success": False}

        # ---- browser_close ----

        async def browser_close() -> str | dict:
            approval = await _approval("browser_close", {}, "")
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                await browser_session_manager.release(flow_id)
                return {
                    "success": True,
                    "note": (
                        "浏览器已关闭"
                        + ("，临时数据已清理" if not cfg.user_data_path.strip() else "")
                        + "；再次调用 browser_start 可重新启动"
                    ),
                }
            except Exception as e:
                return {"error": f"浏览器关闭失败: {e}", "success": False}

        # ---- browser_console_logs ----

        async def browser_console_logs(
            action: str = "list",
            level: str = "all",
            include_preserved: bool = False,
            page_size: int = 50,
            page_idx: int = 0,
            tab_id: Optional[str] = None,
        ) -> str | dict:
            approval = await _approval(
                "browser_console_logs", {"action": action, "level": level}, ""
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    session.active_tab = tab
                    if action == "clear":
                        session.nav_segments = [
                            {"url": (tab.url or "")[:500], "console": [], "network": []}
                        ]
                        try:
                            tab.console.clear()
                        except Exception:
                            pass
                        return {"success": True, "note": "控制台日志缓冲已清空"}
                    # list：排空队列进分段缓冲后按级别过滤 + 分页
                    # （默认仅当前导航段，include_preserved 回看最近 3 次导航）
                    session.drain_console(tab)
                    lv = (level or "all").strip().lower()
                    items = session.collected_console(include_preserved)
                    if lv and lv != "all":
                        items = [m for m in items if (m["level"] or "").lower() == lv]
                    ps = max(1, int(page_size))
                    pi = max(0, int(page_idx))
                    page = items[pi * ps : (pi + 1) * ps]
                    return {
                        "success": True,
                        "logs": page,
                        "total": len(items),
                        "page_idx": pi,
                        "page_size": ps,
                        "note": (
                            f"当前导航段共 {len(session.nav_segments[0]['console'])} 条，"
                            f"过滤后 {len(items)} 条，本页 {len(page)} 条（level={lv}）；"
                            "浏览器启动后全程捕获，navigate 后调用可看页面加载期报错"
                        ),
                    }

                return await asyncio.to_thread(run)
            except Exception as e:
                return {"error": f"读取控制台日志失败: {e}", "success": False}

        # ---- browser_network ----

        async def browser_network(
            action: str = "list",
            reqid: Optional[int] = None,
            resource_types: str = "",
            url_filter: str = "",
            method: str = "",
            include_preserved: bool = False,
            page_size: int = 50,
            page_idx: int = 0,
            tab_id: Optional[str] = None,
        ) -> str | dict:
            approval = await _approval(
                "browser_network",
                {"action": action, "reqid": reqid, "url_filter": url_filter},
                url_filter,
            )
            if approval not in (None, "approved"):
                return {"error": f"用户未批准（{approval}）", "success": False}
            try:
                session = await _get_session()

                def run():
                    tab = _resolve_tab(session, tab_id)
                    if tab is None:
                        return {
                            "error": f"tab_id={tab_id} 不存在，请先 browser_tabs(action='list') 查看可用标签页",
                            "success": False,
                        }
                    session.active_tab = tab

                    if action == "get":
                        packet = session.find_network_packet(int(reqid or 0))
                        if packet is None:
                            return {
                                "success": False,
                                "error": f"reqid={reqid} 不存在：请先用 action=list 获取有效编号"
                                "（缓冲保留最近 3 次导航，更早的请求已被截断）",
                            }
                        detail = dict(_packet_summary(packet))
                        try:
                            if packet.is_failed:
                                detail["fail_reason"] = getattr(
                                    packet.fail_info, "errorText", None
                                )
                            else:
                                resp = packet.response
                                detail["response_headers"] = dict(
                                    list((resp.headers or {}).items())[:40]
                                )
                                body = resp.raw_body
                                if body is None:
                                    detail["body"] = "<not available anymore>"
                                elif not isinstance(body, str):
                                    detail["body"] = "<binary data>"
                                elif len(body) == 0:
                                    detail["body"] = "<empty response>"
                                elif len(body) > _NETWORK_BODY_MAX:
                                    detail["body"] = (
                                        body[:_NETWORK_BODY_MAX] + "... <truncated>"
                                    )
                                else:
                                    detail["body"] = body
                        except Exception as e:
                            detail["body"] = f"<parse error: {e}>"
                        return {"success": True, "request": detail}

                    if action == "start":
                        # 覆盖监听过滤条件。坑：DP start() 空参数不重置过滤
                        # （`if targets or ... or res_type:` 全空跳过 set_targets），
                        # 须显式传全量：空串=True（不过滤）
                        kw = {
                            "targets": url_filter.strip() or True,
                            "method": method.strip().upper() or True,
                            "res_type": (
                                [
                                    t.strip().upper()
                                    for t in resource_types.split(",")
                                    if t.strip()
                                ]
                                or True
                            ),
                        }
                        tab.listen.start(**kw)
                        return {
                            "success": True,
                            "note": (
                                "监听过滤已覆盖为: "
                                f"url={url_filter or '全部'} method={method or '全部'} "
                                f"type={resource_types or '全部'}；"
                                "之后触发的请求按新条件捕获"
                            ),
                        }

                    if action == "stop":
                        tab.listen.stop()
                        return {
                            "success": True,
                            "note": "网络收集已暂停（缓冲存量仍可 list/get；恢复用 action=start）",
                        }

                    if action == "performance":
                        raw = tab.run_js(_PERFORMANCE_JS)
                        entries = (
                            json.loads(raw) if isinstance(raw, str) else (raw or [])
                        )
                        return {
                            "success": True,
                            "resources": entries,
                            "total": len(entries),
                            "note": (
                                "资源时间线（全量 URL+耗时，无状态码；仅当前文档，"
                                "导航后清空）。需要状态码/响应体用 action=list + get"
                            ),
                        }

                    # list：排空进分段缓冲后过滤 + 分页（默认仅当前导航段）。
                    # 常驻收集下永远可用，无需先 start
                    session.drain_network(tab)
                    items = session.collected_network(include_preserved)
                    rt = {
                        t.strip().upper()
                        for t in (resource_types or "").split(",")
                        if t.strip()
                    }
                    if rt:
                        items = [
                            it
                            for it in items
                            if (it["summary"].get("resource_type") or "").upper() in rt
                        ]
                    if url_filter.strip():
                        items = [
                            it
                            for it in items
                            if url_filter.strip() in it["summary"]["url"]
                        ]
                    if method.strip():
                        items = [
                            it
                            for it in items
                            if it["summary"]["method"].upper() == method.strip().upper()
                        ]
                    # reqid 基于过滤前的全量序号（1 起，与 get 对齐）
                    base = session.collected_network(include_preserved)
                    offset_map = {id(it["packet"]): i + 1 for i, it in enumerate(base)}
                    ps = max(1, int(page_size))
                    pi = max(0, int(page_idx))
                    page = items[pi * ps : (pi + 1) * ps]
                    return {
                        "success": True,
                        "requests": [
                            {
                                **it["summary"],
                                "reqid": offset_map.get(id(it["packet"]), 0),
                            }
                            for it in page
                        ],
                        "total": len(items),
                        "page_idx": pi,
                        "page_size": ps,
                        "note": (
                            f"共 {len(items)} 条，本页 {len(page)} 条；"
                            "action=get + reqid 查单条详情（含响应体）；"
                            "include_preserved=true 回看最近 3 次导航"
                        ),
                    }

                return await asyncio.to_thread(run)
            except Exception as e:
                return {"error": f"网络查看操作失败: {e}", "success": False}

        return [
            StructuredTool(
                name="browser_start",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_start"],
                args_schema=StartInput,
                func=None,
                coroutine=browser_start,
            ),
            StructuredTool(
                name="browser_snapshot",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_snapshot"],
                args_schema=_NoArgsInput,
                func=None,
                coroutine=browser_snapshot,
            ),
            StructuredTool(
                name="browser_navigate",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_navigate"],
                args_schema=NavigateInput,
                func=None,
                coroutine=browser_navigate,
            ),
            StructuredTool(
                name="browser_click",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_click"],
                args_schema=RefInput,
                func=None,
                coroutine=browser_click,
            ),
            StructuredTool(
                name="browser_input",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_input"],
                args_schema=InputInput,
                func=None,
                coroutine=browser_input,
            ),
            StructuredTool(
                name="browser_screenshot",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_screenshot"],
                args_schema=ScreenshotInput,
                func=None,
                coroutine=browser_screenshot,
            ),
            StructuredTool(
                name="browser_get_content",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_get_content"],
                args_schema=GetContentInput,
                func=None,
                coroutine=browser_get_content,
            ),
            StructuredTool(
                name="browser_evaluate",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_evaluate"],
                args_schema=EvaluateInput,
                func=None,
                coroutine=browser_evaluate,
            ),
            StructuredTool(
                name="browser_tabs",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_tabs"],
                args_schema=TabsInput,
                func=None,
                coroutine=browser_tabs,
            ),
            StructuredTool(
                name="browser_scroll",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_scroll"],
                args_schema=ScrollInput,
                func=None,
                coroutine=browser_scroll,
            ),
            StructuredTool(
                name="browser_wait_for",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_wait_for"],
                args_schema=WaitForInput,
                func=None,
                coroutine=browser_wait_for,
            ),
            StructuredTool(
                name="browser_close",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_close"],
                args_schema=_NoArgsInput,
                func=None,
                coroutine=browser_close,
            ),
            StructuredTool(
                name="browser_console_logs",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_console_logs"],
                args_schema=ConsoleInput,
                func=None,
                coroutine=browser_console_logs,
            ),
            StructuredTool(
                name="browser_network",
                description=BROWSER_TOOL_DESCRIPTIONS["browser_network"],
                args_schema=NetworkInput,
                func=None,
                coroutine=browser_network,
            ),
        ]

    @classmethod
    def tool_info(cls, node=None) -> list[dict]:
        """从描述常量派生（单源：与 build_tools 产出逐字节一致）"""
        return [
            {"name": n, "description": d} for n, d in BROWSER_TOOL_DESCRIPTIONS.items()
        ]


from app.utils.media_file import save_media_bytes  # noqa: E402  工具闭包内使用，置底避免循环导入
