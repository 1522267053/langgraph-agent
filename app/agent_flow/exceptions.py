"""
人工协助异常和处理
"""


class ToolExecutionException(Exception):
    """工具执行异常"""

    def __init__(self, tool_name: str, error_message: str):
        self.tool_name = tool_name
        self.error_message = error_message
        super().__init__(f"工具 {tool_name} 执行失败: {error_message}")


class MaxIterationsExceededException(Exception):
    """超过最大迭代次数异常"""

    def __init__(self, max_iterations: int):
        self.max_iterations = max_iterations
        super().__init__(f"超过最大工具调用迭代次数: {max_iterations}")


class FlowValidationError(Exception):
    """流程验证错误（不可恢复）"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NodeExecutionError(Exception):
    """节点执行错误（可恢复）"""

    def __init__(self, node_key: str, message: str):
        self.node_key = node_key
        self.message = message
        super().__init__(f"节点 {node_key} 执行失败: {message}")


class CheckpointError(Exception):
    """Checkpoint 相关错误"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
