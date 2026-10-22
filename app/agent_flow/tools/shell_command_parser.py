"""Shell 命令结构化解析器（沙箱 opaque 判定的唯一事实源）

借鉴 opencode（anomalyco/opencode packages/core/src/shell/scan.ts）的设计：
- 把命令拆成子命令序列，供权限层按「子命令粒度」匹配，而非整串正则；
- 解析不了的输入标记为 opaque（附原因），**opaque ≠ 放行**，由调用方
  按 sandbox_opaque_policy 决定 ask/deny/off。

与 opencode 的差异（有意裁剪，不过度承诺）：
- opencode 用手写词法状态机覆盖 bash/zsh/posix 全语法；本项目面向
  Windows cmd（主）+ POSIX sh（次），用 shlex(posix=False) + cmd 元字符
  扫描实现「尽力解析」，解析不完备是设计预期——opaque 兜底转策略。
- opaque 分类名与 opencode 对齐（9 类），便于跨项目对照审计。

方言选择：命令中含 cmd 专属结构（%VAR% / ^ 转义 / cmd 内建命令）按
cmd 解析，否则按 sh 解析。两方言都解析不了才判 opaque。

依赖方向：tools/* → 本模块（无内部依赖，纯函数无 IO）。
消费方：shell_toolkit.validate_command / ssh_toolkit（远端命令同审计）。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

# ---- opaque 原因分类（与 opencode scan.ts 对齐）----
OPAQUE_COMMAND_SUBSTITUTION = "command-substitution"  # $(...) `...`
OPAQUE_COMPOUND_COMMAND = "compound-command"  # if/for/while/case 等复合结构
OPAQUE_DYNAMIC_COMMAND_NAME = "dynamic-command-name"  # 命令名来自变量展开
OPAQUE_DYNAMIC_EXECUTION = "dynamic-execution"  # eval/变量间接执行
OPAQUE_HEREDOC = "heredoc"  # << heredoc
OPAQUE_INVALID_REDIRECT = "invalid-redirect"  # 重定向目标缺失/非法
OPAQUE_INVALID_STRUCTURE = "invalid-structure"  # 括号/引号结构不完整
OPAQUE_UNTERMINATED_ESCAPE = "unterminated-escape"  # 行尾裸转义
OPAQUE_UNTERMINATED_QUOTE = "unterminated-quote"  # 引号未闭合

ALL_OPAQUE_REASONS = frozenset(
    {
        OPAQUE_COMMAND_SUBSTITUTION,
        OPAQUE_COMPOUND_COMMAND,
        OPAQUE_DYNAMIC_COMMAND_NAME,
        OPAQUE_DYNAMIC_EXECUTION,
        OPAQUE_HEREDOC,
        OPAQUE_INVALID_REDIRECT,
        OPAQUE_INVALID_STRUCTURE,
        OPAQUE_UNTERMINATED_ESCAPE,
        OPAQUE_UNTERMINATED_QUOTE,
    }
)

# cmd 内建/常用命令（这些词作为首词时按 cmd 语义处理。
# 注意：只收 cmd 独有词——echo/type/find/sort/more 等两方言共有词不列入，
# 否则 sh 命令被误判走 cmd 路径（cmd 拆分不含 ';'，分号链会漏切——首轮单测实锤））
_CMD_BUILTINS = frozenset(
    {
        "dir",
        "cd",
        "md",
        "mkdir",
        "rd",
        "rmdir",
        "del",
        "erase",
        "copy",
        "xcopy",
        "robocopy",
        "move",
        "ren",
        "rename",
        "type",
        "set",
        "setlocal",
        "endlocal",
        "call",
        "start",
        "title",
        "cls",
        "color",
        "date",
        "time",
        "ver",
        "vol",
        "label",
        "attrib",
        "fc",
        "comp",
        "findstr",
        "tree",
        "where",
        "tasklist",
        "taskkill",
        "sc",
        "net",
        "reg",
        "schtasks",
        "python",
        "node",
        "npm",
        "git",
        "poetry",
        "pip",
        "ping",
        "ipconfig",
        "netstat",
        "systeminfo",
        "whoami",
        "hostname",
        "wmic",
        "assoc",
        "ftype",
        "mklink",
        "subst",
        "doskey",
        "chcp",
        "path",
        "prompt",
        "pushd",
        "popd",
        "exit",
        "pause",
        "rem",
    }
)

# 复合结构关键字（两方言共有；首词命中即 opaque——复合结构语义依赖运行时求值，
# 不做半解析。注意：这些词绝不放入 _CMD_BUILTINS，否则方言误判绕过检测）
_COMPOUND_KEYWORDS = frozenset(
    {
        "for",
        "if",
        "else",
        "not",
        "exist",
        "defined",
        "errorlevel",
        "while",
        "until",
        "case",
        "esac",
        "do",
        "done",
        "then",
        "elif",
        "fi",
        "select",
        "function",
        "coproc",
    }
)

# sh 侧危险/动态执行命令（出现即 opaque：语义依赖运行时求值）
_SH_DYNAMIC_EXECUTION = frozenset({"eval", "exec", "source", "."})

# cmd 侧动态执行形态
_CMD_DYNAMIC_EXECUTION = frozenset({"call"})

# 命令替换检测（sh 语法；cmd 的 %VAR% 是变量展开不是命令替换）
_RE_CMD_SUBSTITUTION = re.compile(r"\$\(|`")
# cmd 变量展开 %VAR% / %VAR:~0,3%（子串展开可构造任意串 → 视为动态）
_RE_CMD_VAR = re.compile(r"%[^%\n]+%")
# 子串展开必须锚定在单个变量内部（变量名是标识符，不含 %/空白/路径字符）：
# %VAR:~0,3% / %VAR:a=b%。旧版 %[^%\n]*:[^%\n]*% 允许跨段乱配对——
# `echo EXIT:%ERRORLEVEL%` 与相邻 %TEMP% 的 % 跨过 ` & echo EXIT:` 误配成
# "子串展开"，安全命令被误判 opaque（vue-tsc 实锤 2026-10-20）
_RE_CMD_VAR_SUBSTR = re.compile(r"%(?!%)[A-Za-z0-9_#]+:[^%\n]*%(?!%)")
# heredoc（sh）
_RE_HEREDOC = re.compile(r"(<{2,})(?!<)")
# 复合结构：由 _has_sh_compound_structure 按**成对关键字**判定（引号遮蔽后），
# 不再用单词正则——单看 for/if/done 会把 `echo done`、python -c 内代码误判 opaque
# cmd 复合结构（for / if 带括号形态）
_RE_CMD_COMPOUND = re.compile(
    r"(?i)(?<![\w-])(for|if)(\s+/[a-z]+)*\s*%[^%\n]*%\s+(?:in|exist|defined|errorlevel|not)\b"
)
# 未闭合引号检测：引号计数（区分单双引号，cmd 与 sh 规则一致——成对才算闭合）
# 行尾续行符（sh 的 \ 与 cmd 的 ^）
_RE_SH_LINE_CONT = re.compile(r"\\\s*$")
_RE_CMD_LINE_CONT = re.compile(r"\^\s*$")
# 重定向（两方言共用符号；cmd 的 < > >> 2> 等）
_RE_REDIRECT = re.compile(r"(?:\d*&?[<>]{1,2}|&>>?|<{1,2})")
# 动态命令名：首词含变量展开（sh $VAR / cmd %VAR%）
_RE_SH_VAR = re.compile(r"\$[{(]?[A-Za-z_][A-Za-z0-9_]*}?")

# cmd 元字符：用于拆分子命令（& && | || ; 换行已在上层禁止）
_RE_CMD_LIST_OPS = re.compile(r"&&|\|\||&|\||;")
_RE_SH_LIST_OPS = re.compile(r"&&|\|\||&|\||;")

# cmd /c 或 /k 链式包装（cmd /c "..." 内层再解析）
_RE_CMD_WRAPPER = re.compile(r"(?i)^\s*cmd(?:\.exe)?\s+/[ck]\s+(.*)$")
# sh 系解释器 -c 包装（sh -c "..." / bash -c "...")：内层须按 sh 语义再解析，
# 否则引号遮蔽会掩盖内层结构（`sh -c "for …; done"` 漏检，2026-10-21 实锤）
_RE_SH_C_WRAPPER = re.compile(r"(?i)^\s*(?:sh|bash|zsh|dash)(?:\.exe)?\s+-c\s+(.+)$")


@dataclass
class ParsedCommand:
    """单个子命令的解析结果"""

    base_cmd: str  # 首词（小写，剥 .exe 后缀）
    words: list[str] = field(default_factory=list)  # 展开后的词序列（尽力）
    raw: str = ""  # 原始子命令串


@dataclass
class ScanResult:
    """解析结果：scanned（含子命令列表）或 opaque（含原因）"""

    kind: str  # "scanned" | "opaque"
    commands: list[ParsedCommand] = field(default_factory=list)
    opaque_reason: str | None = None
    dialect: str = ""  # "cmd" | "sh" | ""（opaque 时可能未判定）
    # scanned 时的重定向写目标（剥引号后的原始串，可能是相对路径）。
    # 仅覆盖 > >> 2> &> 等写方向；2>&1 / < / nul / /dev/null 不入列。
    # 尽力解析：解析失败的命令走 opaque 兜底，不承诺完备。
    redirect_targets: list[str] = field(default_factory=list)


def _looks_like_cmd(command: str) -> bool:
    """启发式方言判定：cmd 专属语法特征。

    注意：echo/type/find 等两方言共有词**不**作为判定依据（否则 sh 命令被
    误判走 cmd 路径，绕过 sh 侧引号/复合检测——首轮单测实锤）。
    """
    if _RE_CMD_VAR.search(command):
        return True
    if re.search(r"\^", command):
        return True
    first_word = command.strip().split()[0].lower() if command.strip() else ""
    if first_word in _CMD_BUILTINS:
        return True
    return False


def _strip_cmd_wrapper(command: str) -> str:
    """剥 cmd /c "..." 包装（一层；嵌套由递归语义自然覆盖——本项目单行命令最多一层）"""
    m = _RE_CMD_WRAPPER.match(command)
    if m:
        inner = m.group(1).strip()
        # cmd /c "quoted" 形态：剥成对外层引号
        if len(inner) >= 2 and inner[0] == '"' and inner[-1] == '"':
            inner = inner[1:-1]
        return inner
    return command


def _mask_quoted(command: str) -> str:
    r"""遮蔽引号内容（保留引号本身，内容替换为等长空格）。

    用于**结构类**检测（复合关键字 for/if/do...、heredoc `<<`）：
    `python -c "…for i in range(3)…"` 里的 for 是 Python 语法，不是 sh
    复合结构；`x << 2` 里的 << 是位移不是 heredoc——结构检测只看引号外
    才不误报（2026-10-21 curl+python -c 实锤）。

    **命令替换 $(…) 与反引号不适用本遮蔽**：sh 双引号内仍会执行，必须
    保持整串检测（_scan_sh 里单独处理）。

    单引号内无转义；双引号内 `\` 转义下一个字符（与 _check_unterminated_quotes 一致）。
    引号未闭合时返回原文（由 opaque 未闭合分支先行拦截）。
    """
    out: list[str] = []
    in_single = in_double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if in_single:
            out.append(ch if ch == "'" else " ")
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\" and i + 1 < len(command):
                out.append("  ")  # 转义对整体遮蔽
                i += 2
                continue
            out.append(ch if ch == '"' else " ")
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        out.append(ch)
        i += 1
    if in_single or in_double:
        return command
    return "".join(out)


def _check_unterminated_quotes(command: str) -> bool:
    """引号未闭合检测：逐字符扫描，引号在字符串内不计数"""
    in_single = in_double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if ch == "\\" and in_double and i + 1 < len(command):
            i += 2  # sh 双引号内转义
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        i += 1
    return in_single or in_double


# 重定向写目标豁免：目标为空设备/FD 复制时不构成文件写入
_REDIRECT_TARGET_EXEMPT = {"nul", "/dev/null", "1", "2"}


# 写方向重定向符号：2>> / &>> / >> / 2> / &> / >（>> 优先于 >；\d*>(?![&\d])
# 排除 2>&1 的 FD 复制形态——其 & 侧目标由豁免集兜住）
_RE_WRITE_REDIRECT = re.compile(r"\d*&?>>|\d*>(?![&\d])")


def _expand_cmd_vars(text: str) -> str:
    """尽力展开 cmd 普通变量 %VAR%（仅用于重定向目标路径解析，不参与安全判定）。

    - 环境变量按大小写不敏感匹配（cmd 语义）；展开不了的（%ERRORLEVEL%
      等运行时变量、含子串语法的）保留字面量，由调用方按 outside 走策略。
    - 安全判定（opaque 检测）仍基于原始命令串——展开只为把 %TEMP%\\x.txt
      解析成真实路径以命中 temp 白名单，避免安全命令误弹审批。
    """
    import os as _os

    def _repl(m: "re.Match[str]") -> str:
        name = m.group(0)[1:-1]  # 剥首尾 %
        val = _os.environ.get(name)
        if val is None:
            # 大小写不敏感兜底
            val = _os.environ.get(name.upper())
        return val if val is not None else m.group(0)

    return _RE_CMD_VAR.sub(_repl, text)


def _extract_redirect_targets(command: str) -> list[str]:
    """提取命令中重定向**写方向**的目标（> >> 2> 2>> &> &>>），尽力解析。

    规则：
    - 符号右侧目标：带引号取到闭引号（支持含空格路径），无引号取到
      空白/shell 分隔符；`>>file` 粘连与 `>> file` 分离都覆盖。
    - `2>&1` 等 FD 复制（目标以 & 开头或落在豁免集）、nul、/dev/null 不入列。
    - 读方向（<）不匹配；解析不出的形态跳过（不阻塞 scanned 判定，
      opaque 兜底另有人管）。
    """
    targets: list[str] = []
    for m in _RE_WRITE_REDIRECT.finditer(command):
        rest = command[m.end():].lstrip()
        if not rest:
            continue
        if rest[0] in ('"', "'"):
            quote = rest[0]
            end = rest.find(quote, 1)
            if end == -1:
                continue  # 引号未闭合由 opaque 检测负责，这里跳过
            target = rest[1:end]
        else:
            mm = re.match(r"[^\s|;&]+", rest)
            if not mm:
                continue
            target = mm.group(0)
        target = target.strip()
        if not target or target.startswith("&"):
            continue
        if target.lower() in _REDIRECT_TARGET_EXEMPT:
            continue
        targets.append(target)
    return targets


def _scan_cmd(command: str) -> ScanResult:
    """cmd 方言解析：拆子命令 → 逐条提取首词"""
    # 0) 复合结构关键字（for/if/do/...）：两方言共有，首词命中即 opaque
    first_word = command.strip().split()[0].lower() if command.strip() else ""
    if first_word in _COMPOUND_KEYWORDS:
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_COMPOUND_COMMAND, dialect="cmd"
        )

    # 0.5) 引号未闭合（cmd 与 sh 规则一致：成对才算闭合）
    if _check_unterminated_quotes(command):
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_UNTERMINATED_QUOTE, dialect="cmd"
        )

    # 1) 动态执行：call + 变量（call %%i 等形态）
    if re.search(r"(?i)\bcall\b\s*%", command):
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_DYNAMIC_EXECUTION, dialect="cmd"
        )

    # 2) 变量子串展开（%VAR:~0,3% 可构造任意串）→ 动态执行
    if _RE_CMD_VAR_SUBSTR.search(command):
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_DYNAMIC_EXECUTION, dialect="cmd"
        )

    # 3) cmd for/if 复合结构（只看引号外：python -c "…for…" 不是 cmd 结构）
    if _RE_CMD_COMPOUND.search(_mask_quoted(command)):
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_COMPOUND_COMMAND, dialect="cmd"
        )

    # 4) 命令替换符号（cmd 不该有 $( / `，出现即结构异常）
    if _RE_CMD_SUBSTITUTION.search(command):
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_COMMAND_SUBSTITUTION, dialect="cmd"
        )

    # 5) 拆子命令并提取首词（拆分前先检测尾随操作符：&& 后无内容属结构不完整）
    # FD 复制保护：2>&1 / 1>&2 的 & 是重定向语法不是列表操作符，拆段前
    # 换占位符防被 _RE_CMD_LIST_OPS 拆成 "… 2>" + "1"（vue-tsc 实锤）。
    _FD_GUARD = "\x00FD"
    guarded = re.sub(r"(\d*)&\s*(\d)", lambda m: f"{m.group(1) or ''}>{_FD_GUARD}{m.group(2)}", command)
    if _RE_CMD_LIST_OPS.search(guarded[-3:] if len(guarded) >= 3 else guarded):
        tail = guarded.rstrip()
        if _RE_CMD_LIST_OPS.fullmatch(tail.split()[-1]) if tail.split() else False:
            return ScanResult(
                kind="opaque", opaque_reason=OPAQUE_INVALID_STRUCTURE, dialect="cmd"
            )
    commands: list[ParsedCommand] = []
    for seg in _RE_CMD_LIST_OPS.split(guarded):
        seg = seg.replace(_FD_GUARD, "&").strip()
        if not seg:
            continue
        # 首词：先按空白切，剥引号与 .exe 后缀
        words = seg.split()
        if not words:
            continue
        base = words[0].strip('"').strip("'")
        if base.lower().endswith(".exe"):
            base = base[:-4]
        # 首词含 %VAR% 展开 → 动态命令名
        if _RE_CMD_VAR.search(base):
            return ScanResult(
                kind="opaque", opaque_reason=OPAQUE_DYNAMIC_COMMAND_NAME, dialect="cmd"
            )
        # 重定向目标缺失（> 或 < 后无内容）
        if _RE_REDIRECT.search(seg) and re.search(r"[<>]{1,2}\s*$", seg):
            return ScanResult(
                kind="opaque", opaque_reason=OPAQUE_INVALID_REDIRECT, dialect="cmd"
            )
        commands.append(ParsedCommand(base_cmd=base.lower(), words=words, raw=seg))

    if not commands:
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_INVALID_STRUCTURE, dialect="cmd"
        )
    return ScanResult(
        kind="scanned",
        commands=commands,
        dialect="cmd",
        redirect_targets=_extract_redirect_targets(command),
    )


def _scan_sh(command: str) -> ScanResult:
    """sh 方言解析：opaque 检测 → shlex 拆词 → 子命令列表"""
    # 1) 命令替换（整串检测：双引号内仍会执行，不可遮蔽）
    if _RE_CMD_SUBSTITUTION.search(command):
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_COMMAND_SUBSTITUTION, dialect="sh"
        )

    # 结构类检测（复合关键字 / heredoc / 行尾续行）只看**引号外**：
    # python -c "…for i in…" 的 for、SQL/正则里的 << 不是 sh 结构（2026-10-21 实锤）
    structural = _mask_quoted(command)

    # 2) heredoc（sh）
    if _RE_HEREDOC.search(structural):
        return ScanResult(kind="opaque", opaque_reason=OPAQUE_HEREDOC, dialect="sh")

    # 3) 复合结构（**成对**判定，只看引号外）：单个 `done`/`then` 是普通参数
    # （`dir /b && echo done`），不能单词命中；python -c "…for i in…" 的 for
    # 在引号内也不算（2026-10-21 实锤）
    if _has_sh_compound_structure(structural):
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_COMPOUND_COMMAND, dialect="sh"
        )

    # 3.5) sh -c "…" 包装：内层引号内容是要执行的脚本，必须剥引号后按 sh
    # 再解析一次——否则遮蔽会把内层结构掩盖成 scanned（安全回退）。
    # 递归一层足够（本项目单行命令，深嵌套内层会因其它特征继续 opaque）
    wrapper = _RE_SH_C_WRAPPER.match(command.strip())
    if wrapper:
        inner = wrapper.group(1).strip()
        if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ('"', "'"):
            inner = inner[1:-1]
        inner_scan = _scan_sh(inner)
        if inner_scan.kind == "opaque":
            return inner_scan

    # 4) 动态执行命令（eval/exec/source/.）
    first_word = command.strip().split()[0].lower() if command.strip() else ""
    if first_word in _SH_DYNAMIC_EXECUTION:
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_DYNAMIC_EXECUTION, dialect="sh"
        )

    # 5) 行尾续行符（多行已被上层硬拒，行尾 \ / ^ 属未终止转义）
    if _RE_SH_LINE_CONT.search(structural) or _RE_CMD_LINE_CONT.search(structural):
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_UNTERMINATED_ESCAPE, dialect="sh"
        )

    # 6) 引号未闭合
    if _check_unterminated_quotes(command):
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_UNTERMINATED_QUOTE, dialect="sh"
        )

    # 7) shlex 拆词（posix=False 保留引号原样，Windows 路径反斜杠不被吞）
    try:
        lexer = shlex.shlex(command, posix=False)
        lexer.whitespace_split = True
        lexer.commenters = ""  # 保留 #（URL/颜色值场景），注释检测交给调用方
        words = list(lexer)
    except ValueError:
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_INVALID_STRUCTURE, dialect="sh"
        )

    if not words:
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_INVALID_STRUCTURE, dialect="sh"
        )

    # 8) 按列表操作符切子命令，逐条提取首词
    # shlex whitespace_split 会把 "a;" 这类「词尾粘连操作符」合成一个词，
    # 需先把每词尾部的粘连操作符剥出来作为独立 token（"&&"/"||" 不会被
    # shlex 粘连——它们两侧必有空白，cmd 亦然；只有单字符 ; & | 会粘连）
    tokens: list[str] = []
    for w in words:
        while w and w[-1] in ";&|" and w not in ("&&", "||", "&", "|", ";"):
            # 词尾单字符操作符粘连（如 "a;" "b|"）；剥出后继续检查剩余尾部
            w, op = w[:-1], w[-1]
            tokens.append(w) if w else None
            tokens.append(op)
            w = ""
        tokens.append(w) if w else None

    commands: list[ParsedCommand] = []
    current: list[str] = []
    for w in tokens:
        if w in ("&&", "||", "&", "|", ";"):
            if current:
                base = current[0].strip('"').strip("'")
                if base.lower().endswith(".exe"):
                    base = base[:-4]
                if _RE_SH_VAR.search(base):
                    return ScanResult(
                        kind="opaque",
                        opaque_reason=OPAQUE_DYNAMIC_COMMAND_NAME,
                        dialect="sh",
                    )
                commands.append(
                    ParsedCommand(
                        base_cmd=base.lower(),
                        words=list(current),
                        raw=" ".join(current),
                    )
                )
                current = []
            else:
                # 操作符前无命令（如 "echo a &&" 的尾段、"&& echo" 的首段）
                return ScanResult(
                    kind="opaque", opaque_reason=OPAQUE_INVALID_STRUCTURE, dialect="sh"
                )
            continue
        current.append(w)
    if current:
        base = current[0].strip('"').strip("'")
        if base.lower().endswith(".exe"):
            base = base[:-4]
        if _RE_SH_VAR.search(base):
            return ScanResult(
                kind="opaque", opaque_reason=OPAQUE_DYNAMIC_COMMAND_NAME, dialect="sh"
            )
        commands.append(
            ParsedCommand(
                base_cmd=base.lower(), words=list(current), raw=" ".join(current)
            )
        )
    else:
        # 尾段为空：最后一个词是操作符（"echo a &&" 形态）→ 结构不完整
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_INVALID_STRUCTURE, dialect="sh"
        )

    if not commands:
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_INVALID_STRUCTURE, dialect="sh"
        )

    # 9) 重定向目标缺失（词序列尾部是重定向符）
    for cmd in commands:
        if cmd.words and _RE_REDIRECT.fullmatch(cmd.words[-1]):
            return ScanResult(
                kind="opaque", opaque_reason=OPAQUE_INVALID_REDIRECT, dialect="sh"
            )

    return ScanResult(
        kind="scanned",
        commands=commands,
        dialect="sh",
        redirect_targets=_extract_redirect_targets(command),
    )


def _has_sh_compound_structure(masked: str) -> bool:
    """判定是否存在**成对**的 sh 复合结构（已遮蔽引号内容）。

    必须成对判定：单个 `done`/`then` 常是普通参数（`dir /b && echo done`），
    只看单词会把安全命令误判 opaque（vue-tsc 命令里就有 `& echo DONE`）。
    成对形态：for…do…done / while…do…done / if…then…fi / case…esac。
    """
    low = masked.lower()

    def has(word: str) -> bool:
        return re.search(rf"(?<![\w-]){word}(?![\w-])", low) is not None

    if has("for") and has("do") and has("done"):
        return True
    if has("while") and has("do") and has("done"):
        return True
    if has("until") and has("do") and has("done"):
        return True
    if has("if") and has("then") and has("fi"):
        return True
    if has("case") and has("esac"):
        return True
    return False


def scan_command(command: str) -> ScanResult:
    """解析单行 shell 命令（唯一入口）。

    方言判定：cmd 专属特征 → cmd 解析；cmd 解析 opaque 且未命中 cmd 特征时
    回退 sh 解析（sh 解析失败才最终 opaque）。

    Args:
        command: 单行命令（多行由调用方提前硬拒）

    Returns:
        ScanResult: kind="scanned"（commands 非空）或 kind="opaque"（opaque_reason 非空）
    """
    if not command or not command.strip():
        return ScanResult(
            kind="opaque", opaque_reason=OPAQUE_INVALID_STRUCTURE, dialect=""
        )

    cmd_candidate = _strip_cmd_wrapper(command.strip())

    if _looks_like_cmd(cmd_candidate):
        result = _scan_cmd(cmd_candidate)
        if result.kind == "scanned":
            # cmd 解析通过，但命令可能含 sh 复合结构（`python -c '…' && for
            # …; do …; done`：python 在 _CMD_BUILTINS 被判 cmd，裸 for 只有
            # sh 侧成对判定能识别）——命中即 opaque，防方言误判漏检
            # （2026-10-21 实锤）。cmd 原生 for /f 形态由 _RE_CMD_COMPOUND 先拦。
            if _has_sh_compound_structure(_mask_quoted(cmd_candidate)):
                return ScanResult(
                    kind="opaque", opaque_reason=OPAQUE_COMPOUND_COMMAND, dialect="sh"
                )
            return result
        # cmd 解析 opaque 但命令不含 cmd 特征 → 回退 sh 再试
        if not (_RE_CMD_VAR.search(cmd_candidate) or re.search(r"\^", cmd_candidate)):
            sh_result = _scan_sh(cmd_candidate)
            if sh_result.kind == "scanned":
                return sh_result
            # 两者都 opaque：cmd 原因优先（更贴近用户意图方言）
            return result
        return result

    return _scan_sh(cmd_candidate)
