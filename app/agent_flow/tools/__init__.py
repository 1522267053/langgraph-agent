"""
Agent Flow LLM 工具集

存放以 StructuredTool 形式提供给 LLM 的工具实现模块。
每个工具一个文件（如 file_read.py），共享原语放 common.py。

约定：
- 本目录不参与自动扫描注册（models/node_handlers/ai_provider 专属），全部显式导入
- 工具实现类不持有流程编排逻辑，handler 只负责注册与依赖注入
- 依赖方向：tools/* → app/utils/*，node_handlers/* → tools/*，禁止反向
"""

from app.agent_flow.tools.common import (
    MAX_FILE_SIZE,
    detect_and_read,
    validate_file_path,
    validate_writable_path,
)

__all__ = [
    "MAX_FILE_SIZE",
    "detect_and_read",
    "validate_file_path",
    "validate_writable_path",
]
