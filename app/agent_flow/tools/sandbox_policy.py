"""沙箱策略解析（唯一事实源）

职责：
1. resolve_opaque_policy：LLM 节点 sandbox_opaque_policy 配置 → 最终策略
   （ask/deny/off）。auto 按运行域分流：Agent 会话=ask，Flow=off。
2. get_writable_roots：可写目录白名单（会话/Agent 工作目录 + temp + .env 追加项）。

设计约束（AGENTS.md「确定性归代码」）：
- 域判定与 base_handler._resolve_context 同源（_session_id/_execution_id），
  禁止第二套判定。
- 本模块纯函数 + settings 读取，不做 DB 查询、不做审批（审批由调用方
  通过注入的 approval_cb 执行）。

消费方：setup_tool_handlers（注入 handler._sandbox_policy）、
shell_toolkit / ssh_toolkit / file_toolkit / python_handler（open 收口）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config.build_utils import BASE_DIR, get_temp_dir
from app.config.settings import settings

POLICY_ASK = "ask"
POLICY_DENY = "deny"
POLICY_OFF = "off"
POLICY_AUTO = "auto"
_VALID_POLICIES = frozenset({POLICY_ASK, POLICY_DENY, POLICY_OFF, POLICY_AUTO})


def resolve_opaque_policy(
    node_value: Optional[str],
    session_id: int,
    execution_id: int,
) -> str:
    """解析最终 opaque 策略。

    优先级：节点显式配置（ask/deny/off）> auto 按域分流 > 非法值回退 auto。

    Args:
        node_value: LLM 节点 sandbox_opaque_policy 配置值（None/"" = auto）
        session_id: >0 表示 Agent 会话（与 base_handler._session_id 同源）
        execution_id: >0 表示 Flow 执行（与 base_handler._execution_id 同源）

    Returns:
        "ask" / "deny" / "off"
    """
    value = (node_value or "").strip().lower()
    # 显式档位直接生效；auto/非法值/空 → 按域分流（Agent=ask，Flow=off）。
    # POLICY_AUTO 不在直接生效集合内：它本身不是返回值，只触发域分流
    if value in _VALID_POLICIES and value != POLICY_AUTO:
        return value
    if session_id > 0:
        return POLICY_ASK
    return POLICY_OFF


def _parse_env_roots() -> list[Path]:
    """解析 .env SANDBOX_WRITABLE_ROOTS（逗号/分号分隔；相对路径基于项目根）"""
    raw = (getattr(settings, "sandbox_writable_roots", "") or "").strip()
    if not raw:
        return []
    roots: list[Path] = []
    for part in raw.replace(";", ",").split(","):
        item = part.strip()
        if not item:
            continue
        p = Path(item).expanduser()
        if not p.is_absolute():
            p = BASE_DIR / p
        try:
            roots.append(p.resolve())
        except (OSError, RuntimeError):
            continue
    return roots


# 实例级缓存：.env 在进程内不变，解析一次即可（设置页 UI 本期不提供，无失效问题）
_ENV_ROOTS_CACHE: Optional[list[Path]] = None


def get_writable_roots(
    session_work_dir: Optional[Path] = None,
    agent_work_dir: Optional[Path] = None,
    node_work_dir: Optional[Path] = None,
) -> list[Path]:
    """返回当前运行域的可写目录白名单（绝对路径列表）。

    组成：会话工作目录（聊天页配置）+ 节点 default_workdir + Agent 工作目录
    （会话 > 节点配置 > Agent 默认的最终生效目录）+ temp 目录 + .env 追加项。
    节点 default_workdir 单独入列：会话目录存在时它不是最终生效目录，
    但它是用户在节点配置里显式指定的目录，写入不应被审批拦截。
    目录可不存在（首次写入前），由调用方 mkdir 前置创建。
    """
    roots: list[Path] = []
    for d in (session_work_dir, node_work_dir, agent_work_dir):
        if d:
            try:
                roots.append(Path(d).resolve())
            except (OSError, RuntimeError):
                continue
    try:
        roots.append(Path(get_temp_dir()).resolve())
    except (OSError, RuntimeError):
        pass
    global _ENV_ROOTS_CACHE
    if _ENV_ROOTS_CACHE is None:
        _ENV_ROOTS_CACHE = _parse_env_roots()
    roots.extend(_ENV_ROOTS_CACHE)
    # 去重保序
    seen: set[str] = set()
    unique: list[Path] = []
    for r in roots:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique
