r"""list_files / file_search include 花括号展开（bash 语义）测试

背景（2026-10-22）：LLM 按 bash 习惯传 {README.md,AGENTS.md}，Python
pathlib.Path.glob 不支持花括号 → 字面匹配永远零命中。修复口径：
list_files pattern 与 file_search include 在 glob/fnmatch 前先做
bash 语义花括号展开（_expand_brace_pattern / _split_include_globs，
两条搜索路径共用，保证行为一致）。

造数纪律：tmp_path 真实建文件（复制真实结构），不手写字段；list_files/
file_search 均为只读工具，不涉及 DB。
"""

import pytest

from app.agent_flow.tools.file_toolkit import (
    FileToolsKit,
    ListFilesInput,
    _expand_brace_pattern,
    _split_include_globs,
)


class _NoApproval:
    """list_files/file_search 为只读工具，审批回调不应被触发"""

    async def __call__(self, tool_name, tool_args, content="", force_reason=None):
        raise AssertionError(f"只读工具不应触发审批: {tool_name}")


def _make_kit(tmp_path):
    return FileToolsKit(
        approval_cb=_NoApproval(),
        base_working_dir=tmp_path,
        sandbox_policy="off",
        writable_roots=[tmp_path],
    )


async def _get_tool(tmp_path, name):
    kit = _make_kit(tmp_path)
    tools = await kit.build_tools()
    return next(t for t in tools if t.name == name)


# ---- _expand_brace_pattern 纯函数单测 ----


class TestExpandBracePattern:
    @pytest.mark.parametrize(
        "pattern,expected",
        [
            # 基本展开
            ("{a,b}", ["a", "b"]),
            ("{a,b,c}", ["a", "b", "c"]),
            ("*.{md,txt}", ["*.md", "*.txt"]),
            ("{README.md,AGENTS.md}", ["README.md", "AGENTS.md"]),
            # 无花括号：原样返回单元素（行为与直接 glob 一致）
            ("*.py", ["*.py"]),
            ("", [""]),
            # 前后缀保留
            ("src/{a,b}/*.py", ["src/a/*.py", "src/b/*.py"]),
            ("**/{x,y}.ts", ["**/x.ts", "**/y.ts"]),
            # 空分支（bash 语义：允许，产生空替换）
            ("a{,x}b", ["ab", "axb"]),
            # 嵌套花括号（笛卡尔积）
            ("{a,b}{1,2}", ["a1", "a2", "b1", "b2"]),
            ("{a,{b,c}}", ["a", "b", "c"]),
            ("{a,{b,c}}.py", ["a.py", "b.py", "c.py"]),
            # bash 语义：无顶层逗号不展开（文件名可能真含花括号）
            ("{a}", ["{a}"]),
            ("{}", ["{}"]),
            ("{.ssh}", ["{.ssh}"]),
            # 不配对花括号：字面处理（与 glob 字面语义一致）
            ("{a,b", ["{a,b"]),
            ("a}b", ["a}b"]),
        ],
    )
    def test_expand(self, pattern, expected):
        assert _expand_brace_pattern(pattern) == expected

    def test_expansion_limit_raises(self):
        # 64 分支上限：33×2=66 > 64 → ValueError
        pattern = "{" + ",".join(str(i) for i in range(33)) + "}{a,b}"
        with pytest.raises(ValueError, match="花括号展开超过"):
            _expand_brace_pattern(pattern)

    def test_expansion_at_limit_ok(self):
        # 恰好 64 分支不抛（上限语义：>64 才拒绝）
        pattern = "{" + ",".join(str(i) for i in range(32)) + "}{a,b}"
        assert len(_expand_brace_pattern(pattern)) == 64


# ---- _split_include_globs 纯函数单测 ----


class TestSplitIncludeGlobs:
    @pytest.mark.parametrize(
        "include,expected",
        [
            ("*.py", ["*.py"]),
            ("*.py, *.md", ["*.py", "*.md"]),
            ("*.{ts,tsx}", ["*.ts", "*.tsx"]),
            ("*.py, *.{ts,tsx}", ["*.py", "*.ts", "*.tsx"]),
            (" , *.py ", ["*.py"]),  # 空片段跳过
            ("", []),
        ],
    )
    def test_split(self, include, expected):
        assert _split_include_globs(include) == expected

    def test_split_limit_propagates(self):
        # 33×2=66 > 64 上限，ValueError 穿透
        pattern = "{" + ",".join(str(i) for i in range(33)) + "}{a,b}"
        with pytest.raises(ValueError):
            _split_include_globs(pattern)


# ---- list_files 集成用例（真实文件造数）----


class TestListFilesBrace:
    @pytest.mark.asyncio
    async def test_brace_matches_two_files(self, tmp_path):
        """用户实踩场景：{README.md,AGENTS.md} 命中两文件（修复前零命中）"""
        (tmp_path / "README.md").write_text("# r", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("# a", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("# c", encoding="utf-8")

        lf = await _get_tool(tmp_path, "list_files")
        result = await lf.coroutine(path=str(tmp_path), pattern="{README.md,AGENTS.md}")
        assert "Found 2 files" in result
        assert "README.md" in result
        assert "AGENTS.md" in result
        assert "CLAUDE.md" not in result

    @pytest.mark.asyncio
    async def test_brace_with_recursive_prefix(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "src" / "b.ts").write_text("let x", encoding="utf-8")

        lf = await _get_tool(tmp_path, "list_files")
        result = await lf.coroutine(path=str(tmp_path), pattern="**/*.{py,ts}")
        assert "Found 2 files" in result
        assert "src/a.py" in result.replace("\\", "/")
        assert "src/b.ts" in result.replace("\\", "/")

    @pytest.mark.asyncio
    async def test_dedup_across_patterns(self, tmp_path):
        """{*.md,README.md} 双模式命中同一文件只报一次"""
        (tmp_path / "README.md").write_text("# r", encoding="utf-8")

        lf = await _get_tool(tmp_path, "list_files")
        result = await lf.coroutine(path=str(tmp_path), pattern="{*.md,README.md}")
        assert "Found 1 files" in result

    @pytest.mark.asyncio
    async def test_no_match_reports_original_pattern(self, tmp_path):
        (tmp_path / "x.txt").write_text("x", encoding="utf-8")

        lf = await _get_tool(tmp_path, "list_files")
        result = await lf.coroutine(path=str(tmp_path), pattern="{nope.md,missing.txt}")
        assert "No files matched pattern: {nope.md,missing.txt}" in result

    @pytest.mark.asyncio
    async def test_plain_pattern_unchanged(self, tmp_path):
        """无花括号模式行为与原实现完全一致（回归保护）"""
        (tmp_path / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "b.md").write_text("b", encoding="utf-8")
        (tmp_path / "c.txt").write_text("c", encoding="utf-8")

        lf = await _get_tool(tmp_path, "list_files")
        result = await lf.coroutine(path=str(tmp_path), pattern="*.md")
        assert "Found 2 files" in result

    @pytest.mark.asyncio
    async def test_expansion_limit_returns_readable_error(self, tmp_path):
        """展开爆炸：返回可读错误而非异常穿透"""
        lf = await _get_tool(tmp_path, "list_files")
        big = "{" + ",".join(str(i) for i in range(33)) + "}{a,b}"
        result = await lf.coroutine(path=str(tmp_path), pattern=big)
        assert "模式展开失败" in result
        assert "花括号展开超过" in result

    @pytest.mark.asyncio
    async def test_single_brace_literal_passthrough(self, tmp_path):
        """{a} 单元素不展开：按字面 glob（文件名真含花括号的场景）"""
        (tmp_path / "{odd}.txt").write_text("x", encoding="utf-8")

        lf = await _get_tool(tmp_path, "list_files")
        result = await lf.coroutine(path=str(tmp_path), pattern="{odd}.txt")
        assert "Found 1 files" in result
        assert "{odd}.txt" in result

    @pytest.mark.asyncio
    async def test_bad_char_class_no_crash(self, tmp_path):
        """坏字符类分支：实测 Python 3.12 glob 对不配对 '[' 零命中不抛异常
        （2026-10-22 探针取证），坏分支不影响好分支命中"""
        (tmp_path / "ok.txt").write_text("x", encoding="utf-8")

        lf = await _get_tool(tmp_path, "list_files")
        # ok.txt 命中；[zz] 是合法字符类（匹配 z 文件）零命中 → 并集 1 个
        result = await lf.coroutine(path=str(tmp_path), pattern="{ok.txt,[zz]}")
        assert "Found 1 files" in result
        assert "ok.txt" in result

    @pytest.mark.asyncio
    async def test_unbalanced_brace_literal_no_match(self, tmp_path):
        """不配对花括号（{ok.txt,[）：整体字面 glob → 零命中，
        报告携带原始模式（与 bash 字面语义一致）"""
        (tmp_path / "ok.txt").write_text("x", encoding="utf-8")

        lf = await _get_tool(tmp_path, "list_files")
        result = await lf.coroutine(path=str(tmp_path), pattern="{ok.txt,[")
        assert "No files matched pattern: {ok.txt,[" in result

    @pytest.mark.asyncio
    async def test_schema_default_and_description(self):
        """schema 描述单源：pattern 描述已声明花括号支持（LLM 可见）"""
        desc = ListFilesInput.model_fields["pattern"].description
        assert "花括号" in desc
        assert "{a,b}" in desc


# ---- file_search include 花括号（两条路径行为一致）----


class TestFileSearchIncludeBrace:
    @pytest.mark.asyncio
    async def test_include_brace_python_fallback(self, tmp_path, monkeypatch):
        """Python 回退路径：include 花括号展开生效（修复前 fnmatch 不认花括号）"""
        (tmp_path / "a.py").write_text("needle_py = 1\n", encoding="utf-8")
        (tmp_path / "b.ts").write_text("needle_ts = 2\n", encoding="utf-8")
        (tmp_path / "c.md").write_text("needle_md\n", encoding="utf-8")

        monkeypatch.setattr(
            "app.agent_flow.tools.file_toolkit.shutil.which", lambda _: None
        )
        fs = await _get_tool(tmp_path, "file_search")
        result = await fs.coroutine(
            pattern="needle_", path=str(tmp_path), include="*.{py,ts}"
        )
        assert "Found 2 matches" in result
        assert "a.py" in result
        assert "b.ts" in result
        assert "c.md" not in result

    @pytest.mark.asyncio
    async def test_include_comma_and_brace_mixed(self, tmp_path, monkeypatch):
        """逗号 + 花括号混用：*.py, *.{md,ts} 三叶子"""
        (tmp_path / "a.py").write_text("n = 1\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("n\n", encoding="utf-8")
        (tmp_path / "c.ts").write_text("n\n", encoding="utf-8")
        (tmp_path / "d.json").write_text("1\n", encoding="utf-8")

        monkeypatch.setattr(
            "app.agent_flow.tools.file_toolkit.shutil.which", lambda _: None
        )
        fs = await _get_tool(tmp_path, "file_search")
        result = await fs.coroutine(
            pattern="n", path=str(tmp_path), include="*.py, *.{md,ts}"
        )
        assert "Found 3 matches" in result
        assert "d.json" not in result

    @pytest.mark.asyncio
    async def test_ripgrep_path_consistent_with_fallback(self, tmp_path):
        """ripgrep 可用时：include 花括号展开后过滤行为与回退路径一致"""
        rg_available = pytest.importorskip("shutil")  # noqa: F841
        import shutil as _shutil

        if not _shutil.which("rg"):
            pytest.skip("ripgrep 未安装")

        (tmp_path / "a.py").write_text("needle = 1\n", encoding="utf-8")
        (tmp_path / "b.ts").write_text("needle = 2\n", encoding="utf-8")
        (tmp_path / "c.md").write_text("needle\n", encoding="utf-8")

        fs = await _get_tool(tmp_path, "file_search")
        result = await fs.coroutine(
            pattern="needle", path=str(tmp_path), include="*.{py,ts}"
        )
        assert "Found 2 matches" in result
        assert "a.py" in result
        assert "b.ts" in result
        assert "c.md" not in result
