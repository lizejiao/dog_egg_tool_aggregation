# Voice Script Studio

演讲稿 → 你的音色配音，一台 MacBook Air 上的本地可视化工具。

## 这是什么

一个自建的「文本转语音」编排框架 + WebUI（v1 已实现）：

- **稿件**：粘贴文本或上传 `.txt` / `.md` 演讲稿（Markdown 自动清洗、智能分句）
- **音色**：上传 10~30 秒参考音频 + 逐字稿，**或直接在浏览器里照提示词录音克隆**（麦克风采集 + 前端编 WAV，提示词即逐字稿，保证稿音一致）；试听一次生成、缓存回放；音色库页支持**手动同步**（网页端新建音色即时拉取，无需等缓存过期）
- **引擎可切换**：云端 Fish S2.1 Pro（REST 直调，不依赖官方 SDK）与本地引擎通过统一 Provider 接口随时切换，切换 = 改配置
- **批量可靠**：长稿自动分句、逐段合成、断点续跑、失败段单独重试、整稿拼接下载
- **多格式导出**：下载音频三选一——WAV（原音质）/ M4A（AAC，体积约 1/5）/ MP3（128kbps，兼容性最好）；按需转码 + 缓存，重合成后自动重转
- **完全自建编排**：无 LangGraph / Pipecat 等编排框架；模型与推理引擎层用公开 REST / 运行时

## 快速开始

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8410
# 浏览器打开 http://127.0.0.1:8410
```

1. **引擎设置**页：粘贴 fish.audio 的 apiKey → 点「测试连接」（或先用 Mock 引擎离线体验）
2. **音色库**页：照提示词直接录音（推荐，浏览器请求麦克风权限，仅 localhost/HTTPS 可用），或上传参考音频 + 逐字稿创建克隆音色
3. **合成工作台**：粘贴演讲稿 → 选音色/引擎 → 开始合成 → 下载整稿

测试（隔离数据目录，不影响真实数据）：

```bash
cd backend
VSS_DATA_DIR=$(mktemp -d /tmp/vss-test-XXXX) .venv/bin/python -m unittest discover -s tests -v
```

## 目录结构

```
backend/
├── app/
│   ├── main.py            # FastAPI 入口（uvicorn app.main:app）
│   ├── settings.py        # 路径常量（VSS_DATA_DIR 可覆盖，测试隔离用）
│   ├── api/               # REST 路由：voices / tasks / providers / upload
│   ├── pipeline/          # text_clean（清洗）splitter（分句）assembler（拼接）runner（状态机）
│   ├── providers/         # base（抽象）fish_cloud（REST 直调）mock（离线自测）
│   └── store/             # db（SQLite）config（0600 配置 + 密钥脱敏）
├── data/                  # 运行数据（gitignore：db / config.json / 音频工件）
└── tests/                 # 54 项单测 + 端到端集成测试
frontend-vue/              # 新版 WebUI（Vite + Vue3 + Pinia；构建产物 dist/ 由 FastAPI 托管）
frontend-legacy/           # 旧版单页（原生 HTML/JS，P3 归档，仅作回滚参考）
docs/ARCHITECTURE.md       # 技术架构设计（含 v1 实现偏差说明）
```

## 安全与许可要点

- apiKey 只存 `backend/data/config.json`（0600 权限，gitignore，日志全脱敏）
- `s2.1-pro-free` 免费窗口至 **2026-11-30**：无 SLA、请求可能用于改进模型、个人用途；切付费仅改模型串（设置页下拉即换）
- 克隆仅限本人或已授权声线；本地引擎权重许可（未来 M3 接入时）需逐个确认
- 情绪标签 `[pause]` `[excited]` 等：Fish 引擎支持透传，其他引擎自动剥离

## 文档

| 文件 | 内容 |
|------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 技术架构设计 + ADR 决策记录 + v1 实现偏差 |

## 里程碑状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| M1 | Provider 抽象 + fish_cloud（REST 直调）+ mock + 文本管道 | ✅ 完成 |
| M2 | FastAPI + WebUI 四页面 | ✅ 完成 |
| M3 | 本地引擎接入（mlx-speech fish-s2-pro，需 Python 3.13 + 4.5GB 权重） | ⬜ 预留接口未实装 |
| M4 | 打磨（WebSocket 进度等） | ⬜ 部分（轮询已可用） |
