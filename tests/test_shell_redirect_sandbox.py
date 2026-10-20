"""shell 重定向写目标沙箱校验单测

覆盖三层：
1. 解析器提取（scan_command.redirect_targets / scan_redirect_targets）
2. 决策核心（common.decide_writable_path：ask/deny/off × 系统路径/白名单外）
3. shell_toolkit 闭环（execute_shell 弹审批/拒绝不落盘/白名单内直接执行）

单测纪律：涉及系统路径写入的测试，假审批回调一律返回 rejected——
绝不让审批通过后真写 C:\\Windows。
"""

from pathlib import Path

import pytest

from app.agent_flow.tools.common import decide_writable_path
from app.agent_flow.tools.shell_command_parser import scan_command
from app.agent_flow.tools.shell_toolkit import scan_redirect_targets


class TestRedirectTargetExtraction:
    """解析器提取重定向写目标（scanned 才有；opaque 返回空）"""

    def test_quoted_path_with_spaces(self):
        r = scan_command('echo abc >> "D:/x y/test.txt"')
        assert r.kind == "scanned"
        assert r.redirect_targets == ["D:/x y/test.txt"]

    def test_glued_no_space(self):
        r = scan_command("echo abc>>out.log")
        assert r.kind == "scanned"
        assert r.redirect_targets == ["out.log"]

    def test_single_gt(self):
        r = scan_command("type a > b.txt")
        assert r.redirect_targets == ["b.txt"]

    def test_fd_dup_not_target(self):
        # 2>&1 不构成文件写入
        r = scan_command("type a > b.txt 2>&1")
        assert r.redirect_targets == ["b.txt"]

    def test_nul_exempt(self):
        assert scan_command("echo x > nul").redirect_targets == []
        assert scan_command("echo x > NUL").redirect_targets == []

    def test_dev_null_exempt(self):
        assert scan_command("echo x > /dev/null").redirect_targets == []

    def test_chained_multiple_targets(self):
        r = scan_command("dir > a.txt && echo done > b.txt")
        assert r.redirect_targets == ["a.txt", "b.txt"]

    def test_chinese_path(self):
        r = scan_command("echo ok >> D:\\\\work\\\\项目文档\\\\test.txt")
        assert r.redirect_targets == ["D:\\\\work\\\\项目文档\\\\test.txt"]

    def test_read_redirect_not_included(self):
        # 读方向 < 不入列
        r = scan_command("sort < in.txt")
        assert r.redirect_targets == []

    def test_opaque_returns_empty(self):
        assert scan_redirect_targets("echo $(whoami) > x.txt") == []

    def test_scan_redirect_targets_scanned(self):
        assert scan_redirect_targets("echo hi > out.txt") == ["out.txt"]


class TestCmdVarHandling:
    """cmd %VAR% 处理：子串展开仍 opaque；普通展开不误判；FD 复制不被拆段"""

    def test_substr_expansion_still_opaque(self):
        """%VAR:~0,3% 子串展开可构造任意串 → 仍判 opaque（防误放）"""
        r = scan_command("echo %VAR:~0,3%")
        assert r.kind == "opaque"

    def test_substr_expansion_replace_still_opaque(self):
        r = scan_command("echo %PATH:a=b%")
        assert r.kind == "opaque"

    def test_plain_var_not_substr_false_positive(self):
        """普通 %VAR% 不命中子串正则（vue-tsc 跨段误配回归）"""
        from app.agent_flow.tools.shell_command_parser import _RE_CMD_VAR_SUBSTR

        P = "%"
        cmd = f"echo EXIT:{P}ERRORLEVEL{P} & findstr {P}TEMP{P}"
        assert not _RE_CMD_VAR_SUBSTR.search(cmd)

    def test_fd_dup_not_split_by_list_ops(self):
        """2>&1 的 & 不被当列表操作符拆段（vue-tsc invalid-redirect 回归）"""
        r = scan_command("npx tsc --noEmit > %TEMP%\\a.txt 2>&1 & echo DONE")
        assert r.kind == "scanned"
        assert [c.base_cmd for c in r.commands] == ["npx", "echo"]

    def test_vtsc_command_scanned(self):
        """用户实测命令整串回归：应 scanned 而非 opaque"""
        P = "%"
        Q = '"'
        cmd = (
            f"npx vue-tsc --noEmit > {P}TEMP{P}\\_vtsc2.txt 2>&1 "
            f"& echo EXIT:{P}ERRORLEVEL{P} "
            f"& findstr /C:{Q}error{Q} {P}TEMP{P}\\_vtsc2.txt "
            "& echo DONE && npx eslint src/App.vue && echo LINT_OK"
        )
        r = scan_command(cmd)
        assert r.kind == "scanned"
        assert r.redirect_targets == ["%TEMP%\\_vtsc2.txt"]

    def test_expand_cmd_vars_temp(self):
        from app.agent_flow.tools.shell_command_parser import _expand_cmd_vars

        expanded = _expand_cmd_vars("%TEMP%\\_vtsc2.txt")
        assert expanded.endswith("_vtsc2.txt")
        assert "%" not in expanded

    def test_expand_cmd_vars_unknown_kept_literal(self):
        """运行时变量（%ERRORLEVEL%）无环境值 → 保留字面量"""
        from app.agent_flow.tools.shell_command_parser import _expand_cmd_vars

        assert _expand_cmd_vars("%ERRORLEVEL%") == "%ERRORLEVEL%"

    def test_resolve_targets_temp_in_whitelist(self, tmp_path):
        """%TEMP% 展开后命中 Windows 用户临时目录白名单 → allow 不弹窗"""
        from app.agent_flow.tools.shell_toolkit import resolve_redirect_targets

        P = "%"
        targets = resolve_redirect_targets(
            f"echo x > {P}TEMP{P}\\_probe_test.txt", tmp_path
        )
        assert len(targets) == 1
        assert "%" not in targets[0]

    def test_resolve_targets_relative(self, tmp_path):
        from app.agent_flow.tools.shell_toolkit import resolve_redirect_targets

        targets = resolve_redirect_targets("echo x > rel.txt", tmp_path)
        assert targets == [str(tmp_path / "rel.txt")]

    def test_user_temp_in_writable_roots(self):
        """Windows %TEMP% 目录入可写白名单（vue-tsc 误弹回归）"""
        import os

        from app.agent_flow.tools.sandbox_policy import get_writable_roots

        user_temp = os.environ.get("TEMP") or os.environ.get("TMP")
        if not user_temp:
            pytest.skip("无 TEMP 环境变量")
        roots = get_writable_roots()
        assert Path(user_temp).resolve() in roots


class TestDecideWritablePath:
    """决策核心三态 × 策略矩阵（file_toolkit 与 shell 重定向共用）"""

    def test_allow_inside_root(self, tmp_path):
        d = decide_writable_path(
            "shell_executor", str(tmp_path / "a.txt"), [tmp_path], "ask"
        )
        assert d.action == "allow"

    def test_ask_outside_approve(self, tmp_path):
        d = decide_writable_path(
            "shell_executor", str(tmp_path / "a.txt"), [], "ask"
        )
        assert d.action == "approve"
        assert d.grant_key == "shell_executor"
        assert "白名单" in d.force_reason

    def test_deny_outside_reject(self, tmp_path):
        d = decide_writable_path(
            "shell_executor", str(tmp_path / "a.txt"), [], "deny"
        )
        assert d.action == "reject"
        assert "deny" in d.error

    def test_off_outside_allow(self, tmp_path):
        d = decide_writable_path(
            "shell_executor", str(tmp_path / "a.txt"), [], "off"
        )
        assert d.action == "allow"

    def test_system_path_off_still_approve(self):
        """off 策略下系统路径仍强制审批（独立授权键）"""
        d = decide_writable_path(
            "shell_executor", "C:/Windows/x.txt", None, "off"
        )
        assert d.action == "approve"
        assert d.grant_key == "shell_executor#system_path"
        assert d.force_reason.startswith("写入系统路径")

    def test_system_path_ask_grant_key(self):
        d = decide_writable_path(
            "shell_executor", "C:/Windows/x.txt", None, "ask"
        )
        assert d.action == "approve"
        assert d.grant_key == "shell_executor#system_path"

    def test_blocked_data_dir_any_policy(self):
        from app.config.build_utils import BASE_DIR

        d = decide_writable_path(
            "shell_executor", str(BASE_DIR / "data" / "x.txt"), None, "off"
        )
        assert d.action == "reject"

    def test_none_roots_no_whitelist_check(self, tmp_path):
        """writable_roots=None 兼容旧行为：不做白名单判定"""
        d = decide_writable_path(
            "shell_executor", str(tmp_path / "a.txt"), None, "ask"
        )
        assert d.action == "allow"


class _FakeApproval:
    """假审批回调：记录调用参数，返回预设结果

    仅对强制审批（force_reason 非空）返回预设结果；入口审批钩子
    （force_reason=None，语义是「未命中审批条件」）返回 None 放行——
    与真实 _check_and_request_approval 的命中语义对齐。
    """

    def __init__(self, result="rejected"):
        self.calls: list[tuple[str, str]] = []
        self.result = result

    async def __call__(self, tool_name, tool_args, content, force_reason=None):
        self.calls.append((tool_name, force_reason or ""))
        if force_reason is None:
            return None
        return self.result


class _FakeCfg:
    """最小 cfg：execute_shell 只读 max_duration"""

    max_duration = 0


def _make_kit(tmp_path: Path, policy: str, approval):
    """构造最小 ShellToolKit（不启动真实子进程的路径即可）"""
    from app.agent_flow.tools.shell_toolkit import ShellToolKit

    return ShellToolKit(
        cfg=_FakeCfg(),
        node_key="test_node",
        approval_cb=approval,
        base_working_dir=tmp_path,
        timeout=10,
        async_wait=1,
        system_info="当前系统: Windows",
        resolve_workdir=lambda w, d: tmp_path,
        sandbox_policy=policy,
        writable_roots=[tmp_path],
    )


class TestShellRedirectApprovalFlow:
    """execute_shell 闭环：重定向白名单外 → 审批/拒绝；白名单内 → 直接执行"""

    @pytest.mark.asyncio
    async def test_ask_outside_rejected_no_execute(self, tmp_path):
        """ask 策略 + 白名单外重定向 + 用户拒绝 → 命令不执行"""
        approval = _FakeApproval(result="rejected")
        kit = _make_kit(tmp_path, "ask", approval)
        tools = {t.name: t for t in await kit.build_tools()}
        outside = tmp_path.parent / "outside_target.txt"
        result = await tools["shell_executor"].ainvoke(
            {"command": f"echo x >> \"{outside}\""}
        )
        assert result["success"] is False
        assert result["error_type"] == "approval_rejected"
        # 强制审批回调确实被调用（force_reason 透传；入口钩子记录 force_reason 为空）
        forced = [c for c in approval.calls if c[1]]
        assert len(forced) == 1
        assert "白名单" in forced[0][1]
        # 命令未执行：目标文件不存在
        assert not outside.exists()

    @pytest.mark.asyncio
    async def test_ask_outside_approved_executes(self, tmp_path):
        """ask 策略 + 白名单外重定向 + 批准 → 命令执行落盘"""
        approval = _FakeApproval(result="approved")
        kit = _make_kit(tmp_path, "ask", approval)
        tools = {t.name: t for t in await kit.build_tools()}
        outside = tmp_path.parent / f"outside_{tmp_path.name}.txt"
        result = await tools["shell_executor"].ainvoke(
            {"command": f"echo probe-line >> \"{outside}\""}
        )
        assert result["success"] is True
        assert outside.exists()

    @pytest.mark.asyncio
    async def test_inside_root_no_approval(self, tmp_path):
        """白名单内重定向不弹审批（免打扰）"""
        approval = _FakeApproval(result="rejected")
        kit = _make_kit(tmp_path, "ask", approval)
        tools = {t.name: t for t in await kit.build_tools()}
        result = await tools["shell_executor"].ainvoke(
            {"command": f"echo inner > \"{tmp_path / 'inner.txt'}\""}
        )
        assert result["success"] is True
        # 无强制审批调用（入口钩子的空 force_reason 记录不算）
        assert [c for c in approval.calls if c[1]] == []

    @pytest.mark.asyncio
    async def test_relative_target_resolved_against_cwd(self, tmp_path):
        """相对路径目标按 cwd 解析后判定（cwd 在白名单内 → 放行）"""
        approval = _FakeApproval(result="rejected")
        kit = _make_kit(tmp_path, "ask", approval)
        tools = {t.name: t for t in await kit.build_tools()}
        result = await tools["shell_executor"].ainvoke(
            {"command": "echo rel > rel_target.txt"}
        )
        assert result["success"] is True
        assert (tmp_path / "rel_target.txt").exists()

    @pytest.mark.asyncio
    async def test_system_path_rejected_never_written(self, tmp_path):
        """系统路径重定向：假审批回调返回 rejected，绝不真写 C:\\Windows"""
        approval = _FakeApproval(result="rejected")
        kit = _make_kit(tmp_path, "off", approval)
        tools = {t.name: t for t in await kit.build_tools()}
        result = await tools["shell_executor"].ainvoke(
            {"command": "echo x > C:/Windows/_sandbox_probe_test.txt"}
        )
        assert result["success"] is False
        assert result["error_type"] == "approval_rejected"
        # 独立授权键：shell_executor#system_path
        forced = [c for c in approval.calls if c[1]]
        assert forced[0][0] == "shell_executor#system_path"
        assert not Path("C:/Windows/_sandbox_probe_test.txt").exists()

    @pytest.mark.asyncio
    async def test_off_outside_silent_pass(self, tmp_path):
        """off 策略：白名单外普通路径重定向不弹审批直接执行"""
        approval = _FakeApproval(result="rejected")
        kit = _make_kit(tmp_path, "off", approval)
        tools = {t.name: t for t in await kit.build_tools()}
        outside = tmp_path.parent / f"off_pass_{tmp_path.name}.txt"
        result = await tools["shell_executor"].ainvoke(
            {"command": f"echo off-pass > \"{outside}\""}
        )
        assert result["success"] is True
        assert [c for c in approval.calls if c[1]] == []
        assert outside.exists()
