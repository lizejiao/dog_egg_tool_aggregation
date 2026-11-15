"""全局路径与常量定义。

所有运行时数据（数据库、配置、音频工件）统一收敛在 backend/data/ 下，
该目录已被 .gitignore 忽略，其中 config.json 含 apiKey，禁止入库。
"""
import os

# 项目根目录：backend/ 的上一级
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
PROJECT_DIR = os.path.dirname(BACKEND_DIR)                                # voice-script-studio/
# 前端目录：优先新版构建产物（frontend-vue/dist），未构建时回退归档版（frontend-legacy/，
# P3 前的旧单页）。两者并存期可用 VSS_FRONTEND_DIR 强制指定。
_DIST_DIR = os.path.join(PROJECT_DIR, "frontend-vue", "dist")
_LEGACY_DIR = os.path.join(PROJECT_DIR, "frontend-legacy")
FRONTEND_DIR = os.environ.get("VSS_FRONTEND_DIR") or (
    _DIST_DIR if os.path.isfile(os.path.join(_DIST_DIR, "index.html")) else _LEGACY_DIR)

# 运行数据目录（全部 gitignore）。测试时可用 VSS_DATA_DIR 指向临时目录隔离。
DATA_DIR = os.environ.get(
    "VSS_DATA_DIR",
    os.path.join(BACKEND_DIR, "data"),
)
DB_PATH = os.path.join(DATA_DIR, "vss.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

# 音频工件目录
VOICES_DIR = os.path.join(DATA_DIR, "voices")          # 克隆音色的参考音频归档
PREVIEWS_DIR = os.path.join(VOICES_DIR, "previews")    # 试听缓存（每音色只真实合成一次）
TASKS_DIR = os.path.join(DATA_DIR, "tasks")            # 每个任务一个子目录：分段音频 + 整稿

# 上传约束
MAX_SCRIPT_BYTES = 2 * 1024 * 1024     # 稿件上传上限 2MB
MAX_VOICE_BYTES = 20 * 1024 * 1024     # 参考音频上限 20MB
SCRIPT_EXTS = {".txt", ".md"}          # 稿件白名单
VOICE_EXTS = {".wav", ".mp3", ".m4a", ".opus"}  # 参考音频白名单（Fish 官方支持格式）


def ensure_dirs() -> None:
    """确保所有运行目录存在（应用启动与测试前调用一次）。"""
    for d in (DATA_DIR, VOICES_DIR, PREVIEWS_DIR, TASKS_DIR):
        os.makedirs(d, exist_ok=True)
