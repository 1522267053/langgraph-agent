"""
独立更新器（纯标准库，随主程序打包为 updater.exe / updater）

由 update_service.apply_update() 以独立进程拉起，在主进程退出后接管：
等待主进程退出 → 备份旧版本 → 校验并解压新包 → 启动新版本 → 健康检查 → 失败回滚。

调用约定:
    updater <pid> <zip_path> <app_dir> <health_port> [health_timeout]

更新范围严格限定为 exe + _internal，用户数据（data/uploads/workspace/.env）不受影响。
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

IS_WIN = platform.system() == "Windows"
EXE_NAME = "langgraph_agent.exe" if IS_WIN else "langgraph_agent"


def _log(log_file, msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        log_file.write(line + "\n")
        log_file.flush()
    except Exception:
        pass


def _is_process_alive(pid: int) -> bool:
    """跨平台检测进程是否存活"""
    if IS_WIN:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_process_exit(pid: int, timeout: int, log_file) -> bool:
    """等待进程退出，超时则强制终止。返回 True 表示进程已退出"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _is_process_alive(pid):
            _log(log_file, f"主进程 {pid} 已退出")
            return True
        time.sleep(0.5)
    if not _is_process_alive(pid):
        _log(log_file, f"主进程 {pid} 已退出")
        return True
    _log(log_file, f"等待超时（{timeout}s），主进程仍存活，强制终止")
    _force_kill(pid, log_file)
    time.sleep(2)
    alive = _is_process_alive(pid)
    if alive:
        _log(log_file, f"强制终止后主进程 {pid} 仍存活，更新中止")
    else:
        _log(log_file, f"主进程 {pid} 已强制终止")
    return not alive


def _force_kill(pid: int, log_file) -> None:
    """强制终止进程（跨平台兜底）"""
    import signal

    try:
        if IS_WIN:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(3)
            if _is_process_alive(pid):
                os.kill(pid, signal.SIGKILL)
    except Exception as e:
        _log(log_file, f"强制终止失败: {e}")


def _popen_detached(cmd: list[str], cwd: str | None = None) -> subprocess.Popen:
    """以独立进程方式启动（脱离父进程，父进程退出不影响它）"""
    if IS_WIN:
        return subprocess.Popen(
            cmd,
            cwd=cwd,
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    return subprocess.Popen(cmd, cwd=cwd, start_new_session=True, close_fds=True)


def _wait_health(port: int, timeout: int, log_file) -> bool:
    """轮询健康检查端点，返回 True 表示新版本就绪"""
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    body = resp.read().decode("utf-8", errors="ignore")
                    if "running" in body:
                        _log(log_file, "新版本健康检查通过")
                        return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)
    _log(log_file, f"健康检查超时（{timeout}s）")
    return False


def _write_result(result_file: Path, result: str, error: str = "") -> None:
    """写入更新结果标记，供主程序下次启动时读取展示"""
    data = {
        "result": result,
        "error": error,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        result_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _rollback(backup_dir: Path, exe: Path, internal: Path, log_file) -> None:
    """从 backup 恢复旧版本（删除当前文件，移回备份）"""
    try:
        if exe.exists():
            try:
                exe.unlink()
            except Exception:
                pass
        if internal.exists():
            shutil.rmtree(internal, ignore_errors=True)
        backup_exe = backup_dir / EXE_NAME
        backup_internal = backup_dir / "_internal"
        if backup_exe.exists():
            shutil.move(str(backup_exe), str(exe))
        if backup_internal.exists():
            shutil.move(str(backup_internal), str(internal))
        _log(log_file, "回滚完成")
    except Exception as e:
        _log(log_file, f"回滚过程出错：{e}")


def _detect_strip_prefix(zf: zipfile.ZipFile) -> str:
    """检测 zip 内是否有一层包裹目录，返回需剥离的前缀。

    以 exe 位置为锚点：exe 在顶层则无包裹（返回 ''），否则取其所在子目录为前缀。
    兼容 package_release_zip 生成的扁平结构与手动压缩带包裹目录的两种包。
    """
    names = zf.namelist()
    if any(n == EXE_NAME for n in names):
        return ""
    for n in names:
        parts = n.split("/")
        if parts[-1] == EXE_NAME and len(parts) >= 2:
            return parts[0] + "/"
    return ""


def main() -> int:
    if len(sys.argv) < 5:
        print("用法: updater <pid> <zip_path> <app_dir> <health_port> [health_timeout]")
        return 1

    pid = int(sys.argv[1])
    zip_path = Path(sys.argv[2])
    app_dir = Path(sys.argv[3])
    health_port = int(sys.argv[4])
    health_timeout = int(sys.argv[5]) if len(sys.argv) > 5 else 60

    cache_dir = zip_path.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_dir = app_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "updater.log").open("a", encoding="utf-8")
    result_file = cache_dir / "result.json"

    exe = app_dir / EXE_NAME
    internal = app_dir / "_internal"
    self_name = os.path.basename(sys.executable)
    backup_dir = cache_dir / f"backup_{int(time.time())}"

    _log(log_file, "=" * 50)
    _log(
        log_file,
        f"启动更新器: pid={pid} zip={zip_path} app_dir={app_dir} port={health_port}",
    )

    # 1. 等待主进程退出（文件解锁前置条件）
    if not _wait_process_exit(pid, 30, log_file):
        _write_result(result_file, "failed", error="主进程未退出")
        _log(log_file, "主进程未退出，终止更新")
        return 1

    # 2. 备份旧版本（同盘 rename，瞬间完成）
    _log(log_file, f"备份旧版本到 {backup_dir}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        if exe.exists():
            shutil.move(str(exe), str(backup_dir / EXE_NAME))
        if internal.exists():
            shutil.move(str(internal), str(backup_dir / "_internal"))
    except Exception as e:
        _log(log_file, f"备份失败: {e}")
        _write_result(result_file, "failed", error=f"备份失败: {e}")
        return 1
    _log(log_file, "备份完成")

    # 3. 校验 CRC 并解压新包（自动剥离包裹目录、跳过 updater 自身）
    try:
        if not zip_path.exists():
            raise FileNotFoundError(f"更新包不存在: {zip_path}")
        app_dir_resolved = app_dir.resolve()
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise ValueError(f"压缩包损坏: {bad}")
            strip = _detect_strip_prefix(zf)
            if strip:
                _log(log_file, f"检测到包裹目录 {strip}，解压时剥离")
            _log(log_file, "完整性校验通过，开始解压")
            for m in zf.infolist():
                name = m.filename
                if name == self_name:
                    continue
                if strip and name.startswith(strip):
                    name = name[len(strip) :]
                if not name:
                    continue
                target = (app_dir / name).resolve()
                if not target.is_relative_to(app_dir_resolved):
                    raise ValueError(f"非法路径: {m.filename}")
                if m.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(m) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    except Exception as e:
        _log(log_file, f"解压失败: {e}，执行回滚")
        _rollback(backup_dir, exe, internal, log_file)
        _write_result(result_file, "rolled_back", error=f"解压失败: {e}")
        if exe.exists():
            _popen_detached([str(exe)], cwd=str(app_dir))
        return 1
    _log(log_file, "解压完成")

    # 4. 启动新版本
    if not exe.exists():
        _log(log_file, "新版本可执行文件缺失，回滚")
        _rollback(backup_dir, exe, internal, log_file)
        _write_result(result_file, "rolled_back", error="新版本可执行文件缺失")
        if exe.exists():
            _popen_detached([str(exe)], cwd=str(app_dir))
        return 1

    _log(log_file, "启动新版本")
    try:
        new_proc = _popen_detached([str(exe)], cwd=str(app_dir))
    except Exception as e:
        _log(log_file, f"启动新版本失败: {e}，回滚")
        _rollback(backup_dir, exe, internal, log_file)
        _write_result(result_file, "rolled_back", error=f"启动失败: {e}")
        if exe.exists():
            _popen_detached([str(exe)], cwd=str(app_dir))
        return 1

    # 5. 健康检查，失败则终止新进程并回滚
    if not _wait_health(health_port, health_timeout, log_file):
        _log(log_file, "新版本健康检查未通过，终止并回滚")
        try:
            new_proc.kill()
        except Exception:
            pass
        time.sleep(2)
        _rollback(backup_dir, exe, internal, log_file)
        _write_result(result_file, "rolled_back", error="新版本健康检查未通过")
        _log(log_file, "启动旧版本")
        if exe.exists():
            _popen_detached([str(exe)], cwd=str(app_dir))
        return 1

    # 6. 成功，清理备份
    _log(log_file, "更新成功，清理备份")
    shutil.rmtree(backup_dir, ignore_errors=True)
    _write_result(result_file, "success")
    return 0


if __name__ == "__main__":
    sys.exit(main())
