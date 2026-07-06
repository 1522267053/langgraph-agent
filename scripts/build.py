"""
跨平台构建脚本：Nuitka 模块编译 + PyInstaller 打包

用法:
    poetry run python scripts/build.py <版本号>             # 完整构建
    poetry run python scripts/build.py <版本号> --skip-nuitka  # 跳过 Nuitka 步骤

示例:
    poetry run python scripts/build.py 0.2.0
    poetry run python scripts/build.py 0.2.0 --skip-nuitka
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

IS_WINDOWS = platform.system() == "Windows"
NUITKA_EXT = ".cp312-win_amd64.pyd" if IS_WINDOWS else ".cpython-*.so"


def run(cmd: list[str], desc: str) -> None:
    print(f"[{desc}]", flush=True)
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print(f"[ERROR] {desc} failed (exit code {result.returncode})", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] {desc} done")
    print()


def step(step_num: int, total: int, label: str) -> None:
    print(f"[{step_num}/{total}] {label}...")
    print()


def set_version(version: str) -> None:
    version_file = PROJECT_ROOT / "app" / "config" / "version.py"
    content = version_file.read_text(encoding="utf-8")
    content = re.sub(
        r'__version__\s*=\s*"[^"]*"',
        f'__version__ = "{version}"',
        content,
    )
    version_file.write_text(content, encoding="utf-8")

    pyproject = PROJECT_ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    content = re.sub(
        r'^version\s*=\s*"[^"]*"',
        f'version = "{version}"',
        content,
        flags=re.MULTILINE,
    )
    pyproject.write_text(content, encoding="utf-8")
    print(f"  Version set to {version}")


def build_frontend() -> None:
    dist_index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if dist_index.exists():
        print("  Frontend dist exists, skip build")
        return

    print("  Frontend dist not found, building...")
    frontend_dir = PROJECT_ROOT / "frontend"
    if IS_WINDOWS:
        run(["cmd", "/c", "npm", "run", "build"], "npm run build")
    else:
        run(["npm", "run", "build"], "npm run build")


def generate_static_imports() -> None:
    run(
        ["poetry", "run", "python", "scripts/generate_static_imports.py"],
        "generate_static_imports",
    )


def compile_nuitka() -> None:
    build_dir = PROJECT_ROOT / "build"
    build_dir.mkdir(exist_ok=True)

    for p in build_dir.glob(f"app{NUITKA_EXT}"):
        p.unlink()

    run(
        [
            "poetry",
            "run",
            "nuitka",
            "--module",
            "app",
            "--include-package=app",
            "--output-dir=build",
            "--show-progress",
        ],
        "nuitka --module app",
    )

    app_pyi = PROJECT_ROOT / "app.pyi"
    if app_pyi.exists():
        shutil.copy2(app_pyi, build_dir / "app.pyi")


def run_pyinstaller() -> None:
    dist_dir = PROJECT_ROOT / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    pyi_build = PROJECT_ROOT / "build" / "langgraph_agent"
    if pyi_build.exists():
        shutil.rmtree(pyi_build)

    run(
        ["poetry", "run", "pyinstaller", "build.spec", "--noconfirm"],
        "pyinstaller build.spec",
    )


def create_runtime_dirs() -> None:
    dist_base = PROJECT_ROOT / "dist" / "langgraph_agent"
    for sub in ("uploads", "data", "logs"):
        (dist_base / sub).mkdir(parents=True, exist_ok=True)

    env_example = PROJECT_ROOT / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, dist_base / ".env")
        print("  .env.example copied as .env to dist/langgraph_agent/")
    else:
        print("  .env.example not found, skip copy")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="langgraph_agent 跨平台构建脚本 (Nuitka + PyInstaller)",
    )
    parser.add_argument("version", help="版本号，例如 0.2.0")
    parser.add_argument(
        "--skip-nuitka",
        action="store_true",
        help="跳过 Nuitka 模块编译步骤",
    )
    args = parser.parse_args()

    total_steps = 5 if args.skip_nuitka else 6
    step_idx = 0

    print("=" * 60)
    print("  langgraph_agent PyInstaller Build")
    print(f"  Version: {args.version}")
    if args.skip_nuitka:
        print("  (PyInstaller only, skip Nuitka)")
    else:
        print("  (Nuitka module + PyInstaller)")
    print("=" * 60)
    print()

    print(f"[0/{max(total_steps - 1, 0)}] Setting version to {args.version}...")
    print()
    set_version(args.version)
    print(f"[OK] Version set to {args.version}")
    print()

    step_idx += 1
    step(step_idx, total_steps, "Checking frontend build")
    build_frontend()

    step_idx += 1
    step(step_idx, total_steps, "Generating static imports")
    generate_static_imports()

    if not args.skip_nuitka:
        step_idx += 1
        step(step_idx, total_steps, "Compiling app package with Nuitka (--module)")
        compile_nuitka()

    step_idx += 1
    step(step_idx, total_steps, "Running PyInstaller (--onedir)")
    run_pyinstaller()

    step_idx += 1
    step(step_idx, total_steps, "Creating runtime directories")
    create_runtime_dirs()

    ext = ".exe" if IS_WINDOWS else ""
    nui_ext = ".pyd" if IS_WINDOWS else ".so"
    print("=" * 60)
    print("  Build complete!")
    print("=" * 60)
    print()
    print(f"  Output: dist/langgraph_agent/")
    print(f"  Executable: dist/langgraph_agent/langgraph_agent{ext}")
    print(f"  Version: {args.version}")
    print()
    if not args.skip_nuitka:
        print(f"  Business code: app.*{nui_ext} (compiled binary)")
        print("  Third-party libs: .pyc (PyInstaller default)")
    else:
        print("  All code: .pyc (PyInstaller default, Nuitka skipped)")
    print()


if __name__ == "__main__":
    main()
