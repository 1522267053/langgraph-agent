"""
模块自动加载器
自动导入 models/api/node_handlers 目录下的所有模块
兼容开发环境和 PyInstaller/Nuitka 打包环境
"""

import importlib
import logging
import pkgutil
import sys

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _is_packaged_env() -> bool:
    """判断是否为打包环境（PyInstaller 或 Nuitka）"""
    return getattr(sys, "frozen", False) or "__compiled__" in dir()


def _ensure_submodules_loaded(package_name: str):
    """
    在打包环境下，通过导入 _static_imports 确保所有子模块已加载到 sys.modules
    _static_imports.py 由 scripts/generate_static_imports.py 自动生成
    """
    if not _is_packaged_env():
        return
    try:
        import app.utils._static_imports  # noqa: F401
    except ImportError:
        pass


def _get_submodules_from_sys_modules(package_name: str):
    """从 sys.modules 中获取已加载的子模块列表"""
    prefix = package_name + "."
    found = []
    for name in sys.modules:
        if name.startswith(prefix):
            parts = name[len(prefix) :].split(".")
            if len(parts) == 1 and parts[0] and not parts[0].startswith("_"):
                found.append(parts[0])
    return sorted(set(found))


def _iter_package_modules(package_name: str):
    """
    通过 Python 包的 __path__ 遍历子模块
    兼容文件系统和 PyInstaller 打包后的 zip archive
    """
    package = importlib.import_module(package_name)
    return pkgutil.iter_modules(package.__path__)


def _iter_modules_safe(package_name: str):
    """
    安全遍历子模块，兼容 Nuitka 编译环境
    开发环境：使用 pkgutil.iter_modules 扫描文件系统
    打包环境：先通过 _static_imports 加载子模块，再从 sys.modules 获取列表
    """
    if _is_packaged_env():
        _ensure_submodules_loaded(package_name)
        modules = _get_submodules_from_sys_modules(package_name)
        if modules:
            return [(None, name, False) for name in modules]
        return []

    return list(_iter_package_modules(package_name))


def load_all_models():
    """
    自动导入 app/models 目录下的所有模型模块
    确保所有模型都被 SQLAlchemy 的 Base.metadata 追踪
    """
    package_name = "app.models"
    count = 0
    for _, module_name, is_pkg in _iter_modules_safe(package_name):
        if not is_pkg and module_name != "__init__":
            importlib.import_module(f"{package_name}.{module_name}")
            count = count + 1
    logger.info("[OK] Loaded model count: %d", count)


def load_all_handlers():
    """
    自动导入 app/agent_flow/node_handlers 目录下的所有处理器模块
    触发装饰器注册，确保所有处理器都被 NodeHandlerRegistry 注册
    """
    package_name = "app.agent_flow.node_handlers"
    count = 0
    for _, module_name, is_pkg in _iter_modules_safe(package_name):
        if not is_pkg and module_name != "__init__":
            importlib.import_module(f"{package_name}.{module_name}")
            count = count + 1
    logger.info("[OK] Loaded handler count: %d", count)


def load_all_providers():
    """
    自动导入 app/agent_flow/ai_provider 目录下的所有提供商模块
    触发装饰器注册，确保所有提供商都被 AIProviderRegistry 注册
    """
    package_name = "app.agent_flow.ai_provider"
    count = 0
    for _, module_name, is_pkg in _iter_modules_safe(package_name):
        if not is_pkg and module_name != "__init__":
            importlib.import_module(f"{package_name}.{module_name}")
            count = count + 1
    logger.info("[OK] Loaded provider count: %d", count)


def register_all_routers(app: FastAPI):
    """
    自动注册 app/api 目录下的所有路由

    约定：每个 api 文件必须导出一个名为 'router' 的 APIRouter 实例

    Args:
        app: FastAPI 应用实例
    """
    package_name = "app.api"
    count = 0
    try:
        modules = _iter_modules_safe(package_name)
    except ModuleNotFoundError:
        logger.warning("[SKIP] Package not found: %s", package_name)
        return
    for _, module_name, is_pkg in modules:
        if not is_pkg and module_name != "__init__":
            module = importlib.import_module(f"{package_name}.{module_name}")
            if hasattr(module, "router"):
                app.include_router(module.router)
                count = count + 1
    logger.info("[OK] Registered router count: %d", count)
