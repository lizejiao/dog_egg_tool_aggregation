# Voice Script Studio 技术架构设计

> 版本：v1.0（2026-11-14）　状态：设计定稿，待实现
> 场景：演讲稿（txt / Markdown 文本）→ 用克隆音色批量生成配音音频，本地 WebUI 操作。

## 1. 项目概述

### 1.1 目标

| # | 能力 | 说明 |
|---|------|------|
| G1 | 自建编排框架 | 自建"文本管道 + 任务编排 + 引擎抽象"薄框架，直接消费外部语音大模型，不引入重型编排框架 |
| G2 | 引擎可随时切换 | 云端（Fish S2.1 Pro）与本地（mlx-speech / mlx-audio）通过统一 Provider 接口接入，配置即切换 |
| G3 | WebUI 可视化 | 单页 Web 应用：音色选择、上传音频克隆音色、语音模型切换（含 apiKey 输入）、语音稿输入与上传 |
| G4 | 稿件格式 | 文本输入 或 上传文件，仅支持 `.txt` / `.md` 两种格式 |
| G5 | 断点续跑 | 长稿分段合成，进程中断后可从断点继续，失败段可单独重试 |

### 1.2 非目标（明确不做）

- 实时语音对话 / 流式低延迟场景（不做 VAD、ASR、LLM 链路）
- 模型训练与微调（只做零样本克隆）
- 多用户系统（单机单用户工具，不做账号体系）
- 移动端适配（桌面浏览器优先）

### 1.3 硬件与运行环境

- MacBook Air（Apple Silicon，16GB 统一内存），macOS
- 云端引擎无本机算力要求；本地引擎峰值内存 6.4–7.1GB（fish-s2-pro int8），16GB 可行但需控制并发与常驻进程

## 2. 总体架构

### 2.1 分层视图

```
┌─────────────────────────────────────────────────────────────┐
│  WebUI（单页前端，无构建工具）                                  │
│  音色库页 · 合成工作台 · 引擎设置页 · 任务历史页                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP (REST + 轮询进度)
┌──────────────────────────▼──────────────────────────────────┐
│  API 服务层（FastAPI + uvicorn）                              │
│  /api/voices · /api/tasks · /api/providers · /api/upload     │
├─────────────────────────────────────────────────────────────┤
│  编排层（自建核心）                                            │
│  文本管道：md/txt 清洗 → 分句(40~80字) → 段落队列               │
│  任务引擎：状态机 → 逐段合成 → 拼接/停顿/响度归一 → 产物落盘      │
│  断点续跑：分段落盘即标记，重启跳过已完成段                       │
├─────────────────────────────────────────────────────────────┤
│  引擎适配层（Provider 抽象，配置切换）                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│  │ fish云   │ │ mlx-speech│ │ mlx-audio│ │  mock    │        │
│  │S2.1 Pro  │ │fish-s2-pro│ │Qwen3-TTS │ │ 离线自测  │        │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘         │
├─────────────────────────────────────────────────────────────┤
│  公共服务：SQLite 元数据 · 文件存储(音频工件) · 密钥管理 · 音频工具│
└─────────────────────────────────────────────────────────────┘
```

### 2.2 架构决策记录（ADR 摘要）

| 决策 | 结论 | 理由 / 放弃项 |
|------|------|--------------|
| 自建 vs 现成框架 | 自建薄编排 + Provider 抽象 | 云 API 接入只需官方 SDK；「演讲稿批量克隆」细分无成熟开源框架；Pipecat/LiveKit 为实时对话设计，属过度工程 |
| 后端框架 | FastAPI + uvicorn | 异步并发、自动 OpenAPI 文档、生态成熟；放弃 Flask（异步弱）与纯标准库（路由/上传/校验手写成本高） |
| 前端形态 | 单页 HTML + 原生 JS + CSS（无构建） | 单机工具无需 Node 构建链；可引入 CDN 版轻量库（如 Alpine.js）增强交互；放弃 React/Vue 工程化（重） |
| 元数据存储 | SQLite | 单文件、零运维、够用；文件类工件（音频、上传稿）直接落文件系统，库中只存路径与状态 |
| 云端引擎 | fish-audio-sdk（官方 Python SDK） | 官方维护、含异常类/流式/voices 管理；REST 直调仅作降级备选 |
| 本地引擎 | mlx-speech（fish-s2-pro）为主，mlx-audio（Qwen3-TTS）为备 | 与云端同源情绪标签语法可无损迁移；mlx-speech 项目较年轻，故保留 mlx-audio 备胎 |
| 引擎进程模型 | 云端同进程调用；本地引擎独立子进程调用 | 本地 MLX 模型加载慢、内存大，用"工作进程"方式按需拉起、空闲回收，避免常驻挤占 16GB 内存 |
| 进度推送 | 轮询（GET /tasks/{id}） | 实现简单可靠；WebSocket 留作 M4 可选增强 |
| 任务编排框架 | **不引入 LangGraph** | 本项目状态机为确定性直线（5 节点、无动态路由、无 LLM 在环），断点续跑用数据库行级状态即可；LangGraph 的价值（agent 图编排/条件路由/checkpoint 快照）与需求错配，且会带来 langchain-core 重依赖与调试黑盒。触发重评条件：未来引入 LLM 稿件预处理、TTS→ASR→LLM 质量校验闭环、人工审核卡点时 |

## 3. 技术栈清单

| 层 | 组件 | 版本/说明 |
|----|------|----------|
| 运行时 | Python | 3.13+（mlx-speech 硬性要求；用 uv 管理虚拟环境） |
| API | FastAPI + uvicorn + python-multipart | 文件上传需 python-multipart |
| 云端 SDK | fish-audio-sdk | `pip install fish-audio-sdk`，密钥走 `FISH_API_KEY` 或配置注入 |
| 本地引擎 | mlx-speech（fish-s2-pro int8） | 可选安装；Python 子进程调用 CLI/Python API |
| 本地备胎 | mlx-audio（Qwen3-TTS-Base-8bit） | 可选安装；同样子进程隔离 |
| 音频处理 | ffmpeg（优先）+ macOS afconvert（兜底） | 分段拼接、格式转换、静音插入、响度归一 |
| 元数据 | SQLite（内置 sqlite3） | 任务/音色/引擎配置三张核心表 |
| 前端 | 原生 HTML/JS/CSS 单页 | 桌面浏览器（Safari/Chrome） |
| 密钥存储 | `data/config.json`（0600 权限，gitignore） | API key 由 WebUI 输入后写入，绝不写日志、绝不进 git |

## 4. 核心模块设计

### 4.1 WebUI（四个页面）

**① 音色库页**
- 音色列表分两组展示：
  - **预置音色**（引擎自带，随引擎启用同步）：卡片含名称、来源引擎、试听按钮；克隆型引擎（无预置音色）显示"该引擎需上传参考音频"占位说明而非空白
  - **我的克隆音色**（用户上传创建）：卡片含名称、试听、各引擎注册状态、删除
- **试听机制（预置音色与克隆音色统一）**：点击试听 → 用该音色合成一句固定示例文本（约 30 字，含中英混读与情绪标签展示）→ 结果落盘缓存（`data/voices/previews/{voice_key}.wav`）→ 前端直接播放缓存；**同一音色只真实合成一次**，避免反复消耗云端配额（免费档 Fair Use 保护）
- 「上传克隆」：音频文件（wav/mp3/m4a，≤20MB）+ 逐字稿（必填，必须与录音内容一致）+ 音色名称 → 创建克隆音色
- 音色详情：参考音频回放、逐字稿展示、在各引擎的注册状态（云端 reference_id / 本地引用）
- 删除音色（联动删除云端引用与本地文件）

**② 合成工作台**
- 稿件输入：多行文本框直接粘贴 或 上传 `.txt`/`.md`（前端按扩展名+MIME 白名单校验，超时 20MB）
- 参数区：音色下拉（来自音色库）、引擎下拉（来自已启用 Provider）、语速、句间停顿时长、情绪标签开关（按引擎 capabilities 显隐）
- 操作：开始合成 → 进度条（已完成段/总段数、当前段耗时）→ 段落试听列表（失败段标红可重试）→ 整稿下载（wav/mp3）

**③ 引擎设置页**
- Provider 列表：名称、类型（cloud/local）、启用开关、健康状态
- 云端引擎：apiKey 输入（掩码显示）、模型串选择（`s2.1-pro-free` / `s2.1-pro`）、「测试连接」按钮
- 本地引擎：模型路径/选择器、预计内存占用提示、「测试加载」按钮
- 免责与提示：免费窗口截止时间、请求可能用于改进模型、许可证提示

**④ 任务历史页**
- 任务列表：时间、稿件名、引擎、音色、状态（进行中/完成/部分失败）、总时长
- 详情：进入工作台上下文，支持断点续跑与失败段重试

### 4.2 引擎适配层（本设计的核心）

统一接口（伪代码，实现时以此为准）：

```python
class TTSProvider(ABC):
    id: str
    kind: str                    # "cloud" | "local" | "mock"

    def capabilities(self) -> Caps:
        ...  # emotion_tags: bool, sample_rate: int, max_chars: int, concurrency: int

    def test_connection(self) -> Health:
        ...  # 云端：余额/鉴权；本地：模型可加载

    # 音色
    def list_voices(self) -> list[VoiceMeta]: ...
    def create_voice(self, ref_wav_path: str, ref_text: str, name: str) -> VoiceRef:
        ...  # 云端：voices.create() → reference_id；本地：记录 ref 文件+文本
    def delete_voice(self, voice_ref: VoiceRef) -> None: ...

    # 合成（单段文本 → 单段音频文件）
    def synthesize(self, text: str, voice: VoiceRef, params: SynthParams) -> SegmentResult:
        ...  # 返回音频路径、实际采样率、失败原因
```

适配器矩阵：

| Provider | 音色载体 | 预置音色 | 输出 | 情绪标签 | 并发 | 备注 |
|----------|----------|----------|------|----------|------|------|
| `fish_cloud` | 持久音色 `reference_id`（`voices.create` 建一次） | **有**：云端音色库（官方 + 社区 200 万+），`voices.list()` 同步到本地展示，引用 `reference_id` 即用 | mp3/wav 可选 | 支持（`[pause]` 等 15,000+ 标签原样透传） | ≤2（免费档新账号限流 5 并发，保守取 2） | 模型串可配：`s2.1-pro-free`（免费档）→`s2.1-pro`（付费，$15/百万 UTF-8 字节）；SDK 异常类做重试 |
| `mlx_fish_s2pro` | `ref_audio` + `ref_text` 文件（`prepare_reference` 一次，全篇复用） | 无（零样本克隆型，必须有参考音频） | wav 44.1kHz | 支持（与云端同语法） | 1 | 子进程按需拉起，任务完成回收；峰值 6.4–7.1GB |
| `mlx_qwen_tts` | `ref_audio` + `ref_text` | 无（Base 变体仅克隆；未来可增接 Qwen3-TTS CustomVoice 变体，自带 Chelsie/Serena/Ryan 等 9 个预设音色） | wav 24kHz | 不支持（合成前剥离标签） | 1 | 备胎，兼容引擎层验证 |
| `mock` | 任意 | 2 个演示音色 | 本地生成正弦波/静音 wav | 剥离 | 4 | 无网络、无模型也能全流程自测 |

> `list_voices()` 的返回 = 引擎**预置音色** ∪ 用户已注册到该引擎的**克隆音色**，前端据此分组展示；克隆型引擎返回空预置列表并附提示。

适配层统一兜底逻辑：采样率归一（全部重采样到项目标准 44.1kHz 单声道 16-bit wav 再拼接）、单段超时、失败分类（可重试：429/5xx/OOM；不可重试：鉴权失败/文本超限）。

### 4.3 文本管道

```
原始稿件(txt/md)
  → 解析：剥离 YAML front-matter、代码块、HTML 注释
  → 清洗：去除 Markdown 语法（标题符、粗斜体、链接→纯文本、表格、引用符），
          保留段落与换行语义；[舞台提示] 类括注可选剔除
  → 分句：优先按段落切分；段内按 。！？；等终结标点聚簇，
          目标 40~80 字/段（上限可配，默认 80），句号密度优先、避免弱词结尾
  → 标签检查：情绪标签语法校验（仅 [word] 形式透传）
  → 段落队列：[{seq, text, status: pending}]
```

分句质量直接影响成品稳定性（自回归模型长句会漂移），此模块是自建框架中除 Provider 抽象外最重要的部分。

### 4.4 任务引擎与状态机

```
created → splitting → synthesizing → concatenating → done
                        │  段级状态：pending → running → ok / failed
                        │  failed → (人工/自动) retry → running
                        └→ 全部段 ok 或 仅剩不可重试失败 → partial_failed
```

- 调度：云端并发 = provider 允许值（≤2）；本地并发恒为 1（16GB 内存约束）
- 断点续跑：每段合成成功立即落盘 `data/tasks/{task_id}/seg_0001.wav` 并更新 SQLite 段状态；进程重启后任务从首个非 ok 段继续
- 拼接：按段序 + 句间停顿（默认 0.35s，段间 0.8s，可配）合成整稿；ffmpeg 缺失时降级 afconvert + 手写 WAV 拼接
- 产物命名：`{稿件名}_{音色名}_{引擎}_{时间戳}.wav`

### 4.5 音色管理

- **两类音色统一建模**：引擎预置音色（来自引擎音色库，如云端 `voices.list()`；只读）与用户克隆音色（上传创建；可删改），二者在 `list_voices()` 视角下统一，前端分组展示
- **试听缓存**：预置/克隆音色的试听各生成一次后落盘（`data/voices/previews/`），重复点击直接回放缓存——保护云端免费档 Fair Use 配额；删除音色时联动清缓存
- 一次上传、多引擎复用：上传的参考音频统一转为 24kHz 单声道 wav 归档；对云端引擎调 `voices.create()` 注册拿 `reference_id`；对本地引擎直接引用归档文件
- 逐字稿为一等公民：界面强校验「逐字稿必填」，并对音质影响做用户提示（逐字稿与录音的一致性 > 引擎选择的影响）
- 参考音频建议 10~30s、干净单人声；后端做时长/静音比预检并给出建议（过长自动截取）
- 一致性红线：仅克隆本人或已授权声线（界面提示 + 文档声明）

### 4.6 浏览器直接录音克隆（2026-11-14 新增）

音色库页在「上传克隆」之外提供「直接录音」入口，**纯前端实现、后端零改动**（复用既有
`POST /api/voices`），链路与设计决策：

```
getUserMedia(麦克风) → AudioWorklet 采集 PCM → 前端编码 16-bit 单声道 WAV
→ 试听复核/重录 → FormData 走既有上传 API → 归档 + 引擎注册
```

- **提示词 = 逐字稿**（本功能的核心价值）：界面给出默认提示词模板（约 200 字 / 30~40s 朗读，
  覆盖数字、英文术语与中英混读），可编辑；用户照稿朗读，提交时提示词即 `ref_text`——
  从机制上消灭「逐字稿与录音不一致」这个克隆质量头号坑
- **格式决策——前端编 WAV 而不用 MediaRecorder**：MediaRecorder 在 Chrome 输出 webm/opus、
  Safari 输出 mp4；本机无 ffmpeg（afconvert 不认 webm），跨浏览器直录会卡在转码。
  AudioWorklet 拿 PCM 后前端编 44 字节头标准 WAV（已用 Node+Python 交叉校验），与后端
  归档/拼接约定完全一致；旧浏览器回退 ScriptProcessorNode
- **回声防护**：采集节点经零增益 GainNode 接 destination（驱动音频图但不外放）
- **录音引导**：电平表（AnalyserNode 滚动波形）+ 计时器；时长约束提示（<10s 偏短 /
  >60s 偏长）；录完先试听，满意才提交，杜绝废录音上传
- **环境约束**：浏览器仅允许在 localhost / HTTPS 页面调用麦克风——当前部署恰为
  `http://127.0.0.1:8410`；若未来改为局域网 IP 访问需为 uvicorn 配 TLS
- 权限失败降级：NotAllowedError / NotFoundError 分别给出针对性提示文案

### 4.7 密钥与配置管理

- `data/config.json`（权限 0600，目录级 gitignore）：

```json
{
  "providers": {
    "fish_cloud": { "enabled": true, "api_key": "…", "model": "s2.1-pro-free" },
    "mlx_fish_s2pro": { "enabled": false, "model_dir": "" },
    "mlx_qwen_tts": { "enabled": false, "model": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit" }
  },
  "synthesis": { "chars_per_segment": 80, "pause_between_segments": 0.35, "sample_rate": 44100 }
}
```

- apiKey 仅在引擎设置页明文输入一次，回显掩码；日志与错误堆栈统一脱敏（正则滤 `Bearer xxx` / key 模式）
- 环境变量优先级高于配置文件（沿用 `FISH_API_KEY` 惯例，便于 CI/CLI 场景）

## 5. API 设计（REST）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/voices` | 音色列表（含各引擎注册状态；预置音色 60s TTL 缓存） |
| GET | `/api/voices?refresh=1` | **手动同步**：清空预置音色缓存强制回源（网页端新建音色即时可见） |
| POST | `/api/voices` | 创建克隆音色（multipart：audio + ref_text + name） |
| DELETE | `/api/voices/{id}` | 删除音色（联动引擎侧删除） |
| POST | `/api/voices/{id}/register/{provider}` | 补注册到某引擎 |
| POST | `/api/voices/{key}/preview` | 生成试听：固定示例句合成**一次**并写入缓存 |
| GET | `/api/voices/{key}/preview` | 播放试听缓存文件（支持 Range 拖动） |
| POST | `/api/upload/script` | 上传稿件（.txt/.md 白名单校验，返回清洗后文本预览与分段数） |
| POST | `/api/tasks` | 创建合成任务（text / script_id + voice_id + provider + params） |
| GET | `/api/tasks` / `/api/tasks/{id}` | 任务列表 / 详情（含段级进度） |
| POST | `/api/tasks/{id}/retry` | 断点续跑或重试失败段 |
| GET | `/api/tasks/{id}/segments/{seq}/audio` | 单段试听 |
| GET | `/api/tasks/{id}/audio?format=wav\|mp3\|m4a` | 整稿下载（默认 wav 兼容旧链接）；mp3 走 lameenc(128k CBR)、m4a 走系统 afconvert，按需转码并以母带 mtime 为指纹缓存（重合成自动失效） |
| GET | `/api/meta/exports` | 导出格式可用性（前端下载按钮置灰判断）；注意本端点与任务下载端点均只支持 GET，HEAD 返回 405——前端可用性探测必须走此 meta 端点而非对下载 URL 发 HEAD |
| GET | `/api/providers` | 引擎列表与配置（key 掩码） |
| PUT | `/api/providers/{id}` | 更新引擎配置（含 apiKey 写入） |
| POST | `/api/providers/{id}/test` | 连接/加载测试 |

约定：任务进度由前端 2s 轮询 `GET /api/tasks/{id}`；所有音频接口支持 `Range`（试听拖动）。

## 6. 数据模型（SQLite）

```sql
voices(   id, name, ref_audio_path, ref_text, created_at,
          cloud_ref_id NULL,                -- fish reference_id
          status )                          -- active / broken
providers(id, kind, enabled, config_json, last_health NULL, updated_at)
tasks(    id, title, voice_id, provider_id, params_json,
          status, total_segments, ok_segments, failed_segments,
          work_dir, output_path NULL, created_at, updated_at)
segments( task_id, seq, text, status, audio_path NULL,
          error NULL, attempts, duration_ms NULL,
          PRIMARY KEY(task_id, seq))
```

## 7. 目录结构规划

```
voice-script-studio/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口 + 静态托管前端
│   │   ├── api/                 # voices.py / tasks.py / providers.py / upload.py
│   │   ├── pipeline/
│   │   │   ├── text_clean.py    # md/txt 解析与清洗
│   │   │   ├── splitter.py      # 分句
│   │   │   ├── assembler.py     # 拼接/停顿/响度归一
│   │   │   └── runner.py        # 任务状态机、断点续跑、调度
│   │   ├── providers/
│   │   │   ├── base.py          # TTSProvider 抽象 + Caps/Health
│   │   │   ├── fish_cloud.py    # fish-audio-sdk 适配
│   │   │   ├── mlx_fish.py      # mlx-speech 子进程适配
│   │   │   ├── mlx_qwen.py      # mlx-audio 子进程适配
│   │   │   └── mock.py
│   │   ├── store/               # SQLite 访问 + config.json(0600) + 密钥脱敏
│   │   └── audio_utils.py       # ffmpeg/afconvert 探测与封装
│   ├── data/                    # 运行数据（gitignore：db、config.json、tasks/、voices/）
│   ├── requirements.txt
│   └── tests/                   # 分句/管道/断点续跑单测（mock provider 驱动）
├── frontend/
│   ├── index.html               # 单页（音色/工作台/设置/历史四视图）
│   └── static/                  # app.js / style.css（原生，无构建）
├── docs/
│   └── ARCHITECTURE.md          # 本文档
└── README.md
```

## 8. 关键流程

**A. 上传音频 → 克隆音色**
上传(wav/mp3/m4a) → 转码归档 24kHz mono wav → 时长/静音预检 → 用户补逐字稿 → 对已启用云端引擎调 `voices.create()` 注册 → 音色入库（含 engine→ref 映射）

**B. 合成任务（主流程）**
稿件输入/上传 → 文本管道（清洗→分句）→ 建 task + segments → 调度循环（取 pending 段，按引擎并发调用 `synthesize`，ok 即落盘改状态）→ 全部完成后 assembler 拼接 + 响度归一 → 产物落盘 → 前端整稿下载；任一段 failed → 任务标 partial_failed，可单段重试

**C. 引擎切换**
引擎设置页改配置（模型串/apiKey/启用）→ `test` 验证 → 新任务直接选新引擎；同一音色已多引擎注册，切换零成本；未注册引擎在发起任务时按需补注册

## 9. 非功能设计

- **安全**：apiKey 只存 `data/config.json`（0600）；掩码回显；日志脱敏；上传文件类型白名单 + 大小限制（音频 20MB、稿件 2MB）；路径校验防目录穿越
- **可靠性**：云端指数退避重试（429/5xx，最多 3 次）；本地 OOM → 杀子进程重启并单段重跑；进程崩溃 → 重启后断点续跑
- **性能预期**（参考值）：云端约 1~3s/段（80 字内）；本地 fish-s2-pro 约 3~8s/段（Air 会慢于 Max 实测值）；5000 字稿件（约 80 段）云端全流程约 5~10 分钟
- **许可与合规**：克隆仅限本人/已授权声线；Fish 免费档仅个人用途、无 SLA、请求可能用于改进模型（涉密稿建议走本地引擎）；本地 fish 权重为 Fish Audio Research License，商用需单独授权；`s2.1-pro-free` 免费窗口官方口径至 2026-11-30，架构上要求"到期切付费仅改模型串、切本地仅改 Provider 配置"

## 10. 里程碑

| 阶段 | 内容 | 验收标准 |
|------|------|----------|
| M1 | Provider 抽象 + fish_cloud + mock + 文本管道 + CLI 冒烟 | 用真实 apiKey 把一篇 500 字稿合成整稿 wav |
| M2 | FastAPI + WebUI 四页面（先做音色库 + 合成工作台） | 浏览器完成"上传音频→建音色→粘稿→合成→下载"全流程 |
| M3 | 本地引擎接入（mlx_fish 子进程 + 引擎设置页完整化） | 同一音色同一稿件云端/本地各出一版对比试听 |
| M4 | 断点续跑加固、历史页、响度归一、（可选）WebSocket 进度 | 杀进程重启后续跑成功；失败段单独重试成功 |

## 11. 风险清单

| 风险 | 影响 | 缓解 |
|------|------|------|
| `s2.1-pro-free` 免费窗口 11-30 到期 | 云端零成本失效 | 切付费仅改模型串（$15/百万字节，3000 字稿 ≈ $0.135）；或切本地 Provider |
| mlx-speech 项目年轻（单人维护） | 本地引擎可能停更 | Provider 抽象 + mlx-audio 备胎；两个引擎已可共存 |
| 16GB 内存紧张 | 本地引擎 + 后端 + 浏览器同跑可能换压 | 本地引擎子进程化、空闲即回收、并发恒 1、UI 提示关闭重负载 App |
| 免费档无 SLA/限流 | 批量合成中途 429 | 重试退避 + 断点续跑 + 任务级降速 |
| 逐字稿与录音不一致 | 克隆相似度差（比选引擎影响更大） | 界面强校验 + 引导性提示 + 参考音频预检 |
| 无 ffmpeg | 拼接/转码降级 | 安装引导 + afconvert 兜底路径 |

## 12. v1 实现偏差说明（2026-11-14 交付）

v1 已按上述设计实现并通过 18 项测试 + 真实服务冒烟。与文档的偏差逐条记录，便于评审对齐：

| # | 偏差 | 原因 |
|---|------|------|
| 1 | 云端接入**未用 fish-audio-sdk**，改为 requests 直调官方 REST（`/v1/tts`、`/model`） | 用户明确要求"全部代码自己写"；REST 端点为官方公开 API，依赖更少、行为透明 |
| 2 | §6 的 providers 数据表**合并进 config.json** | §4.7 本就承载 Provider 配置，双处存储易失同步；SQLite 只保留 voices/tasks/segments |
| 3 | 试听端点实现为 `POST /api/voices/preview?provider_id&voice_key` + `GET /api/voices/preview/{hash}.wav` | 预置音色的 voice_key 与引擎相关，必须同时携带 provider_id 才能合成 |
| 4 | 拼接层**纯标准库实现**（wave + 线性重采样），未引入 ffmpeg | 语音场景线性重采样听感可接受；ffmpeg 作为未来增强项 |
| 5 | 后端运行时为 Python 3.13（`python3 -m venv`），未用 uv 管理环境 | 3.13 满足全部依赖；后续接本地引擎（M3）时同样满足 mlx-speech 的 3.13+ 门槛 |
| 6 | 任务进度用 2s 轮询（§2.2 既定决策），WebSocket 未实现 | M4 项，当前轮询已满足体验 |
| 7 | 本地引擎（mlx_fish_s2pro / mlx_qwen_tts）在注册表预留位、未实装 | 第一版范围决策（用户确认）：本地 MLX 环境搭建与 4.5GB 权重下载单独进行 |
| 8 | fish_cloud 新增 `proxy` 配置字段（默认模板、存储白名单、设置页输入框） | 真实验证发现：沙箱/公司环境变量指向的代理不可用时云端全挂；显式配置 `http://127.0.0.1:7890` 后恢复。留空则回落环境变量，兼顾两种环境 |
| 9 | `_wav_info` 对流式 WAV 的 data 块 size 做实际剩余字节兜底 | Fish 云端返回的 WAV data size 为占位值（实测 0xFFFFFF00），标准解析会把 5.6s 段算成 13.5 小时；已补 3 项回归测试 |
| 10 | §4.1 音色库页新增「直接录音克隆」（设计文档 §4.6），纯前端实现 | 用户确认新增；AudioWorklet + 前端 WAV 编码，后端复用既有上传 API 零改动；提示词即逐字稿，机制上保证克隆音色的稿音一致 |
| 11 | 音色库页新增「🔄 同步音色库」按钮：`GET /api/voices?refresh=1` 清 TTL 缓存强制回源；预置音色 training 态标注并禁用试听；刷新失败保留旧列表 | 原被动 60s 缓存下，网页端新建音色需干等过期；用户确认方案 A。训练中音色直接合成会失败，顺手把 state 透传成友好标注 |
| 12 | 新增 `GET /api/voices/{id}/audio` 参考音频下载端点；fish_cloud 注册 403（免费 API 档音色创建配额为 0/0）转为中文引导；音色卡补齐「下载参考音频 / 复制逐字稿 / 补注册」按钮 | 真实验证发现 Fish 免费网页版含 3 个音色槽位、但 API 侧创建音色是独立计费（免费 0 个）——下载端点让归档录音可复用到网页端创建，创建后经 self=true 自动同步回本地预置音色 |
| 13 | 整稿下载支持三格式（`?format=wav\|mp3\|m4a`），前端改三按钮；新增 pipeline/encoder.py：mp3=lameenc 128k CBR（纯 wheel 依赖）、m4a=系统 afconvert、按需转码 + 母带 mtime 指纹缓存 | 用户确认方案；WAV 为唯一母带，压缩格式仅是下载视图——避免合成侧维护多份产物；重试段/续跑后母带变化时旧缓存自动失效 |

补充（2026-11-14 真实云端验证）：apiKey 已配置（掩码 ****DSbA）、出网代理 127.0.0.1:7890；
`POST /api/providers/fish_cloud/test` 真实鉴权通过；用平台公开音色「央视配音」完成全链路
真实合成（done 2/2 段，整稿 44.1kHz / 11.59s）。测试总数 21（18 单测 + 3 项 WAV 头回归）。

测试覆盖清单：Markdown 清洗（front-matter/代码块/链接/表格/情绪标签/舞台提示）、分句
（聚簇/段落标记/超长硬切/序号连续）、端到端（音色上传注册/白名单校验/稿件校验/
mock 全流程合成/失败重试/断点续跑 attempts 断言/引擎设置流转/试听缓存命中）。
