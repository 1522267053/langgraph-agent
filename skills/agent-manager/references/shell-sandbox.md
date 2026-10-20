# Shell 软件层沙箱（命令解析 + 写路径白名单）

LLM 通过 `shell_executor` 执行的命令受**软件层沙箱**约束（2026-10-20 起，无 Docker 依赖）。
本文说明：策略配置、命令三态判定、可写白名单、审批分流与授权键。
实现唯一真源：`app/agent_flow/tools/`（`shell_command_parser.py` / `sandbox_policy.py` / `common.py` / `shell_toolkit.py`）；改动约定见项目根 `AGENTS.md` 沙箱小节。

> 与 [Python 沙箱与文件](python-sandbox-and-files.md) 的区别：那篇讲 Python 节点的
> RestrictedPython 代码沙箱（模块白名单/AST 限制）；本文讲 shell 命令与文件写入的
> 路径沙箱。两套机制独立，互不复用。

## 1. 策略配置：`sandbox_opaque_policy`

LLM 节点 `base_config` 字段，四选一（默认 `auto`）：

| 值 | Agent 会话 | Flow 执行 | 说明 |
|---|---|---|---|
| `auto`（默认） | **ask**（弹审批） | **off**（不拦截） | 按运行域分流：`_session_id > 0` = Agent，`_execution_id > 0` = Flow |
| `ask` | 弹审批 | 弹审批 | 显式收紧（Flow 定时任务中 LLM 动态执行命令建议改此值） |
| `deny` | 拒绝 | 拒绝 | 无法判定/白名单外一律拒绝 |
| `off` | 不拦截 | 不拦截 | 显式放开 |

**「不拦截」语义（重要）**：`off` 与 auto-Flow 只是**跳过审批确认**，黑名单命令与
硬拦截路径仍然生效——「放行」仅指不弹窗，不是跳过安全检查。

策略经 `setup_tool_handlers` 注入 `handler._sandbox_policy`，shell/ssh/file 三 toolkit
闭包消费；解析唯一真源 `sandbox_policy.resolve_opaque_policy`（显式配置 > auto 分域）。

## 2. 命令三态判定（check_command）

`shell_toolkit.check_command` 返回三态：

| verdict | 含义 | 后续 |
|---|---|---|
| `valid` | 结构化解析通过且无危险特征 | 直接执行（重定向写目标仍走白名单校验，见 §4） |
| `blocked` | 命中黑名单/危险模式/DB 写保护 | 直接拒绝，不走审批 |
| `opaque` | 解析器无法判定安全性 | 按 `sandbox_opaque_policy` 分流（ask 弹窗 / deny 拒绝 / off 放行） |

**opaque 分类**（对齐 opencode，cmd/sh 双方言尽力解析，不承诺完备）：
`command-substitution`（`$(...)`/反引号）、`compound-command`（for/if/do…）、
`dynamic-command-name`（首词含变量）、`dynamic-execution`（eval/call %VAR%/子串展开）、
`heredoc`、`invalid-redirect`（重定向目标缺失）、`invalid-structure`、
`unterminated-escape`、`unterminated-quote`。

**黑名单始终优先于 opaque**：`DANGEROUS_PATTERNS` 整串正则兜底扫描对 scanned 与
opaque 一视同仁——危险命令即使解析失败也先硬拦截（`format C:`、`rm -rf /`、
powershell 宿主、system32 整串等）。

`validate_command` 是二态兼容包装（opaque 视为放行），仅供旧调用方；新代码一律用
`check_command`。

## 3. 可写白名单（get_writable_roots）

文件写入（file_write / text_editor / file_delete / shell 重定向 / python open 写模式）
统一按白名单判定，默认 6 类来源：

1. 会话工作路径（聊天页用户选择）
2. 节点 `default_workdir`（即使被会话目录覆盖也单独入列——用户显式配置不拦截）
3. Agent 工作目录（`workspace/agents/{id}`）
4. 项目临时目录（`workspace/temp`）
5. Windows 用户临时目录（`%TEMP%`/`%TMP%`，展开后命中）
6. `.env` 的 `SANDBOX_WRITABLE_ROOTS` 追加项（逗号/分号分隔，相对路径基于项目根）

**硬拦截（任何策略下拒绝，读写都拦，白名单不可覆盖）**：
- 路径穿越（`..`）
- `data/` 数据目录（SQLite/向量库）
- 家目录敏感子目录：`.ssh` / `.aws` / `.docker` / `.gnupg` / `.netrc` / `.kube` / `.config/gcloud`（读私钥=泄漏）

**系统关键路径读写分离**（`FORBIDDEN_PATH_PATTERNS`：`C:\Windows`、Program Files、
`/etc`、`/usr` 等）：**读放开**（诊断需要）；**写走强制审批**——无视策略（off 也不
静默放行），使用独立授权键（见 §5）。

## 4. shell 重定向写校验（堵 `echo >>` 绕过通道）

`file_write` 写白名单外会弹审批，但 `echo x >> "D:\其他目录\f.txt"` 这类重定向
同样落盘——已纳入同一套校验：

1. 解析器提取 scanned 命令的**重定向写目标**（`> >> 2> &>`；`2>&1` FD 复制、
   `nul`、`/dev/null` 豁免；带引号含空格路径、`>>file` 粘连形态均支持）
2. cmd 普通变量**尽力展开**（`%TEMP%\x.txt` → 真实路径使命中白名单；`%ERRORLEVEL%`
   等运行时变量展开不了保留字面量 → 按白名单外走策略）。展开仅用于路径判定，
   不参与安全判定；`%VAR:~0,3%` 子串展开仍判 opaque
3. 相对路径按本次命令 cwd 解析为绝对路径
4. 走 `decide_writable_path` 同一决策（§5）；系统路径用
   `shell_executor#system_path` 独立授权键

解析失败（opaque）不在此层拦——由 opaque 策略分流负责，两层各司其职。

## 5. 写路径决策唯一真源（decide_writable_path）

`common.decide_writable_path(tool_name, file_path, writable_roots, sandbox_policy)`
返回 `WritePathDecision`，file_toolkit 与 shell 重定向共用，保证两条通道审批语义
完全一致：

| 判定 | 决策 |
|---|---|
| allowed（白名单内） | allow 直接放行 |
| blocked（穿越/data//敏感家目录） | reject，任何策略下拒绝 |
| outside + 系统路径 | **approve 强制审批**，授权键 `{tool}#system_path`（off 也不静默） |
| outside + 普通路径 | ask → approve / deny → reject / off → allow |

**审批回调调用留在各 toolkit**（`_check_write_path` / execute_shell），决策函数不持
回调——维持 tools 内依赖方向（tools 之间禁止互引）。

**file 工具审批唯一入口（强制）**：file_write/text_editor/file_delete 的审批只发生
在 `_check_write_path` 内，禁止闭包入口再放一次（双重审批会入口 rejected 短路跳过
策略审批 + 重复弹窗）；text_editor `dry_run` 只读不审批。

## 6. 授权键与免审授权的关系

- 普通白名单外写入：授权键 = 工具名（如 `file_write`），与 `approval_required_tools`
  的「批准并记住」共用 `session_tool_grant`（scope + session_id + tool_name 唯一）
- **系统路径写入：授权键 = `{tool}#system_path`**（如 `file_write#system_path`），
  与普通工具名授权**相互隔离**——用户曾对普通目录「批准并记住」后，系统路径写入
  仍会单独问一次；对系统路径键记住后，装环境连写多个系统文件只问一次
- shell 重定向的系统路径写用 `shell_executor#system_path`，与 `file_write#system_path`
  也相互隔离（按工具名前缀区分）

## 7. python 沙箱 open 收口

`python_handler._sandbox_open` 替换裸 `open`：写模式（w/a/x/+）路径必须落在可写
白名单内，读模式不限制；系统路径写拒绝（python 沙箱无审批交互通道，错误信息引导
改用 file_write/shell）。ALLOWED_MODULES 单一事实源不受影响。

## 8. 排查清单

- [ ] 安全命令被弹审批 → 看判定链：opaque（哪类原因）还是重定向目标 outside；
      `%VAR%` 路径确认展开后是否命中白名单（%TEMP% 已入列）
- [ ] `echo x >> 白名单外路径` 没弹窗 → 确认节点策略（off/auto-Flow 不弹是预期）；
      Agent 域 auto 应弹，不弹则是缺陷
- [ ] 写系统路径没弹窗 → 确认授权键是否 `#system_path`（普通记住不覆盖系统路径）
- [ ] 白名单想加目录 → `.env` 的 `SANDBOX_WRITABLE_ROOTS`（改后重启）
- [ ] Flow 想收紧 → LLM 节点 `sandbox_opaque_policy` 显式改 `ask`/`deny`
- [ ] 命令被拒但看不出原因 → 返回的 `error` 带 opaque 原因或 blocked 模式，
      改写为结构简单的单行命令通常可过
