"""pnw 的静默服务入口：由 pythonw.exe 运行（无任何控制台窗口）。

被 pnw.cmd（C:\\Users\\Administrator\\bin）和开机计划任务 PersonalNav 调用。
stdout/stderr 重定向到 logs/server.log，方便排查启动问题。
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)

_log = open(os.path.join(BASE, "logs", "server.log"), "a",
            encoding="utf-8", buffering=1)
sys.stdout = _log
sys.stderr = _log

import uvicorn

uvicorn.run(
    "app.main:app",
    host="127.0.0.1",
    port=int(os.environ.get("NAV_PORT", "8790")),
    log_level="info",
)
