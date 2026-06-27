# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置 - langgraph_agent 桌面客户端
基于 build.spec，入口改为 desktop.py，隐藏控制台窗口
"""

import glob
import os
import platform
import site
from pathlib import Path

block_cipher = None
project_root = os.path.abspath(".")
venv_site_packages = site.getsitepackages()[0] if site.getsitepackages() else ""

ext = ".pyd" if platform.system() == "Windows" else ".so"
app_binary_pattern = os.path.join("build", f"app.*{ext}")
app_binary_matches = glob.glob(app_binary_pattern)
if not app_binary_matches:
    raise FileNotFoundError(f"No compiled app binary found: {app_binary_pattern}")
binaries = [
    (app_binary_matches[0], "."),
]

# ---- 数据文件收集 ----
datas = [
    ("frontend/dist", "frontend/dist"),
    ("skills", "skills"),
    ("app/utils/_static_imports.py", "app/utils"),
    ("logo.ico", "."),
]

# ---- 收集 chromadb migrations ----
chromadb_migrations = os.path.join(venv_site_packages, "chromadb", "migrations")
if os.path.isdir(chromadb_migrations):
    datas.append((chromadb_migrations, "chromadb/migrations"))

# ---- 隐式导入 ----
hiddenimports = [
    # pywebview 相关
    "webview",
    "webview.platforms",
    "webview.platforms.winforms",
    "webview.platforms.cocoa",
    "webview.platforms.gtk",
    "webview.platforms.qt",
    "webview.http",
    "webview.js",
    "webview.util",
    "webview.errors",
    "webview.event",
    "webview.menu",
    "webview.window",
    "webview.guilib",
    "bottle",
    "clr_loader",
    "pythonnet",
    "pythonnet.runtime",
    # langchain / langgraph 系列
    "langchain",
    "langchain_core",
    "langchain_core.runnables",
    "langchain_core.output_parsers",
    "langchain_core.prompts",
    "langchain_core.messages",
    "langchain_core.language_models",
    "langchain_core.tools",
    "langchain_openai",
    "langchain_deepseek",
    "langchain_anthropic",
    "langchain_mcp_adapters",
    "langchain_mcp_adapters.client",
    "langchain_mcp_adapters.sessions",
    "langchain_text_splitters",
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.checkpoint",
    "langgraph.checkpoint.memory",
    "langgraph.prebuilt",
    # chromadb
    "chromadb",
    "chromadb.config",
    "chromadb.api",
    "chromadb.api.client",
    "chromadb.api.models",
    "chromadb.api.segment",
    "chromadb.db",
    "chromadb.db.impl",
    "chromadb.db.impl.sqlite",
    "chromadb.api.rust",
    "chromadb.segment.impl",
    "chromadb.segment.impl.manager",
    "chromadb.segment.impl.vector",
    "chromadb.segment.impl.vector.local_hnsw",
    "chromadb.segment.impl.metadata",
    "chromadb.telemetry",
    "chromadb.telemetry.product",
    "chromadb.telemetry.product.posthog",
    "chromadb_rust_bindings",
    # uvicorn 完整子模块
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # 数据库相关
    "aiosqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "aiomysql",
    "pymysql",
    "sqlalchemy.dialects.mysql",
    "sqlalchemy.sql.default_comparator",
    # 其他依赖
    "docx",
    "simpleeval",
    "RestrictedPython",
    "openpyxl",
    "dashscope",
    "colorama",
    "cryptography",
    "httpx",
    "httpx._transports",
    "httpx._transports.default",
    "jinja2",
    "sse_starlette",
    "annotated_types",
    "pydantic",
    "pydantic_settings",
    "anyio",
    "anyio._backends",
    "anyio._backends._asyncio",
    "sniffio",
    "h11",
    "certifi",
    "idna",
    "multipart",
    "email_validator",
    "tenacity",
    "jsonpatch",
    "tiktoken",
    "tokenizers",
]

a = Analysis(
    ["desktop.py"],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "scipy",
        "pandas",
        "notebook",
        "IPython",
        "jupyter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="langgraph_agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(project_root, "logo.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="langgraph_agent",
)
