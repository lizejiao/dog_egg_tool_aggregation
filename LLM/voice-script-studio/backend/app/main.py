"""FastAPI 服务入口。

启动方式（backend/ 目录下）：
    uvicorn app.main:app --port 8410
浏览器打开 http://127.0.0.1:8410 即进入 WebUI。
"""
import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import settings
from app.api import providers, tasks, upload, voices
from app.pipeline import encoder
from app.store import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("vss.main")

app = FastAPI(
    title="Voice Script Studio",
    version="0.1.0",
    description="演讲稿 → 克隆音色配音（自建编排框架 + 可切换语音引擎）",
)

# 业务路由
app.include_router(voices.router)
app.include_router(tasks.router)
app.include_router(providers.router)
app.include_router(upload.router)


@app.on_event("startup")
def _startup() -> None:
    """建表 + 目录初始化（幂等）。"""
    settings.ensure_dirs()
    db.init_db()
    logger.info("Voice Script Studio 启动完成，数据目录：%s", settings.DATA_DIR)


@app.get("/api/meta/exports")
def meta_exports() -> dict:
    """导出格式可用性（前端下载按钮置灰判断；lameenc 缺失等场景前置提示）。"""
    return encoder.availability()


@app.get("/")
def index() -> FileResponse:
    """WebUI 入口（单页应用）。"""
    return FileResponse(os.path.join(settings.FRONTEND_DIR, "index.html"))


# 前端静态资源（js/css）挂载
app.mount("/static", StaticFiles(directory=os.path.join(settings.FRONTEND_DIR, "static")),
          name="static")
