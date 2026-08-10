# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置 - updater（独立更新器）

--onefile 模式，仅依赖 Python 标准库，产物与主程序同级放置。
更新主程序时由 update_service 以独立进程拉起，自身运行中无法被覆盖，
解压时跳过自身文件。
"""

import platform

block_cipher = None

a = Analysis(
    ["updater.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pydoc",
        "doctest",
        "pdb",
        "http.server",
        "xmlrpc",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe_kwargs = dict(
    name="updater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
)

if platform.system() == "Windows":
    exe_kwargs["console"] = True
    exe_kwargs["icon"] = "logo.ico" if __import__("os").path.exists("logo.ico") else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    **exe_kwargs,
)
