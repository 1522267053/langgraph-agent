"""LLM 相关常量（llm_*.py 的单一事实源）

从以下模块提取，避免跨模块反向依赖（如 llm_factory / agent_executor_service
需引用 LLM_MAX_TOKENS 时不必从 llm_tool_handler 取）：
- llm_tool_handler.py：压缩阈值、最大 token、人机交互提示等
- llm_tool_executor.py：计划模式禁用工具、hint 优先级、doom loop 阈值
- llm_stream.py：重试间隔
"""

# ==== llm_tool_handler.py ====

# 自动压缩阈值比例：已用 token 超过 context_length 的此比例时触发压缩
COMPRESS_THRESHOLD_RATIO = 0.83

# 单次执行内 ReAct 尾部的最大压缩次数，超过则终止继续调用工具
MAX_REACT_COMPRESSIONS = 3

# LLM 节点默认最大 token 数（与前端、agent_executor_service、llm_factory 保持一致）
LLM_MAX_TOKENS = 16384


# ==== llm_tool_executor.py ====

# 人工协助工具名（LLM 通过此工具名触发 interrupt）
REQUEST_HUMAN_HELP_TOOL = "request_human_help"

# 计划模式下禁用的工具名前缀（写操作 / 有副作用；python_executor 与 call_sub_agent_
# 的实际注册名带动态后缀 _{node_key}，统一按前缀匹配）
# 注：2026-09-13 起 file_write 单独放开（仅用于让 AI 写入脚本骨架文件，
# 解决多行命令硬拒绝后无法创建 .py 临时文件的死锁问题）；text_editor /
# file_delete 仍保持禁用，避免计划阶段对现有业务代码做局部改写 / 删除。
PLAN_DISABLED_TOOL_PREFIXES = (
    "python_executor",
    "text_editor",
    "file_delete",
    "call_sub_agent_",
)

# system_prompt hint 优先级：值越小越靠前，静态内容放前面有利于 LLM 缓存命中
HINT_PRIORITY: dict[str, int] = {
    "todo": 0,
    "human": 0,
    "knowledge": 1,
    "shell": 1,
    "agenda": 1,
    "ssh": 1,
    "memory": 2,
}

# doom loop 检测（三级策略）：
# - 无参工具（args 为空）：不检测（页面轮询等合法重复场景；总次数由 max_tool_iterations 兜底）
# - 有参工具重复 ≤ SOFT：放行执行，从第 SOFT 次起在 ToolMessage 尾部注入警告（LLM 自我纠正）
# - 有参工具重复 > HARD：跳过执行（真抽风最终闸门）
DOOM_LOOP_SOFT_THRESHOLD = 3
DOOM_LOOP_HARD_THRESHOLD = 8


# ==== llm_stream.py ====

# 重试间隔（秒），最多重试 len(RETRY_DELAYS_SECONDS) 次
RETRY_DELAYS_SECONDS = [2, 4, 8]
