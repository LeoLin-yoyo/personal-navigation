"""HTTP API。所有接口仅供本机使用（服务默认只绑定 127.0.0.1）。"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from . import db
from . import process_manager as pm

router = APIRouter(prefix="/api")


class ToolIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    group_name: str = "默认分组"
    icon: str = "🔧"
    url: str = ""
    start_command: str = ""
    work_dir: str = ""
    port: int | None = None
    startup_timeout_ms: int = Field(default=30000, ge=500, le=300000)
    no_port_wait_ms: int = Field(default=1000, ge=0, le=60000)
    stop_command: str = ""
    sort_order: int = 0
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("名称不能为空")
        return v

    @field_validator("group_name")
    @classmethod
    def _group(cls, v: str) -> str:
        return (v or "").strip() or "默认分组"

    @field_validator("icon")
    @classmethod
    def _icon(cls, v: str) -> str:
        return (v or "").strip() or "🔧"

    @field_validator("url", "start_command", "work_dir", "stop_command")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("url")
    @classmethod
    def _url_scheme(cls, v: str) -> str:
        # 没写协议前缀的链接（github.com / 127.0.0.1:8000）自动补 http://；
        # 已带任意 "scheme://" 的（https://、vscode:// 等）原样保留
        if v and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", v):
            v = "http://" + v
        return v

    @field_validator("port")
    @classmethod
    def _port(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if not (1 <= int(v) <= 65535):
            raise ValueError("端口必须在 1-65535 之间")
        return int(v)


@router.get("/health")
def health():
    return {"ok": True, "name": "个人导航站"}


@router.get("/tools")
def list_tools():
    tools = db.list_tools()
    with ThreadPoolExecutor(max_workers=16) as ex:
        runtimes = list(ex.map(pm.tool_status, tools))
    return {"tools": [{**t, "runtime": r} for t, r in zip(tools, runtimes)]}


@router.post("/tools")
def create_tool(data: ToolIn):
    return db.create_tool(data.model_dump())


def _get_tool_or_404(tool_id: int) -> dict:
    tool = db.get_tool(tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    return tool


@router.put("/tools/{tool_id}")
def update_tool(tool_id: int, data: ToolIn):
    _get_tool_or_404(tool_id)
    return db.update_tool(tool_id, data.model_dump())


@router.delete("/tools/{tool_id}")
def delete_tool(tool_id: int):
    tool = _get_tool_or_404(tool_id)
    note = ""
    try:
        r = pm.stop_tool(tool)
        if r.get("ok"):
            note = "已停止其运行中的进程；"
        elif "没有运行中的进程" not in (r.get("message") or ""):
            note = f"进程停止未确认（{r.get('message')}）；"
    except Exception:
        note = "停止进程时出现异常；"
    db.delete_tool(tool_id)
    return {"ok": True, "message": f"{note}工具已删除"}


@router.post("/tools/{tool_id}/start")
def start_tool(tool_id: int):
    tool = _get_tool_or_404(tool_id)
    return pm.start_tool(tool)


@router.post("/tools/{tool_id}/stop")
def stop_tool(tool_id: int):
    tool = _get_tool_or_404(tool_id)
    return pm.stop_tool(tool)


@router.get("/tools/{tool_id}/logs")
def tool_logs(tool_id: int, lines: int = Query(default=400, ge=1, le=2000)):
    _get_tool_or_404(tool_id)
    log_name = db.latest_log_for_tool(tool_id)
    rec = db.active_process_for_tool(tool_id)
    content = (pm.tail_log(log_name, max_lines=lines) if log_name
               else "（还没有日志 —— 该工具尚未启动过）")
    return {"log_file": log_name, "content": content,
            "pid": rec["pid"] if rec else None}


@router.get("/processes")
def list_processes(limit: int = Query(default=100, ge=1, le=500)):
    return {"active": db.list_running_processes(),
            "history": db.list_finished_processes(limit)}


@router.get("/processes/{process_id}/logs")
def process_logs(process_id: int, lines: int = Query(default=400, ge=1, le=2000)):
    rec = db.get_process(process_id)
    if not rec:
        raise HTTPException(status_code=404, detail="进程记录不存在")
    content = (pm.tail_log(rec["log_file"], max_lines=lines)
               if rec["log_file"] else "（该进程没有日志）")
    return {"log_file": rec["log_file"], "content": content, "pid": rec["pid"]}
