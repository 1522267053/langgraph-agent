r"""系统路径独立授权键（方案A）单测：file_write#system_path 与普通 file_write 授权隔离

设计约束（2026-10-20 实测教训）：**测试绝不能让审批通过后真写系统目录**——
_atomic_write 的 tempfile.mkstemp 在 C:\Windows 下会因权限卡死（faulthandler
栈实锤）。因此所有用例审批回调一律返回 rejected：授权键捕获发生在审批
回调入口（写盘之前），rejected 即可完整验证键名隔离，无需真写。
落盘行为已由真机测试覆盖（白名单内直接成功/拒绝后未创建）。
"""

import pytest

from app.agent_flow.tools.file_toolkit import FileToolsKit
from app.agent_flow.tools.common import check_writable_path


class _CaptureApproval:
    """捕获审批回调参数的假回调（固定 rejected：绝不真写系统目录）"""

    def __init__(self):
        self.calls = []  # [(tool_name, tool_args, content, force_reason)]

    async def __call__(self, tool_name, tool_args, content="", force_reason=None):
        self.calls.append((tool_name, tool_args, content, force_reason))
        return "rejected"


def _make_kit(policy, roots):
    cap = _CaptureApproval()
    kit = FileToolsKit(
        approval_cb=cap,
        base_working_dir=None,
        sandbox_policy=policy,
        writable_roots=roots,
    )
    return kit, cap


class TestSystemPathGrantKeyIsolation:
    @pytest.mark.asyncio
    async def test_system_path_uses_independent_grant_key(self, tmp_path):
        """off 策略下写系统路径：审批工具名必须是 file_write#system_path（非 file_write）

        重构后（2026-10-20）审批只有一个点：_check_write_path 内按路径
        类型决定授权键（系统路径=file_write#system_path，普通=file_write）。
        """
        kit, cap = _make_kit("off", [tmp_path])
        tools = await kit.build_tools()
        fw = next(t for t in tools if t.name == "file_write")
        result = await fw.coroutine(file_path="C:/Windows/evil.txt", content="x")
        # off 策略下系统路径仍触发审批（独立键），rejected 后不落盘
        keys = [c[0] for c in cap.calls]
        assert keys == ["file_write#system_path"]
        assert "系统路径" in (cap.calls[0][3] or "")
        assert "未批准" in str(result)

    @pytest.mark.asyncio
    async def test_normal_outside_uses_plain_tool_name(self, tmp_path):
        """普通白名单外路径：授权键保持原工具名 file_write（与系统路径隔离）"""
        kit, cap = _make_kit("ask", [tmp_path])
        tools = await kit.build_tools()
        fw = next(t for t in tools if t.name == "file_write")
        outside = tmp_path.parent / "elsewhere.txt"
        result = await fw.coroutine(file_path=str(outside), content="x")
        keys = [c[0] for c in cap.calls]
        assert keys == ["file_write"]
        assert "系统路径" not in (cap.calls[0][3] or "")
        assert "未批准" in str(result)

    @pytest.mark.asyncio
    async def test_system_path_ask_policy_also_isolated(self, tmp_path):
        """ask 策略下系统路径同样用独立键（强制审批优先于策略）"""
        kit, cap = _make_kit("ask", [tmp_path])
        tools = await kit.build_tools()
        fw = next(t for t in tools if t.name == "file_write")
        await fw.coroutine(file_path="C:/Program Files/app.ini", content="x")
        keys = [c[0] for c in cap.calls]
        assert keys == ["file_write#system_path"]

    @pytest.mark.asyncio
    async def test_allowed_path_no_approval(self, tmp_path):
        """白名单内路径：零审批直接放行（写入成功）"""
        kit, cap = _make_kit("ask", [tmp_path])
        tools = await kit.build_tools()
        fw = next(t for t in tools if t.name == "file_write")
        target = tmp_path / "ok.txt"
        result = await fw.coroutine(file_path=str(target), content="hello")
        assert len(cap.calls) == 0
        assert "成功" in str(result)
        assert target.exists()

    def test_system_path_verdict_is_outside_with_prefix(self):
        """check_writable_path 系统路径返回 outside + 「系统路径:」前缀（grant_key 判定依据）"""
        verdict, detail = check_writable_path("C:/Windows/x.txt", [])
        assert verdict == "outside"
        assert detail.startswith("系统路径:")
