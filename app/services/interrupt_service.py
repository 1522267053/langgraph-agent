"""
中断服务模块

管理流程和Agent执行的中断状态。
使用内存字典存储，支持多进程部署时需替换为Redis。

使用场景：
- 前端点击"停止"按钮时，调用 set_interrupted() 设置中断标志
- 后端执行器在关键点检查 is_interrupted() 判断是否需要停止
- 执行完成或被中断后，调用 clear_interrupted() 清理状态
"""

from typing import Dict


class InterruptService:
    """中断服务，管理执行中断状态"""

    def __init__(self):
        self._agent_interrupts: Dict[int, bool] = {}
        self._flow_interrupts: Dict[int, bool] = {}

    def is_agent_interrupted(self, session_id: int) -> bool:
        """检查Agent会话是否被中断"""
        return self._agent_interrupts.get(session_id, False)

    def is_flow_interrupted(self, execution_id: int) -> bool:
        """检查Flow执行是否被中断"""
        return self._flow_interrupts.get(execution_id, False)

    def set_agent_interrupted(self, session_id: int) -> None:
        """设置Agent会话中断标志"""
        self._agent_interrupts[session_id] = True

    def set_flow_interrupted(self, execution_id: int) -> None:
        """设置Flow执行中断标志"""
        self._flow_interrupts[execution_id] = True

    def clear_agent_interrupted(self, session_id: int) -> None:
        """清除Agent会话中断标志"""
        self._agent_interrupts.pop(session_id, None)

    def clear_flow_interrupted(self, execution_id: int) -> None:
        """清除Flow执行中断标志"""
        self._flow_interrupts.pop(execution_id, None)


interrupt_service = InterruptService()
