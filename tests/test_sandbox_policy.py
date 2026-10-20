"""sandbox_policy / check_command / check_writable_path 分域策略单测"""

from pathlib import Path

import pytest

from app.agent_flow.tools.sandbox_policy import (
    POLICY_ASK,
    POLICY_DENY,
    POLICY_OFF,
    get_writable_roots,
    resolve_opaque_policy,
)


class TestResolveOpaquePolicy:
    """分域策略解析：节点显式配置 > auto 按域分流"""

    def test_auto_agent_ask(self):
        assert resolve_opaque_policy("auto", session_id=1, execution_id=0) == POLICY_ASK

    def test_auto_flow_off(self):
        assert resolve_opaque_policy("auto", session_id=0, execution_id=1) == POLICY_OFF

    def test_auto_none_agent(self):
        assert resolve_opaque_policy(None, session_id=1, execution_id=0) == POLICY_ASK

    def test_auto_none_flow(self):
        assert resolve_opaque_policy(None, session_id=0, execution_id=1) == POLICY_OFF

    def test_explicit_ask_overrides_domain(self):
        # Flow 域显式 ask 也生效（用户主动收紧）
        assert resolve_opaque_policy("ask", session_id=0, execution_id=1) == POLICY_ASK

    def test_explicit_deny_overrides_domain(self):
        assert resolve_opaque_policy("deny", session_id=1, execution_id=0) == POLICY_DENY

    def test_explicit_off_overrides_domain(self):
        assert resolve_opaque_policy("off", session_id=1, execution_id=0) == POLICY_OFF

    def test_invalid_value_falls_back_to_domain(self):
        assert resolve_opaque_policy("hack", session_id=1, execution_id=0) == POLICY_ASK
        assert resolve_opaque_policy("", session_id=0, execution_id=1) == POLICY_OFF

    def test_case_insensitive(self):
        assert resolve_opaque_policy("ASK", session_id=0, execution_id=1) == POLICY_ASK


class TestCheckCommandThreeState:
    """check_command 三态（黑名单优先于 opaque）"""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.agent_flow.tools.shell_toolkit import check_command

        self.check = check_command

    def test_valid_simple(self):
        verdict, _ = self.check("dir /b")
        assert verdict == "valid"

    def test_blocked_dangerous_pattern(self):
        verdict, detail = self.check("echo hi && format C:")
        assert verdict == "blocked"
        assert "危险" in detail

    def test_blocked_chained_command(self):
        # 链式调用中的禁止命令（子命令粒度匹配）
        verdict, detail = self.check("echo start && diskpart")
        assert verdict == "blocked"

    def test_opaque_command_substitution(self):
        verdict, detail = self.check("echo $(whoami)")
        assert verdict == "opaque"
        assert detail == "command-substitution"

    def test_opaque_dynamic_execution(self):
        verdict, _ = self.check("eval $CMD")
        assert verdict == "opaque"

    def test_dangerous_takes_precedence_over_opaque(self):
        # 危险模式即使解析 opaque 也先硬拦截
        verdict, _ = self.check("format C: && echo $(x)")
        assert verdict == "blocked"


class TestCheckWritablePath:
    """check_writable_path 三态：blocked 硬拦截 > outside 白名单外 > allowed"""

    def test_allowed_when_no_roots(self, tmp_path):
        from app.agent_flow.tools.common import check_writable_path

        verdict, _ = check_writable_path(str(tmp_path / "a.txt"), None)
        assert verdict == "allowed"

    def test_outside_when_roots_provided(self, tmp_path):
        from app.agent_flow.tools.common import check_writable_path

        verdict, detail = check_writable_path(str(tmp_path / "a.txt"), [])
        assert verdict == "outside"
        assert detail == str(tmp_path / "a.txt")

    def test_allowed_inside_root(self, tmp_path):
        from app.agent_flow.tools.common import check_writable_path

        verdict, _ = check_writable_path(str(tmp_path / "a.txt"), [tmp_path])
        assert verdict == "allowed"

    def test_system_path_now_outside_not_blocked(self):
        """系统路径写不再硬拦，归 outside 由调用方强制审批（读已放开）"""
        from app.agent_flow.tools.common import check_writable_path

        verdict, detail = check_writable_path("C:/Windows/x.txt", None)
        assert verdict == "outside"
        assert detail.startswith("系统路径:")

    def test_system_path_read_allowed(self):
        """读系统路径放开（诊断需要读 C:\\Windows 日志/配置）"""
        from app.agent_flow.tools.common import validate_file_path

        is_valid, _ = validate_file_path("C:/Windows/win.ini")
        assert is_valid

    def test_sensitive_home_read_still_blocked(self):
        """家目录敏感子目录读写都拦（读 .ssh 私钥=泄漏）"""
        import os

        from app.agent_flow.tools.common import validate_file_path

        home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        if not home:
            pytest.skip("无家目录环境变量")
        is_valid, detail = validate_file_path(f"{home}/.ssh/id_rsa")
        assert not is_valid
        assert "敏感" in detail

    def test_blocked_data_dir(self):
        from app.agent_flow.tools.common import check_writable_path
        from app.config.build_utils import BASE_DIR

        verdict, _ = check_writable_path(str(BASE_DIR / "data" / "x.txt"), None)
        assert verdict == "blocked"

    def test_blocked_sensitive_home_subdir(self):
        import os

        from app.agent_flow.tools.common import check_writable_path

        home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        if not home:
            pytest.skip("无家目录环境变量")
        verdict, detail = check_writable_path(f"{home}/.ssh/id_rsa", None)
        assert verdict == "blocked"
        assert "敏感" in detail

    def test_allowed_normal_home_subdir(self):
        """家目录非敏感子目录不被硬拦截（仅白名单判定）"""
        import os

        from app.agent_flow.tools.common import check_writable_path

        home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        if not home:
            pytest.skip("无家目录环境变量")
        verdict, _ = check_writable_path(f"{home}/Documents/x.txt", None)
        assert verdict == "allowed"


class TestGetWritableRoots:
    def test_contains_temp_dir(self):
        from app.config.build_utils import get_temp_dir

        roots = get_writable_roots()
        assert Path(get_temp_dir()).resolve() in roots

    def test_session_dir_included(self, tmp_path):
        roots = get_writable_roots(session_work_dir=tmp_path)
        assert tmp_path.resolve() in roots

    def test_node_work_dir_included_alongside_session(self, tmp_path):
        """会话目录存在时节点 default_workdir 仍单独入列（用户显式配置不拦截）"""
        session_dir = tmp_path / "session"
        node_dir = tmp_path / "nodecfg"
        roots = get_writable_roots(
            session_work_dir=session_dir, node_work_dir=node_dir
        )
        assert session_dir.resolve() in roots
        assert node_dir.resolve() in roots

    def test_dedup(self, tmp_path):
        roots = get_writable_roots(session_work_dir=tmp_path, agent_work_dir=tmp_path)
        assert roots.count(tmp_path.resolve()) == 1
