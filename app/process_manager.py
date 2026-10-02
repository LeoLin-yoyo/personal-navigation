"""进程管理核心：启动、就绪探测、优雅/强制停止、外部进程接管、状态汇总。

信任模型：本服务仅绑定 127.0.0.1、面向单用户本机使用。“启动命令/停止命令”
是用户在管理界面为自己工具配置的受信命令行（与用户自己在终端输入等价），
服务端不接收任何外部输入去拼装命令。

设计要点
--------
1. 防重复启动（三重保险，命中任一即不再执行启动命令）：
   a. 工具配置了端口 → 探测 127.0.0.1:port 是否已有监听，
      可识别用户在终端手动启动的进程（登记为“外部进程”）；
   b. 存在导航站启动且 PID 仍存活的记录（附带进程创建时间校验，防 PID 复用误判）；
   c. 启动动作持有 per-tool 互斥锁，防止双击竞态。
2. 静默运行：子进程以 CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP 创建，
   拥有独立隐藏控制台；导航站退出、其控制台关闭都不影响工具后端继续运行。
   命令行通过 [cmd.exe, /d, /s, /c, 命令] 参数列表交给 cmd 解释
   （不使用 shell=True），以便支持 npm/yarn 等批处理入口及系统 PATH 解析。
3. 日志：stdout/stderr 重定向到 logs/ 下的文件，页面可查看尾部。
4. 停止：导航站启动的进程依次尝试 自定义停止命令 → CTRL_BREAK 优雅停止
   （3 秒超时）→ taskkill /T /F 强杀整棵进程树；
   外部进程直接强杀（避免向用户终端的控制台发送控制事件）。
"""
from __future__ import annotations

import ctypes
import socket
import subprocess
import threading
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psutil

from . import db

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CTRL_BREAK_EVENT = 1
ATTACH_PARENT_PROCESS = -1  # DWORD(-1)，ctypes 自动转换

GRACEFUL_STOP_SECONDS = 3.0
FORCE_STOP_WAIT_SECONDS = 3.0

_start_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
# 控制台的附着/分离是进程级状态，优雅停止的整个流程必须串行执行
_ctrl_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cmdline(command: str) -> list[str]:
    """把用户配置的命令行包进 cmd 参数列表（等效 cmd /c，但不使用 shell=True）。

    /d 跳过 AutoRun 注册表脚本；/s 保证含引号命令行的剥离行为一致。
    """
    return ["cmd.exe", "/d", "/s", "/c", command]


# ---------- 探测 ----------

def probe_port(port: int | None, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    """探测本地端口是否有服务在监听（仅 TCP 握手，不发任何请求内容）。"""
    if not port:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, int(port))) == 0
    except OSError:
        return False


def find_pid_by_port(port: int) -> int | None:
    """通过 TCP 监听表反查占用端口的进程 PID（无权限或找不到时返回 None）。"""
    try:
        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == int(port) and c.status == psutil.CONN_LISTEN:
                if c.pid and c.pid > 0:
                    return c.pid
    except Exception:
        pass
    return None


def pid_alive(pid: int | None, create_time: float | None = None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    try:
        p = psutil.Process(int(pid))
        if not p.is_running():
            return False
        if create_time:
            try:
                if abs(p.create_time() - float(create_time)) > 1.0:
                    return False  # PID 已被系统复用给别的进程
            except psutil.AccessDenied:
                pass
        return True
    except psutil.Error:
        return False


def record_alive(rec: dict) -> bool:
    """判断一条进程记录是否仍然存活。

    外部记录若无 PID（权限原因未能反查到），视为存活，
    由端口校准逻辑兜底（端口关闭则结束该记录）。
    """
    if rec["started_by"] == "external" and not rec["pid"]:
        return True
    return pid_alive(rec["pid"], rec["create_time"])


def _pid_create_time(pid: int | None) -> float | None:
    if not pid:
        return None
    try:
        return psutil.Process(int(pid)).create_time()
    except psutil.Error:
        return None


# ---------- 启动 ----------

def _spawn(tool: dict) -> tuple[int, float | None, Path]:
    """静默启动工具命令，返回 (pid, 进程创建时间, 日志文件路径)。

    记录的 PID 是 cmd.exe 的（它会等子进程退出），
    CTRL_BREAK 事件会送达同一进程组内的实际服务进程。
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"tool{tool['id']}_{stamp}.log"
    with open(log_file, "ab") as logf:
        logf.write(
            f"—— 导航站于 {_now()} 启动 ——\n"
            f"命令: {tool['start_command']}\n"
            f"工作目录: {tool['work_dir'] or '(继承导航站)'}\n\n".encode("utf-8")
        )
        logf.flush()
        try:
            proc = subprocess.Popen(
                _cmdline(tool["start_command"]),
                cwd=tool["work_dir"] or None,
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
        except Exception:
            logf.write(
                f"\n[导航站错误] 启动命令执行失败：\n{traceback.format_exc()}\n".encode("utf-8"))
            raise
    return proc.pid, _pid_create_time(proc.pid), log_file


def _wait_ready(tool: dict, pid: int | None) -> tuple[bool, str | None]:
    """等待后端就绪。配置了端口则轮询端口，否则等待进程稳定存活。"""
    port = tool["port"]
    if port:
        timeout_ms = max(500, tool["startup_timeout_ms"] or 30000)
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if probe_port(port):
                return True, None
            if not pid_alive(pid):
                return False, "进程在启动过程中退出了（详见日志）"
            time.sleep(0.4)
        if pid_alive(pid):
            return False, (f"已等待 {timeout_ms // 1000} 秒，端口 {port} 仍未就绪"
                           "（进程仍在运行，可能启动较慢或端口配置有误，详见日志）")
        return False, "启动超时，且进程已退出（详见日志）"

    wait_ms = tool["no_port_wait_ms"] or 1000
    deadline = time.monotonic() + wait_ms / 1000
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return False, "进程启动后很快退出了（详见日志）"
        time.sleep(0.15)
    if pid_alive(pid):
        return True, None
    return False, "进程已退出（详见日志）"


def start_tool(tool: dict) -> dict:
    """确保工具后端在运行；已在运行则不重复执行启动命令（阻塞直至就绪/超时）。"""
    with _start_locks[tool["id"]]:
        # 1) 端口已被监听 → 视为已运行（含用户手动启动的情形），绝不重复拉起
        if probe_port(tool["port"]):
            rec = ensure_external_record(tool)
            return {"ok": True, "already": True, "pid": rec["pid"],
                    "message": f"检测到端口 {tool['port']} 已有服务监听，未重复启动"}

        rec = db.active_process_for_tool(tool["id"])
        if rec:
            if rec["started_by"] == "nav" and record_alive(rec):
                if tool["port"] and not probe_port(tool["port"]):
                    # 刚点过启动还没就绪：再等一轮，而不是报错
                    ok, msg = _wait_ready(tool, rec["pid"])
                    return {"ok": ok, "already": True, "pid": rec["pid"],
                            "message": msg or "进程已在运行",
                            "log_tail": None if ok else _tail_by_name(rec["log_file"])}
                return {"ok": True, "already": True, "pid": rec["pid"],
                        "message": "进程已在运行，未重复启动"}
            # 死亡/残留记录：清理后重新启动
            db.finish_process(rec["id"], "exited")

        if not (tool["start_command"] or "").strip():
            return {"ok": False, "message": "该工具没有配置启动命令"}

        try:
            pid, ct, log_file = _spawn(tool)
        except Exception as exc:
            return {"ok": False, "message": f"启动命令执行失败：{exc}"}

        rec = db.insert_process(tool["id"], pid, "running", "nav",
                                log_file.name, create_time=ct)
        ok, msg = _wait_ready(tool, pid)
        if not ok and not pid_alive(pid, ct):
            db.finish_process(rec["id"], "failed")
            return {"ok": False, "message": msg, "log_tail": _tail_by_name(log_file.name)}
        return {"ok": ok, "already": False, "pid": pid, "message": msg,
                "log_tail": None if ok else _tail_by_name(log_file.name)}


# ---------- 停止 ----------

def _wait_pid_gone(pid: int | None, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.15)
    return not pid_alive(pid)


def _send_ctrl_break(pid: int) -> bool:
    """向目标进程组发送 CTRL_BREAK（优雅停止）。尽力而为，失败走强杀兜底。

    子进程创建于独立隐藏控制台（CREATE_NO_WINDOW），这里临时附着到它的
    控制台生成事件，再恢复本进程原来的控制台附着状态。
    """
    if not pid or int(pid) <= 0:
        return False
    with _ctrl_lock:
        try:
            kernel32 = ctypes.windll.kernel32
            had_console = bool(kernel32.GetConsoleWindow())
            try:
                kernel32.FreeConsole()
            except Exception:
                pass
            if not kernel32.AttachConsole(int(pid)):
                if had_console:
                    kernel32.AttachConsole(ATTACH_PARENT_PROCESS)
                return False
            kernel32.SetConsoleCtrlHandler(None, True)  # 忽略可能回传给自己的事件
            ok = bool(kernel32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, int(pid)))
            kernel32.SetConsoleCtrlHandler(None, False)
            kernel32.FreeConsole()
            if had_console:
                # 恢复失败仅意味着本进程此后没有控制台，不影响服务本身
                kernel32.AttachConsole(ATTACH_PARENT_PROCESS)
            return ok
        except Exception:
            return False


def _force_kill_tree(pid: int | None) -> None:
    if not pid or int(pid) <= 0:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True, timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass
    if pid_alive(pid):  # taskkill 失败时最后兜底
        try:
            psutil.Process(int(pid)).kill()
        except psutil.Error:
            pass


def _run_stop_command(tool: dict, rec: dict) -> None:
    """执行工具配置的自定义停止命令，输出追加到该进程的日志。"""
    logf = None
    try:
        if rec["log_file"]:
            logf = open(LOG_DIR / Path(rec["log_file"]).name, "ab")
        subprocess.run(
            _cmdline(tool["stop_command"]), timeout=15,
            stdout=logf or subprocess.DEVNULL, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        if logf:
            logf.write(f"\n[导航站错误] 停止命令执行失败：{exc}\n".encode("utf-8"))
    finally:
        if logf:
            logf.close()


def stop_tool(tool: dict) -> dict:
    """停止工具的后端进程：优雅优先，超时强杀整棵进程树。"""
    rec = db.active_process_for_tool(tool["id"])
    if rec:
        pid = rec["pid"]
        via = None

        if rec["started_by"] == "nav" and (tool["stop_command"] or "").strip():
            _run_stop_command(tool, rec)
            if _wait_pid_gone(pid, GRACEFUL_STOP_SECONDS):
                via = "自定义停止命令"

        if not via and rec["started_by"] == "nav" and pid:
            if _send_ctrl_break(int(pid)) and _wait_pid_gone(pid, GRACEFUL_STOP_SECONDS):
                via = "CTRL_BREAK 优雅停止"

        if not via:
            if pid and pid_alive(pid):
                _force_kill_tree(pid)
                if _wait_pid_gone(pid, FORCE_STOP_WAIT_SECONDS):
                    via = "强制结束进程树"
                else:
                    return {"ok": False, "pid": pid,
                            "message": f"停止失败：进程仍存活，请手动处理（PID {pid}）"}
            elif rec["started_by"] == "external" and tool["port"] and probe_port(tool["port"]):
                return {"ok": False,
                        "message": "端口仍在监听但无法定位进程 PID（可能需要管理员权限），请手动关闭"}
            else:
                via = "进程已不在运行"
        db.finish_process(rec["id"], "stopped")
        return {"ok": True, "pid": pid, "message": f"已停止（{via}）"}

    # 没有记录但端口在监听 → 外部进程，直接强杀
    if tool["port"] and probe_port(tool["port"]):
        pid = find_pid_by_port(tool["port"])
        if pid:
            _force_kill_tree(pid)
            if _wait_pid_gone(pid, FORCE_STOP_WAIT_SECONDS):
                return {"ok": True, "pid": pid, "message": "已强制结束外部进程"}
            return {"ok": False, "pid": pid, "message": "停止失败：进程仍存活，请手动处理"}
        return {"ok": False,
                "message": "无法定位占用端口的进程 PID（可能需要管理员权限），请手动关闭"}

    return {"ok": False, "message": "该工具当前没有运行中的进程"}


# ---------- 状态汇总与外部接管 ----------

def ensure_external_record(tool: dict) -> dict:
    """端口已被监听但导航站没有运行记录时，登记一条“外部进程”记录。"""
    existing = db.active_process_for_tool(tool["id"])
    if existing:
        return existing
    pid = find_pid_by_port(tool["port"]) if tool["port"] else None
    try:
        return db.insert_process(tool["id"], pid, "running", "external", None,
                                 create_time=_pid_create_time(pid))
    except Exception:
        existing = db.active_process_for_tool(tool["id"])
        if existing:
            return existing
        raise


def tool_status(tool: dict) -> dict:
    """计算工具当前运行状态（顺带校准数据库记录）。"""
    rec = db.active_process_for_tool(tool["id"])
    if rec:
        dead_nav = rec["started_by"] == "nav" and not record_alive(rec)
        stale_ext = (rec["started_by"] == "external"
                     and tool["port"] and not probe_port(tool["port"]))
        if dead_nav or stale_ext:
            db.finish_process(rec["id"], "exited")
            rec = None
    if rec:
        return {"status": "running", "source": rec["started_by"], "pid": rec["pid"],
                "port_ready": probe_port(tool["port"]) if tool["port"] else None,
                "process_id": rec["id"], "started_at": rec["started_at"]}
    if tool["port"] and probe_port(tool["port"]):
        rec = ensure_external_record(tool)
        return {"status": "running", "source": "external", "pid": rec["pid"],
                "port_ready": True, "process_id": rec["id"], "started_at": rec["started_at"]}
    return {"status": "stopped", "source": None, "pid": None,
            "port_ready": None, "process_id": None, "started_at": None}


def reconcile() -> None:
    """周期校准：清理死亡记录、把端口上的外部进程登记进来。"""
    for rec in db.list_running_processes():
        tool = db.get_tool(rec["tool_id"])
        if rec["started_by"] == "nav":
            if not record_alive(rec):
                db.finish_process(rec["id"], "exited")
        else:  # 外部记录必须依托端口存在
            if not tool or not tool["port"] or not probe_port(tool["port"]):
                db.finish_process(rec["id"], "exited")
    for tool in db.list_tools():
        if not tool["port"] or db.active_process_for_tool(tool["id"]):
            continue
        if probe_port(tool["port"]):
            ensure_external_record(tool)


# ---------- 日志 ----------

def tail_log(log_name: str, max_bytes: int = 96 * 1024, max_lines: int = 400) -> str:
    path = LOG_DIR / Path(log_name).name
    if not path.exists():
        return "(日志文件不存在)"
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    if size > max_bytes:
        text = text.split("\n", 1)[-1]  # 丢弃被截断的半行
    return "\n".join(text.splitlines()[-max_lines:])


def _tail_by_name(log_name: str | None) -> str | None:
    if not log_name:
        return None
    try:
        return tail_log(log_name, max_lines=40)
    except Exception:
        return None
