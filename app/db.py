"""SQLite 存储层：工具配置、进程记录、全局设置。

约定：
- 所有时间戳为本地时间 ISO 字符串。
- processes.status 取值：running=运行中 / exited=自行退出 /
  failed=启动失败 / stopped=被用户停止。
- 唯一部分索引保证每个工具同一时刻至多一条 running 记录。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "nav.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  group_name TEXT NOT NULL DEFAULT '默认分组',
  icon TEXT NOT NULL DEFAULT '🔧',
  url TEXT NOT NULL DEFAULT '',
  start_command TEXT NOT NULL DEFAULT '',
  work_dir TEXT NOT NULL DEFAULT '',
  port INTEGER,
  startup_timeout_ms INTEGER NOT NULL DEFAULT 30000,
  no_port_wait_ms INTEGER NOT NULL DEFAULT 1000,
  stop_command TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  extra TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tool_id INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
  pid INTEGER,
  create_time REAL,
  status TEXT NOT NULL DEFAULT 'running',
  started_by TEXT NOT NULL DEFAULT 'nav',
  started_at TEXT NOT NULL,
  stopped_at TEXT,
  exit_code INTEGER,
  log_file TEXT
);

-- 每个工具同一时刻至多一条运行中的进程记录
CREATE UNIQUE INDEX IF NOT EXISTS ux_process_running
  ON processes(tool_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# 首次启动的示例数据：演示三种典型工具形态
_SEED_TOOLS = [
    ("示例 · 纯链接导航", "不需要启动后端的普通网址，点卡片直接打开。",
     "示例", "🔗", "https://github.com", "", "", None, 0),
    ("示例 · 本地静态服务器", "演示完整链路：点击后后台执行启动命令 → 等待端口就绪 → 自动打开页面。",
     "示例", "🧪", "http://127.0.0.1:8765",
     "python -m http.server 8765 --bind 127.0.0.1", "", 8765, 1),
    ("示例 · 仅启动的命令", "没有 URL 的 CLI 工具：点卡片只负责后台拉起/停止，不打开网页。",
     "示例", "⏱️", "", "ping -t 127.0.0.1", "", None, 2),
]

TOOL_FIELDS = ("name", "description", "group_name", "icon", "url", "start_command",
               "work_dir", "port", "startup_timeout_ms", "no_port_wait_ms",
               "stop_command", "sort_order", "enabled")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) AS c FROM tools").fetchone()["c"] == 0:
            conn.executemany(
                """INSERT INTO tools
                   (name, description, group_name, icon, url, start_command,
                    work_dir, port, sort_order, enabled, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,1,?,?)""",
                [(*row, _now(), _now()) for row in _SEED_TOOLS],
            )
            conn.commit()


# ---------- tools ----------

def list_tools() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tools ORDER BY sort_order, id").fetchall()
    return [dict(r) for r in rows]


def get_tool(tool_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM tools WHERE id=?", (tool_id,)).fetchone()
    return dict(row) if row else None


def create_tool(data: dict) -> dict:
    values = {k: data.get(k) for k in TOOL_FIELDS}
    values["enabled"] = int(bool(values.get("enabled", True)))
    now = _now()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO tools
               (name, description, group_name, icon, url, start_command, work_dir,
                port, startup_timeout_ms, no_port_wait_ms, stop_command, sort_order,
                enabled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (values["name"], values["description"], values["group_name"],
             values["icon"], values["url"], values["start_command"],
             values["work_dir"], values["port"], values["startup_timeout_ms"],
             values["no_port_wait_ms"], values["stop_command"], values["sort_order"],
             values["enabled"], now, now),
        )
        conn.commit()
        tool_id = cur.lastrowid
    return get_tool(tool_id)


def update_tool(tool_id: int, data: dict) -> dict:
    values = {k: data.get(k) for k in TOOL_FIELDS}
    values["enabled"] = int(bool(values.get("enabled", True)))
    sets = ", ".join(f"{k}=?" for k in TOOL_FIELDS)
    with _conn() as conn:
        conn.execute(
            f"UPDATE tools SET {sets}, updated_at=? WHERE id=?",
            (*[values[k] for k in TOOL_FIELDS], _now(), tool_id),
        )
        conn.commit()
    return get_tool(tool_id)


def delete_tool(tool_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM tools WHERE id=?", (tool_id,))
        conn.commit()


# ---------- processes ----------

def insert_process(tool_id: int, pid: int | None, status: str = "running",
                   started_by: str = "nav", log_file: str | None = None,
                   create_time: float | None = None) -> dict:
    """新增进程记录。若该工具已存在 running 记录（并发竞态），返回已有记录。"""
    conn = _conn()
    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO processes
                   (tool_id, pid, create_time, status, started_by, started_at, log_file)
                   VALUES (?,?,?,?,?,?,?)""",
                (tool_id, pid, create_time, status, started_by, _now(), log_file),
            )
            rec_id = cur.lastrowid
    except sqlite3.IntegrityError:
        existing = active_process_for_tool(tool_id)
        if existing:
            return existing
        raise
    finally:
        conn.close()
    return get_process(rec_id)


def get_process(rec_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM processes WHERE id=?", (rec_id,)).fetchone()
    return dict(row) if row else None


def active_process_for_tool(tool_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            """SELECT * FROM processes
               WHERE tool_id=? AND status='running'
               ORDER BY id DESC LIMIT 1""", (tool_id,)).fetchone()
    return dict(row) if row else None


def list_running_processes() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT p.*, t.name AS tool_name FROM processes p
               LEFT JOIN tools t ON t.id = p.tool_id
               WHERE p.status='running' ORDER BY p.id""").fetchall()
    return [dict(r) for r in rows]


def list_finished_processes(limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT p.*, t.name AS tool_name FROM processes p
               LEFT JOIN tools t ON t.id = p.tool_id
               WHERE p.status != 'running' ORDER BY p.id DESC LIMIT ?""",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def finish_process(rec_id: int, status: str, exit_code: int | None = None) -> None:
    """结束一条运行中的记录（仅当其仍为 running 时生效，避免覆盖历史）。"""
    with _conn() as conn:
        conn.execute(
            """UPDATE processes
               SET status=?, stopped_at=?, exit_code=?
               WHERE id=? AND status='running'""",
            (status, _now(), exit_code, rec_id))
        conn.commit()


def latest_log_for_tool(tool_id: int) -> str | None:
    with _conn() as conn:
        row = conn.execute(
            """SELECT log_file FROM processes
               WHERE tool_id=? AND log_file IS NOT NULL
               ORDER BY id DESC LIMIT 1""", (tool_id,)).fetchone()
    return row["log_file"] if row else None


# ---------- settings（预留扩展） ----------

def get_setting(key: str, default: str | None = None) -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value))
        conn.commit()
