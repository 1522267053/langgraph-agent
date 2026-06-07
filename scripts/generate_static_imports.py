"""
自动扫描 app/ 下的子模块目录，生成 app/utils/_static_imports.py
用于 Nuitka --module 编译时能看到所有子模块

用法: poetry run python scripts/generate_static_imports.py
"""

import os
import pkgutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCAN_DIRS = [
    "app/models",
    "app/schemas",
    "app/services",
    "app/middleware",
    "app/config",
    "app/constants",
    "app/utils",
    "app/agent_flow/node_handlers",
    "app/agent_flow/ai_provider",
    "app/api",
]

EXCLUDE_MODULES = {"__init__", "_static_imports"}


def main():
    lines = [
        '"""',
        "自动生成的静态 import 列表",
        "用于 Nuitka --module 编译时能看到所有子模块",
        "由 scripts/generate_static_imports.py 自动生成，请勿手动编辑",
        '"""',
        "",
        "# ruff: noqa: F401",
        "",
    ]

    for scan_dir in SCAN_DIRS:
        full_path = os.path.join(PROJECT_ROOT, scan_dir)
        if not os.path.isdir(full_path):
            continue

        package_name = scan_dir.replace("/", ".")
        for _, name, is_pkg in pkgutil.iter_modules([full_path]):
            if not is_pkg and name not in EXCLUDE_MODULES:
                lines.append(f"from {package_name} import {name}")

    lines.append("")
    output_path = os.path.join(PROJECT_ROOT, "app", "utils", "_static_imports.py")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    count = len([line for line in lines if line.startswith("from app.")])
    print(f"Generated {output_path} with {count} imports")


if __name__ == "__main__":
    main()
