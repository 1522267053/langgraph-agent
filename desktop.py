"""
桌面客户端入口
使用 pywebview 内嵌 FastAPI 后端，打包为独立桌面应用

开发模式: python main.py（照常工作）
桌面模式: python desktop.py（内嵌窗口）
"""

import base64
import ctypes
import logging
import logging.config
import os
import platform
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
import webview
from plyer import notification

os.environ["DESKTOP_MODE"] = "1"

from app.config.build_utils import BASE_DIR, get_internal_dir
from app.config.logging_config import get_uvicorn_log_config
from app.config.settings import settings
from app.config.version import __version__

logger = logging.getLogger(__name__)

SERVER_HOST = "127.0.0.1"
SERVER_PORT = settings.app_port
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
HEALTH_CHECK_URL = f"{SERVER_URL}/api/health"

WINDOW_TITLE = f"智能体流程编排平台 v{__version__}"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720

LOADING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    height:100vh;
    background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    color:#e0e0e0;user-select:none;
  }
  .logo{width:96px;height:96px;margin-bottom:28px;border-radius:20px;box-shadow:0 4px 24px rgba(64,158,255,.3)}
  .spinner{
    width:44px;height:44px;
    border:4px solid rgba(255,255,255,.12);
    border-top-color:#409eff;
    border-radius:50%;
    animation:spin .75s linear infinite;
    margin-bottom:24px;
  }
  @keyframes spin{to{transform:rotate(360deg)}}
  .title{font-size:20px;font-weight:600;margin-bottom:10px;color:#fff;letter-spacing:1px}
  .subtitle{font-size:13px;color:#909399}
  .dots::after{content:'';animation:dots 1.5s steps(4,end) infinite}
  @keyframes dots{0%{content:''}25%{content:'.'}50%{content:'..'}75%{content:'...'}}
  .version{position:fixed;bottom:24px;font-size:12px;color:#606266}
</style>
</head>
<body>
  <img class="logo" src="__LOGO__" alt="Logo">
  <div class="spinner"></div>
  <div class="title">智能体流程编排平台</div>
  <div class="subtitle">正在启动服务<span class="dots"></span></div>
  <div class="version">v__VERSION__</div>
</body>
</html>""".replace("__VERSION__", __version__)


def _find_asset(*names: str) -> Path | None:
    """在 internal 目录中查找资源文件，依次尝试多个候选名"""
    internal = get_internal_dir()
    for name in names:
        for base in (internal, internal / "frontend" / "public"):
            p = base / name
            if p.exists():
                return p
    return None


def get_screen_center() -> tuple[int, int]:
    """获取屏幕居中坐标，跨平台兼容"""
    system = platform.system()
    screen_w = screen_h = 0

    if system == "Windows":
        try:
            user32 = ctypes.windll.user32
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
        except Exception:
            pass

    if screen_w == 0 or screen_h == 0:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
            root.destroy()
        except Exception:
            return 0, 0

    x = max(0, (screen_w - WINDOW_WIDTH) // 2)
    y = max(0, (screen_h - WINDOW_HEIGHT) // 2)
    return x, y


def get_logo_data_uri() -> str:
    """读取 logo 并转为 base64 data URI"""
    logo_path = _find_asset("logo_256x256.ico", "logo.ico", "logo.png")
    if not logo_path:
        return ""
    data = base64.b64encode(logo_path.read_bytes()).decode()
    mime = "image/png" if logo_path.suffix == ".png" else "image/x-icon"
    return f"data:{mime};base64,{data}"


def get_icon_path() -> str | None:
    """获取窗口图标路径"""
    p = _find_asset("logo.ico")
    return str(p) if p else None


def get_storage_path() -> str:
    """获取 WebView 持久化存储路径（cookie/localStorage）"""
    p = BASE_DIR / "data" / "webview"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


class DesktopApi:
    """暴露给前端调用的 Python API"""

    def notify(self, title: str, body: str = "") -> None:
        try:
            notification.notify(
                title=title,
                message=body,
                app_name=WINDOW_TITLE,
                app_icon=str(get_icon_path() or ""),
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"系统通知失败: {e}")

    def copy_to_clipboard(self, text: str) -> None:
        try:
            import pyperclip

            pyperclip.copy(text)
        except Exception:
            try:
                self._clipboard_copy_fallback(text)
            except Exception as e:
                logger.warning(f"剪贴板操作失败: {e}")

    def get_clipboard(self) -> str:
        try:
            import pyperclip

            return pyperclip.paste() or ""
        except Exception:
            try:
                return self._clipboard_paste_fallback()
            except Exception:
                return ""

    @staticmethod
    def _clipboard_copy_fallback(text: str) -> None:
        system = platform.system()
        if system == "Windows":
            subprocess.run(
                ["powershell", "-Command", f"Set-Clipboard -Value '{text}'"],
                capture_output=True,
                timeout=5,
                check=True,
            )
        elif system == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), timeout=5, check=True)
        else:
            for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
                try:
                    subprocess.run(cmd, input=text.encode(), timeout=5, check=True)
                    return
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            raise RuntimeError(
                "No clipboard tool found (install wl-clipboard or xclip)"
            )

    @staticmethod
    def _clipboard_paste_fallback() -> str:
        system = platform.system()
        if system == "Windows":
            result = subprocess.run(
                ["powershell", "-Command", "Get-Clipboard"],
                capture_output=True,
                timeout=5,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        elif system == "Darwin":
            result = subprocess.run(
                ["pbpaste"], capture_output=True, timeout=5, text=True, check=True
            )
            return result.stdout.strip()
        else:
            for cmd in (["wl-paste"], ["xclip", "-selection", "clipboard", "-o"]):
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, timeout=5, text=True, check=True
                    )
                    return result.stdout.strip()
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            raise RuntimeError(
                "No clipboard tool found (install wl-clipboard or xclip)"
            )


class UvicornThread(threading.Thread):
    """在后台线程中运行 uvicorn"""

    def __init__(self):
        super().__init__(daemon=True)
        self.server: uvicorn.Server | None = None
        self._startup_error: str | None = None

    def run(self):
        try:
            from app.config.app_setup import create_app

            app = create_app()
            config = uvicorn.Config(
                app=app,
                host=SERVER_HOST,
                port=SERVER_PORT,
                log_config=get_uvicorn_log_config(),
                timeout_keep_alive=300,
            )
            self.server = uvicorn.Server(config)
            self.server.run()
        except Exception:
            self._startup_error = traceback.format_exc()
            logger.error(f"uvicorn 启动失败:\n{self._startup_error}")

    def stop(self):
        if self.server:
            self.server.should_exit = True


def _wait_for_server(uvicorn_thread: UvicornThread, timeout: float = 30.0) -> bool:
    """轮询等待服务就绪"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if uvicorn_thread._startup_error:
            return False
        try:
            req = urllib.request.Request(HEALTH_CHECK_URL)
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.3)
    return False


CONTEXT_MENU_JS = r"""
(function() {
    if (window.__pywebview_context_menu) return;
    window.__pywebview_context_menu = true;

    var menu = document.createElement('div');
    menu.id = '__pywebview_cm';
    menu.style.cssText = 'position:fixed;z-index:99999;background:#fff;border:1px solid #dcdfe6;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.15);padding:4px 0;min-width:160px;display:none;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:13px;color:#303133';
    document.body.appendChild(menu);

    var _savedRange = null;
    var _savedEl = null;
    var _savedStart = 0;
    var _savedEnd = 0;

    function isInput(el) {
        return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
    }

    function addItem(label, action) {
        var item = document.createElement('div');
        item.textContent = label;
        item.style.cssText = 'padding:8px 16px;cursor:pointer;white-space:nowrap';
        item.onmouseenter = function() { this.style.background = '#ecf5ff'; };
        item.onmouseleave = function() { this.style.background = ''; };
        item.onclick = function(e) {
            e.preventDefault();
            e.stopPropagation();
            try { action(); } catch(ex) {}
            hideMenu();
        };
        menu.appendChild(item);
    }

    function addSeparator() {
        var sep = document.createElement('div');
        sep.style.cssText = 'height:1px;background:#e4e7ed;margin:4px 0';
        menu.appendChild(sep);
    }

    function hideMenu() { menu.style.display = 'none'; }

    function restoreSelection() {
        if (isInput(_savedEl)) {
            _savedEl.focus();
            _savedEl.selectionStart = _savedStart;
            _savedEl.selectionEnd = _savedEnd;
        } else if (_savedRange) {
            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(_savedRange);
        }
        if (_savedEl) { _savedEl.focus(); }
    }

    function deleteSelection() {
        if (isInput(_savedEl)) {
            _savedEl.focus();
            _savedEl.value = _savedEl.value.substring(0, _savedStart) + _savedEl.value.substring(_savedEnd);
            _savedEl.selectionStart = _savedEl.selectionEnd = _savedStart;
        } else if (_savedRange) {
            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(_savedRange);
            sel.deleteFromDocument();
        }
    }

    function insertText(text) {
        if (isInput(_savedEl)) {
            _savedEl.focus();
            _savedEl.value = _savedEl.value.substring(0, _savedStart) + text + _savedEl.value.substring(_savedEnd);
            var pos = _savedStart + text.length;
            _savedEl.selectionStart = _savedEl.selectionEnd = pos;
            _savedStart = _savedEnd = pos;
        } else if (_savedEl && _savedEl.isContentEditable) {
            _savedEl.focus();
            document.execCommand('insertText', false, text);
        } else {
            if (_savedEl) _savedEl.focus();
            document.execCommand('insertText', false, text);
        }
    }

    addItem('复制', function() {
        restoreSelection();
        document.execCommand('copy');
    });
    addItem('剪切', function() {
        restoreSelection();
        document.execCommand('copy');
        deleteSelection();
    });
    addItem('粘贴', function() {
        try {
            window.pywebview.api.get_clipboard().then(function(text) {
                if (text) { insertText(text); }
            });
        } catch(e) {
            document.execCommand('paste');
        }
    });
    addSeparator();
    addItem('全选', function() {
        if (_savedEl) {
            _savedEl.focus();
            _savedEl.select();
        } else {
            document.execCommand('selectAll');
        }
    });

    document.addEventListener('contextmenu', function(e) {
        e.preventDefault();
        e.stopPropagation();
        _savedEl = document.activeElement;
        if (isInput(_savedEl)) {
            _savedStart = _savedEl.selectionStart || 0;
            _savedEnd = _savedEl.selectionEnd || 0;
            _savedRange = null;
        } else {
            var sel = window.getSelection();
            _savedRange = sel.rangeCount > 0 ? sel.getRangeAt(0).cloneRange() : null;
            _savedStart = _savedEnd = 0;
        }
        menu.style.display = 'block';
        var mw = menu.offsetWidth || 160;
        var mh = menu.offsetHeight || 120;
        var x = Math.min(e.clientX, window.innerWidth - mw - 4);
        var y = Math.min(e.clientY, window.innerHeight - mh - 4);
        x = Math.max(x, 4);
        y = Math.max(y, 4);
        menu.style.left = x + 'px';
        menu.style.top = y + 'px';
    });

    document.addEventListener('mousedown', function(e) {
        if (menu.style.display === 'block' && !menu.contains(e.target)) {
            hideMenu();
        }
    });
})();
"""


class DesktopApp:
    """桌面应用主控，管理 uvicorn 线程和 pywebview 窗口生命周期"""

    def __init__(self):
        self.uvicorn = UvicornThread()
        self.window: webview.Window | None = None

    def run(self):
        logging.config.dictConfig(get_uvicorn_log_config())
        logger.info(f"启动桌面客户端 v{__version__}")

        self.uvicorn.start()

        x, y = get_screen_center()
        logo_uri = get_logo_data_uri()
        loading_html = LOADING_HTML.replace("__LOGO__", logo_uri)

        webview.settings["ALLOW_DOWNLOADS"] = True
        self.window = webview.create_window(
            title=WINDOW_TITLE,
            html=loading_html,
            js_api=DesktopApi(),
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            x=x,
            y=y,
            min_size=(800, 600),
            text_select=True,
        )

        self.window.events.closing += self._on_closing

        webview.start(
            self._startup_sequence,
            self.window,
            debug=settings.debug,
            icon=get_icon_path(),
            private_mode=False,
            storage_path=get_storage_path(),
        )

        self.uvicorn.join(timeout=3)
        logger.info("桌面客户端已退出")

    def _startup_sequence(self, window):
        """窗口显示后的启动流程（在后台线程中执行）"""
        logger.info("等待后端服务就绪...")

        if not _wait_for_server(self.uvicorn):
            logger.error("服务启动超时")
            window.evaluate_js(
                'document.querySelector(".subtitle").textContent="服务启动失败，请重试"'
            )
            time.sleep(3)
            self.uvicorn.stop()
            window.destroy()
            return

        logger.info(f"服务就绪: {SERVER_URL}")
        window.load_url(SERVER_URL)

        time.sleep(1)
        window.evaluate_js(CONTEXT_MENU_JS)

    def _on_closing(self):
        logger.info("窗口关闭，正在停止服务...")
        self.uvicorn.stop()


def main():
    DesktopApp().run()


if __name__ == "__main__":
    main()
