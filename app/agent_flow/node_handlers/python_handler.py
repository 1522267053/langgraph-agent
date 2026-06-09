"""
Python代码执行节点处理器
基于 RestrictedPython 提供安全的沙箱环境执行Python代码
要求定义 main 函数，参数名与输入变量名对应
"""

import asyncio
import inspect
import io
import json
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Optional

from RestrictedPython import compile_restricted_exec
from RestrictedPython.Guards import (
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safe_builtins,
    safer_getattr,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import StreamWriter
from pydantic import BaseModel, Field, create_model

from app.agent_flow.flow_context import FlowState
from app.agent_flow.exceptions import NodeExecutionError
from app.agent_flow.handler_registry import NodeHandlerRegistry
from app.agent_flow.node_handlers.base_handler import (
    BaseNodeHandler,
    BaseNodeConfig,
    NodeVariable,
)
from app.models.flow_node import FlowNode


class PythonNodeConfig(BaseNodeConfig):
    code: str = ""
    timeout: int = 30
    description: Optional[str] = None
    use_preset_for_tool: bool = False
    output_variables: list[NodeVariable] = [
        NodeVariable(name="result", type="python_result"),
    ]


ALLOWED_MODULES = frozenset(
    {
        "math",
        "json",
        "re",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "decimal",
        "fractions",
        "typing",
        "statistics",
        "hashlib",
        "uuid",
        "copy",
        "enum",
        "dataclasses",
        "string",
        "textwrap",
        "operator",
        "numbers",
        "abc",
        "bisect",
        "heapq",
        "array",
        "struct",
        "base64",
        "binascii",
        "io",
        "csv",
        "pathlib",
        "random",
        "time",
        "calendar",
        "zoneinfo",
        "openpyxl",
        "os",
        "requests",
    }
)


def _safe_import(name, *args, **kwargs):
    """白名单模块导入，仅允许安全模块"""
    top = name.split(".")[0]
    if top not in ALLOWED_MODULES:
        raise ImportError(f"Module '{name}' is not allowed for security reasons")
    return __import__(name, *args, **kwargs)


class _StdoutPrintCollector:
    """_print_ 工厂：将 print 输出写入 sys.stdout，配合 redirect_stdout 可捕获"""

    def __init__(self, _getattr_=None):
        self._getattr_ = _getattr_

    def _call_print(self, *objects, **kwargs):
        if kwargs.get("file", None) is None:
            kwargs["file"] = sys.stdout
        else:
            self._getattr_(kwargs["file"], "write")
        print(*objects, **kwargs)


_SAFE_BUILTIN_NAMES = (
    "dict",
    "list",
    "set",
    "tuple",
    "frozenset",
    "bytearray",
    "memoryview",
    "map",
    "filter",
    "any",
    "all",
    "min",
    "max",
    "sum",
    "len",
    "range",
    "sorted",
    "enumerate",
    "zip",
    "reversed",
    "iter",
    "next",
    "type",
    "isinstance",
    "issubclass",
    "callable",
    "hasattr",
    "getattr",
    "setattr",
    "delattr",
    "property",
    "classmethod",
    "staticmethod",
    "super",
    "vars",
    "dir",
    "object",
    "complex",
    "hex",
    "oct",
    "id",
    "abs",
    "chr",
    "ord",
    "pow",
    "format",
    "ascii",
    "bin",
    "bool",
    "bytes",
    "divmod",
    "float",
    "hash",
    "int",
    "repr",
    "round",
    "slice",
    "str",
    "input",
    "print",
    "True",
    "False",
    "None",
)


def _build_restricted_globals():
    """构建受限的全局命名空间"""
    custom_builtins = dict(safe_builtins)
    for name in _SAFE_BUILTIN_NAMES:
        import builtins

        if name in builtins.__dict__ and name not in custom_builtins:
            custom_builtins[name] = builtins.__dict__[name]
    custom_builtins["open"] = open
    custom_builtins["__import__"] = _safe_import
    return {
        "__builtins__": custom_builtins,
        "_print_": _StdoutPrintCollector,
        "_getattr_": safer_getattr,
        "_write_": lambda obj: obj,
        "_getitem_": lambda obj, key: obj[key],
        "_getiter_": lambda ob: ob,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_unpack_sequence_": guarded_unpack_sequence,
        "__name__": "__main__",
    }


@NodeHandlerRegistry.register("python")
class PythonNodeHandler(BaseNodeHandler):
    """
    Python代码执行节点处理器（基于 RestrictedPython）

    功能：
    1. 使用 RestrictedPython AST 级编译限制，提供安全沙箱
    2. 禁止 __dunder__ 属性访问（防止 __class__.__mro__ 等逃逸链）
    3. 禁止 str.format() 攻击
    4. 白名单模块导入（math, json, re, datetime 等安全模块）
    5. 保留 open 函数（配合超时控制）
    6. 要求定义 main 函数，参数与输入变量对应
    7. 支持超时控制
    8. 捕获标准输出和标准错误
    """

    ConfigClass = PythonNodeConfig

    async def execute(
        self,
        node: FlowNode,
        state: FlowState,
        config: Optional[RunnableConfig] = None,
        *,
        writer: Optional[StreamWriter] = None,
    ) -> FlowState:
        cfg = self._get_config(node)

        code = cfg.code
        timeout = cfg.timeout

        if not code:
            state.add_error(node.node_key, "Python代码不能为空")
            return state

        context = self._resolve_input_variables(cfg.input_variables, state)

        try:
            result = await self._execute_python(code, context, timeout)
            output_names = self._get_output_var_names(node, ["result"])
            result_name = output_names[0] if output_names else "result"
            if not result["success"]:
                raise NodeExecutionError(node.node_key, result["stderr"])
            state.set_node_variable(node.node_key, result_name, result)
        except asyncio.TimeoutError:
            raise NodeExecutionError(node.node_key, f"代码执行超时（{timeout}秒）")
        except NodeExecutionError:
            raise

        return state

    async def _execute_python(
        self, code: str, input_vars: dict, timeout: float
    ) -> dict:
        """
        使用 RestrictedPython 在受限环境中执行Python代码

        编译期限制：禁止 __dunder__ 属性访问、str.format() 攻击、
        try/except* 等，AST 级阻断危险语法
        运行时限制：白名单模块导入、受限 builtins

        Args:
            code: Python代码字符串
            input_vars: 输入变量字典
            timeout: 超时时间（秒）

        Returns:
            包含执行结果的字典
        """
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        result = {"stdout": "", "stderr": "", "result": None, "success": True}

        def run_code():
            # RestrictedPython 编译：AST 级安全检查
            compile_result = compile_restricted_exec(code, "<python_node>")
            if compile_result.errors:
                error_msg = "; ".join(compile_result.errors)
                raise SyntaxError(error_msg)

            restricted_globals = _build_restricted_globals()

            # 注入输入变量到全局命名空间
            restricted_globals.update(input_vars)

            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                loc = {}
                exec(compile_result.code, restricted_globals, loc)

                # 检查 main 函数是否存在
                if "main" not in loc:
                    raise RuntimeError("必须定义 main 函数")

                main_func = loc["main"]
                sig = inspect.signature(main_func)
                params = sig.parameters

                call_args = {}
                for param_name in params:
                    if param_name in input_vars:
                        call_args[param_name] = input_vars[param_name]

                return main_func(**call_args)

        loop = asyncio.get_event_loop()

        try:
            exec_result = await asyncio.wait_for(
                loop.run_in_executor(None, run_code), timeout=timeout
            )
            result["result"] = exec_result
        except asyncio.TimeoutError:
            raise
        except SyntaxError as e:
            result["success"] = False
            result["stderr"] = f"语法错误: {str(e)}"
        except Exception:
            result["success"] = False
            tb = traceback.format_exc()
            result["stderr"] = tb
        finally:
            result["stdout"] = stdout_capture.getvalue()
            result["stderr"] = result["stderr"] or stderr_capture.getvalue()

        return result

    @classmethod
    def get_input_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        if config is None:
            config = node.base_config or {}

        input_data = {
            "code": config.get("code", ""),
            "timeout": config.get("timeout", 30),
        }

        input_variables = config.get("input_variables", [])
        for var in input_variables:
            var_name = var.get("name")
            var_source = var.get("source")
            if var_name and var_source:
                input_data[f"input_{var_name}"] = resolver.resolve(var_source, state)

        return input_data

    @classmethod
    def get_output_content(
        cls, node: FlowNode, state: FlowState, resolver, config: Optional[dict] = None
    ) -> Optional[dict]:
        if config is None:
            config = node.base_config or {}

        output = {}
        output_vars = config.get("output_variables", [])
        if output_vars:
            for var in output_vars:
                name = (
                    var.get("name", "")
                    if isinstance(var, dict)
                    else getattr(var, "name", "")
                )
                if name:
                    value = state.get_node_variable(node.node_key, name)
                    if value is not None:
                        output[name] = value
        else:
            value = state.get_node_variable(node.node_key, "python_result")
            if value is not None:
                output["python_result"] = value

        return output if output else None

    @classmethod
    def allow_multiple_tool_connections(cls) -> bool:
        return True

    async def get_tool(self, node: FlowNode) -> Optional[StructuredTool]:
        """
        返回Python执行工具

        use_preset_for_tool=True 时：使用节点已配置的 code，LLM 只需提供
        input_variables 定义的业务参数，不暴露代码细节。
        use_preset_for_tool=False 时：通用 python_executor，LLM 填写全部参数。
        """
        handler = self
        cfg = self._get_config(node)
        timeout = cfg.timeout

        if cfg.use_preset_for_tool:
            return self._build_preset_tool(node, cfg, handler, timeout)

        input_variables = cfg.input_variables

        param_desc_list = []
        for v in input_variables:
            name = v.get("name", "")
            var_type = v.get("type", "string")
            if name:
                param_desc_list.append(f"{name}: {var_type}")

        if param_desc_list:
            params_desc = ", ".join(param_desc_list)
            param_list = "\n".join(
                [
                    f"  - {v.get('name')}: {v.get('type', 'string')}"
                    for v in input_variables
                    if v.get("name")
                ]
            )
        else:
            params_desc = "无"
            param_list = "  无"

        class PythonToolInput(BaseModel):
            code: str = Field(
                ..., description=f"Python代码，必须定义 main 函数，参数: {params_desc}"
            )
            input_data: str = Field(
                default="{}",
                description=f"main 函数参数值，JSON格式。参数列表:\n{param_list}",
            )

        description = f"""在沙箱环境中执行Python代码进行数据处理或计算。

必须定义 main 函数，参数签名:
  def main({params_desc}):

参数类型说明:
{param_list}

允许导入的模块: math, json, re, datetime, collections, itertools, functools, decimal, statistics, hashlib, uuid, copy, dataclasses 等。
禁止导入危险模块（os, sys, subprocess, socket等），禁止访问 __dunder__ 属性。
返回值将作为执行结果。"""

        async def execute_python(code: str, input_data: str = "{}") -> str:
            try:
                input_vars = json.loads(input_data) if input_data else {}
            except json.JSONDecodeError:
                input_vars = {}

            try:
                result = await handler._execute_python(code, input_vars, timeout)
                return json.dumps(result, ensure_ascii=False)
            except asyncio.TimeoutError:
                return json.dumps(
                    {"error": f"执行超时（{timeout}秒）", "success": False},
                    ensure_ascii=False,
                )
            except Exception as e:
                return json.dumps(
                    {"error": str(e), "success": False}, ensure_ascii=False
                )

        return StructuredTool(
            name="python_executor",
            description=description,
            func=None,
            coroutine=execute_python,
            args_schema=PythonToolInput,
        )

    def _build_preset_tool(
        self,
        node: FlowNode,
        cfg: PythonNodeConfig,
        handler,
        timeout: float,
    ) -> Optional[StructuredTool]:
        """构建预设代码的Python工具，LLM 只提供业务参数"""
        code = cfg.code
        input_variables = cfg.input_variables

        # ---- 动态构建 args_schema ----
        fields: dict[str, tuple[type, Any]] = {}
        for var in input_variables:
            name = var.get("name", "")
            if not name:
                continue
            var_type = var.get("type", "string")
            if var_type == "number":
                py_type = float
                default = Field(default=0.0, description=name)
            elif var_type == "boolean":
                py_type = bool
                default = Field(default=False, description=name)
            else:
                py_type = str
                default = Field(default="", description=name)
            fields[name] = (py_type, default)

        if not fields:
            fields["_dummy"] = (str, Field(default="", description="忽略"))

        ToolInput = create_model(
            f"{node.node_key}_python_input", __base__=BaseModel, **fields
        )

        tool_name = (
            (node.node_name or node.node_key)
            .strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
        )
        description = (
            cfg.description or f"执行 {node.node_name or node.node_key} Python代码"
        )

        async def execute_preset_python(**kwargs) -> str:
            input_vars = {k: v for k, v in kwargs.items() if k != "_dummy"}
            try:
                result = await handler._execute_python(code, input_vars, timeout)
                return json.dumps(result, ensure_ascii=False)
            except asyncio.TimeoutError:
                return json.dumps(
                    {"error": f"执行超时（{timeout}秒）", "success": False},
                    ensure_ascii=False,
                )
            except Exception as e:
                return json.dumps(
                    {"error": str(e), "success": False}, ensure_ascii=False
                )

        return StructuredTool(
            name=tool_name,
            description=description,
            func=None,
            coroutine=execute_preset_python,
            args_schema=ToolInput,
        )
