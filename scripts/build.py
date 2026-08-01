"""
Cross-platform build script: Nuitka module compilation + PyInstaller packaging

Usage:
    poetry run python scripts/build.py <version>               # full build
    poetry run python scripts/build.py <version> --skip-nuitka # skip Nuitka

Examples:
    poetry run python scripts/build.py 0.2.0
    poetry run python scripts/build.py 0.2.0 --skip-nuitka
"""

import argparse
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

IS_WINDOWS = platform.system() == "Windows"
NUITKA_EXT = ".cp312-win_amd64.pyd" if IS_WINDOWS else ".cpython-*.so"
NPM = "npm.cmd" if IS_WINDOWS else "npm"


class BuildError(RuntimeError):
    """Raised when a build step fails"""


def run(cmd: list[str], desc: str, cwd: Path | None = None) -> None:
    workdir = str(cwd if cwd is not None else PROJECT_ROOT)
    print(f"[{desc}]", flush=True)
    try:
        result = subprocess.run(cmd, cwd=workdir)
    except OSError as e:
        raise BuildError(f"{desc} failed to start: {e}") from e
    if result.returncode != 0:
        raise BuildError(f"{desc} failed (exit code {result.returncode})")
    print(f"[OK] {desc} done")
    print()


def run_steps(steps: list[tuple[str, Callable[[], None]]]) -> None:
    total = len(steps)
    for i, (label, fn) in enumerate(steps, start=1):
        print(f"[{i}/{total}] {label}...")
        print()
        fn()


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
    frontend_dir = PROJECT_ROOT / "frontend"
    dist_index = frontend_dir / "dist" / "index.html"
    if dist_index.exists():
        print("  Frontend dist exists, skip build")
        return
    print("  Frontend dist not found, building...")
    run([NPM, "run", "build"], "npm run build", cwd=frontend_dir)


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


def _copy_file(src: Path, dest: Path, label: str) -> None:
    if src.exists():
        shutil.copy2(src, dest)
        print(f"  {label} copied to {dest.parent}/")
    else:
        print(f"  {label} not found, skip copy")


def create_runtime_dirs() -> None:
    dist_base = PROJECT_ROOT / "dist" / "langgraph_agent"
    for sub in ("uploads", "data", "logs"):
        (dist_base / sub).mkdir(parents=True, exist_ok=True)

    _copy_file(PROJECT_ROOT / ".env.example", dist_base / ".env", ".env.example")
    _copy_file(
        PROJECT_ROOT / "models.dev.api.json",
        dist_base / "models.dev.api.json",
        "models.dev.api.json",
    )


def print_banner(args: argparse.Namespace) -> None:
    print("=" * 60)
    print("  langgraph_agent PyInstaller Build")
    print(f"  Version: {args.version}")
    if args.skip_nuitka:
        print("  (PyInstaller only, skip Nuitka)")
    else:
        print("  (Nuitka module + PyInstaller)")
    print("=" * 60)
    print()


def print_summary(args: argparse.Namespace, start_time: datetime) -> None:
    ext = ".exe" if IS_WINDOWS else ""
    nui_ext = ".pyd" if IS_WINDOWS else ".so"
    elapsed = datetime.now() - start_time
    elapsed_str = str(elapsed).split(".")[0]
    print("=" * 60)
    print("  Build complete!")
    print("=" * 60)
    print()
    print("  Output: dist/langgraph_agent/")
    print(f"  Executable: dist/langgraph_agent/langgraph_agent{ext}")
    print(f"  Version: {args.version}")
    print(f"  Build finished at: {start_time:%Y-%m-%d %H:%M:%S}")
    print(f"  Total build time: {elapsed_str}")
    print()
    if not args.skip_nuitka:
        print(f"  Business code: app.*{nui_ext} (compiled binary)")
        print("  Third-party libs: .pyc (PyInstaller default)")
    else:
        print("  All code: .pyc (PyInstaller default, Nuitka skipped)")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="langgraph_agent cross-platform build script (Nuitka + PyInstaller)",
    )
    parser.add_argument("version", help="Version number, e.g. 0.2.0")
    parser.add_argument(
        "--skip-nuitka",
        action="store_true",
        help="Skip the Nuitka module compilation step",
    )
    args = parser.parse_args()

    print_banner(args)

    start_time = datetime.now()

    steps: list[tuple[str, Callable[[], None]]] = [
        ("Setting version", lambda: set_version(args.version)),
        ("Checking frontend build", build_frontend),
        ("Generating static imports", generate_static_imports),
    ]
    if not args.skip_nuitka:
        steps.append(("Compiling app with Nuitka", compile_nuitka))
    steps.extend(
        [
            ("Running PyInstaller", run_pyinstaller),
            ("Creating runtime directories", create_runtime_dirs),
        ]
    )

    try:
        run_steps(steps)
    except BuildError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        return 1

    print_summary(args, start_time)
    return 0


if __name__ == "__main__":
    sys.exit(main())
