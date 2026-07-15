"""
系统托盘管理（仅 Windows 打包环境启用）

主线程运行 pystray 图标循环，守护线程运行 uvicorn 服务器。
通过菜单可打开浏览器、打开日志目录、重启服务、退出。
"""

import http.server
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from app.config.build_utils import BASE_DIR, IS_WINDOWS, get_internal_dir
from app.config.settings import settings

logger = logging.getLogger(__name__)

_server = None
_server_thread = None
_loading_server = None

_LOADING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>智能体平台</title>
<style>
  body{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}
  .spinner{width:48px;height:48px;border:4px solid #dcdfe6;border-top-color:#409eff;border-radius:50%;animation:spin .8s linear infinite;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .text{margin-top:24px;color:#606266;font-size:16px;}
  .hint{margin-top:8px;color:#909399;font-size:13px;}
</style>
</head>
<body>
  <div class="spinner"></div>
  <div class="text">智能体平台正在启动...</div>
  <div class="hint">请稍候</div>
<script>
  var APP_URL='http://127.0.0.1:__PORT__/';
  function poll(){
    fetch(APP_URL+'api/health',{mode:'no-cors'})
      .then(function(){window.location.href=APP_URL;})
      .catch(function(){setTimeout(poll,600);});
  }
  poll();
</script>
</body>
</html>"""


def handle_duplicate_instance() -> bool:
    """单实例守卫：若已有实例运行，打开其浏览器并返回 True。

    通过探测健康端点判断是否已有实例。仅在打包环境由 main.py 最早调用，
    命中后调用方应 sys.exit(0) 退出新进程，避免出现重复托盘图标。

    先用原生 socket 快速探测端口（连接被拒绝时立即返回，不等满超时），
    端口开放时才走 HTTP 确认是否为本应用。
    """

    port = settings.app_port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        sock.connect(("127.0.0.1", port))
        sock.close()
    except OSError:
        return False

    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status != 200:
                return False
            body = resp.read().decode("utf-8", errors="ignore")
            if "running" in body:
                _open_browser()
                return True
    except (urllib.error.URLError, ConnectionError, OSError):
        pass
    return False


def open_loading_page() -> None:
    """启动临时 HTTP 服务返回加载页，并立即用浏览器打开。

    使用真实 HTTP 服务而非 file:// 协议，避免 Windows 文件关联弹窗。
    """
    global _loading_server

    html = _LOADING_HTML.replace("__PORT__", str(settings.app_port))

    class _LoadingHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def log_message(self, *args):
            pass

    _loading_server = http.server.HTTPServer(("127.0.0.1", 0), _LoadingHandler)
    port = _loading_server.server_address[1]
    threading.Thread(
        target=_loading_server.serve_forever, daemon=True, name="loading-server"
    ).start()
    webbrowser.open(f"http://127.0.0.1:{port}/")


def stop_loading_server() -> None:
    """关闭加载页 HTTP 服务（主服务就绪后调用）"""
    global _loading_server
    if _loading_server is not None:
        server = _loading_server
        _loading_server = None

        def _stop() -> None:
            server.shutdown()  # 停止 serve_forever 循环
            server.server_close()  # 关闭 socket 释放资源

        threading.Thread(target=_stop, daemon=True, name="loading-server-stop").start()


def _start_uvicorn(app) -> None:
    """在守护线程中启动 uvicorn 服务器（可编程停止）"""
    import uvicorn

    from app.config.logging_config import get_uvicorn_log_config

    global _server, _server_thread

    config = uvicorn.Config(
        app,
        host=settings.app_host,
        port=settings.app_port,
        log_config=get_uvicorn_log_config(),
        timeout_keep_alive=300,
    )
    _server = uvicorn.Server(config)

    def _serve() -> None:
        try:
            _server.run()
        except Exception:
            logger.exception("uvicorn server crashed")

    _server_thread = threading.Thread(target=_serve, daemon=True, name="uvicorn")
    _server_thread.start()


def _wait_for_server(timeout: int = 30) -> bool:
    """轮询健康检查端点，等待服务就绪"""
    url = f"http://127.0.0.1:{settings.app_port}/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server is None:
            time.sleep(0.3)
            continue
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    return False


def _signal_stop() -> None:
    """非阻塞：通知 uvicorn 退出（不等待，保证托盘菜单立即响应）"""
    if _server is not None:
        _server.should_exit = True


def _finalize_exit() -> None:
    """icon.run() 返回后执行：尽力等待优雅关闭，然后强制退出进程。

    强制 os._exit 绕过 atexit，避免 chromadb 等依赖的残留非守护线程
    阻塞解释器退出导致进程假死、端口占用。
    """
    if _server_thread is not None and _server_thread.is_alive():
        _server_thread.join(timeout=5)
    logging.shutdown()
    os._exit(0)


def _get_url() -> str:
    return f"http://127.0.0.1:{settings.app_port}/"


def _open_browser() -> None:
    webbrowser.open(_get_url())


def _open_logs_dir() -> None:
    log_dir = BASE_DIR / settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        os.startfile(str(log_dir))


def _on_open_browser(icon, item) -> None:
    _open_browser()


def _on_open_logs(icon, item) -> None:
    _open_logs_dir()


def _on_restart(icon, item) -> None:
    """重启服务：拉起新 exe 进程，停止当前进程"""
    icon.notify("正在重启服务...", "智能体平台")
    try:
        subprocess.Popen(
            [sys.executable],
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception:
        logger.exception("重启服务失败")
        icon.notify("重启失败，请查看日志", "智能体平台")
        return
    _signal_stop()
    icon.stop()


def _on_quit(icon, item) -> None:
    _signal_stop()
    icon.stop()


def _load_icon_image():
    """加载托盘图标，找不到则生成占位图标"""
    from PIL import Image

    for path in (get_internal_dir() / "logo.ico", BASE_DIR / "logo.ico"):
        if path.exists():
            return Image.open(str(path))
    logger.warning("未找到 logo.ico，使用占位图标")
    return Image.new("RGB", (64, 64), color=(64, 158, 255))


def run_with_tray(app) -> None:
    """启动 uvicorn（守护线程）+ 系统托盘（主线程）"""
    import pystray

    _start_uvicorn(app)

    if not _wait_for_server(timeout=30):
        logger.warning("服务在 30 秒内未就绪，仍创建托盘图标")
    stop_loading_server()

    image = _load_icon_image()

    from app.config.version import __version__

    menu = pystray.Menu(
        pystray.MenuItem("打开浏览器", _on_open_browser, default=True),
        pystray.MenuItem("打开日志目录", _on_open_logs),
        pystray.MenuItem("重启服务", _on_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _on_quit),
    )

    icon = pystray.Icon(
        "langgraph_agent",
        image,
        f"智能体平台 v{__version__}",
        menu,
    )
    icon.run()
    _finalize_exit()
