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
from app.api import config_api, providers, tasks, upload, voices
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
app.include_router(voices.ent_router)   # P2-0：音色实体子路由
app.include_router(tasks.router)
app.include_router(providers.router)
app.include_router(config_api.router)   # P2c：全局合成参数
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
    """WebUI 入口（单页应用；新版 dist 优先，未构建时回退 frontend-legacy）。"""
    return FileResponse(os.path.join(settings.FRONTEND_DIR, "index.html"))


# 前端静态资源挂载：新版 Vite 产物在 /assets，旧版在 /static（并存期两者都挂）
_ASSETS_DIR = os.path.join(settings.FRONTEND_DIR, "assets")
if os.path.isdir(_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
_LEGACY_STATIC = os.path.join(settings.FRONTEND_DIR, "static")
if os.path.isdir(_LEGACY_STATIC):
    app.mount("/static", StaticFiles(directory=_LEGACY_STATIC), name="static")
