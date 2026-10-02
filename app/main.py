"""个人导航站入口：FastAPI 应用 + 静态页面 + 周期状态校准。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from . import process_manager as pm
from .api import router

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
RECONCILE_INTERVAL_SECONDS = 8


async def _reconcile_loop():
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(pm.reconcile)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    await asyncio.to_thread(pm.reconcile)
    task = asyncio.create_task(_reconcile_loop())
    yield
    task.cancel()
    # 有意设计：导航站退出时不杀掉由它启动的工具后端。
    # 后端进程独立存活，重新打开导航站会通过 PID 记录 / 端口探测自动重新识别。


app = FastAPI(title="个人导航站", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
