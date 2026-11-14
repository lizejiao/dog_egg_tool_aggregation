# listing-guard —— 房源发房上架自动审核 Agent

基于 **LangGraph** 的多模态验真驱动自动审核 demo（对应简历项目：房源发房上架自动审核 Agent）。
目标一句话：**高质量房源分钟级自动上架，可疑房源精准转人工（AI 给出评判意见）**。

```
START → 材料接入 → 注入防护 →(并行扇出，同一超步)→ ┬ 子图1 表单规则核验 form_audit
                                                    ├ 子图2 图片 VLM 核验 image_vlm（Send 按图扇出）
                                                    ├ 子图3 视频 ASR+帧识别 video（Send 按视频扇出）
                                                    ├ 子图4 文本内容风控 text_risk（规则+语义双路）
                                                    ├ 子图5 房源基本面 base_info（楼盘库/历史房源库/信用库，
                                                    │        子图内四路二级并行）
                                                    └ 政府核验（异步回调 interrupt 挂起）
                                        → 置信度汇聚(加权+降级重归一+否决隔离) → 决策路由(非对称阈值)
                                          → 自动上架 / 人工复审(interrupt) / 自动驳回
```

## 5 分钟跑通

```bash
cd agents/listing-guard
uv sync --group dev          # 零外部依赖：不需要 MySQL/Redis/任何服务器

# ① 单条全流程（含 挂起→回调→resume 的完整叙事）
uv run python scripts/run_case.py --case normal_01

# ② 断点续跑：两个进程验证"进度在 SQLite 检查点，与进程无关"
uv run python scripts/resume_case.py --case normal_01          # 进程 1：提交后挂起退出
uv run python scripts/resume_case.py --case normal_01 --resume # 进程 2：从检查点恢复

# ③ 批量评测：混淆矩阵 + 自动覆盖率/转人工率/误杀数 + P95
uv run python scripts/batch_eval.py

# ④ 两个终端模拟真实异步回调
#    终端 1: uv run python scripts/run_case.py --case normal_01 --no-auto
#    终端 2: uv run python scripts/callback_sim.py --audit-id AUD-NORMAL_01

# ⑤ Web 审核控制台（P4：FastAPI + 单页控制台）
./scripts/serve.sh          # 默认 http://127.0.0.1:8300，浏览器打开即是
./scripts/serve.sh 8400     # 自定义端口

uv run pytest                 # 单测：路由/汇聚/注入/降级/断点续跑/API 集成
```

`data/checkpoints.sqlite` 就是断点续跑的全部进度（LangGraph SqliteSaver，
每超步一个快照，`thread_id` = 审核单号）。可以现场 `sqlite3` 打开看
`checkpoints` / `writes` 两张表。

## 简历技术点 → 代码位置

| 简历里的点 | 实现 |
|---|---|
| LangGraph 状态机 + 条件边 | `graph/builder.py` + `graph/routes.py`（路由纯函数，可单测） |
| 5 条并行核验链路均为独立子图 | `graph/subgraphs/`：form_audit / image_vlm / video / text_risk / base_info |
| 视频取关键帧接入 + 语音转写封装子图 | `graph/subgraphs/video_audit.py`（Send 按视频扇出，ASR 转写子图内风控） |
| 对接楼盘库/历史房源库核验（重复房源/价格偏离） | `graph/subgraphs/base_info.py` + `tools/repository.py`（三库种子） |
| 并行扇出 + 汇聚、单算子超时降级 | 主图 6 路并行边（5 子图+政府核验）+ `tools/registry.py`（降级因子从分母剔除重归一化） |
| 检查点持久化与断点复跑 | `SqliteSaver` + `nodes/gov_verify.py`（interrupt 挂起，回调 resume 不重跑已完成算子） |
| 子图上下文隔离 | 各子图只回传结构化结论；图片 base64/OCR 全文/ASR 转写全文都不进主图 state |
| 人工审批节点 + 裁决回流 | `nodes/human_review.py`（interrupt 带 AI 评判意见，结果落 `data/feedback/`） |
| 提示词注入防范 | `nodes/injection_guard.py` + `config/compliance.yaml`（命中即强制转人工，自由文本不影响路由） |
| 多因子可解释合成 | `nodes/aggregate.py`（确定性因子 + 概率性因子加权，输出命中清单与权重） |
| 非对称阈值（误杀成本高于漏放） | `config/thresholds.yaml`（放行 0.85 / 拦截 0.35，中间地带转人工）+ 价格极端偏离强因子转人工 |
| 策略配置化不发版 | 因子/权重/阈值全部在 `config/*.yaml`，策略版本哈希随检查点落盘可回溯 |
| mock 双模式 | `.env` 里 `AUDIT_MODE=mock|live`；live 走多模态模型 + 结构化输出 |
| LangSmith 可观测 | `builder._setup_langsmith()`：配 `LANGSMITH_API_KEY` 自动开 trace，没配零开销 |
| 指标沉淀与 P95 | `observability/metrics.py`：JSONL 逐条记录，`/stats` 与 batch_eval 消费同一份 |
| Web 审核控制台 | `api/main.py` + `web/index.html`：提交→回调→裁决全链路可视化 |

## Web 控制台（P4）

`./scripts/serve.sh` 后浏览器打开 `http://127.0.0.1:8300`，三个 tab：

**审核工作台** —— 提交审核全流程：
- 顶部指标卡：累计审核 / 自动覆盖率 / 转人工率 / P95 耗时 / 降级因子数
- 选 fixture 一键提交 → 因子表（权重、得分条、命中/否决/降级标签）实时渲染
- 挂起态按钮化操作：**模拟政府回调** / **人工放行·驳回** / **模拟房东补材料**
- AI 评判意见面板 + 执行轨迹（与 SQLite 检查点 history 对应）

**审核队列** —— 所有未上架房源及当前审核状态：
- 状态分档：等待政府核验回调（蓝）/ 等待房东补材料（紫）/ 等待人工复审（黄，带 AI 意见标记）/ 自动·人工驳回（红）
- 挂起中的单子**可直接在队列里继续流转**（对应按钮 resume），驳回的显示终态与否决原因
- 数据源即检查点库（每单一个 thread）——「进度在检查点里」的可视化证据；已上架的自动隐藏

**上架房源** —— 已上架房源列表（data/published.jsonl，对应生产「上架房源表」）：
- 自动核验上架（绿色标签，decide 节点落库）与人工放行上架（蓝色标签，裁决节点落库）
- 来源筛选、置信度、审核单号、上架时间；驳回房源不入表
- 首次查询自动从检查点库回填历史已上架审核单（幂等）

HTTP 接口（`tests/test_api.py` 全覆盖）：

```
POST /audit                        提交（case 或 listing）
GET  /audit/{id}                   状态快照（因子/评分/挂起点/AI 意见）
POST /audit/{id}/gov-callback      政府核验回调 → resume（可覆盖 status）
POST /audit/{id}/decision          人工裁决 → resume（回流 data/feedback/）
POST /audit/{id}/supplement        房东补材料 → resume（断点续跑）
GET  /pending?status=pending|rejected|all  审核队列（未上架单 + 当前状态，检查点库扫描）
GET  /listings?source=auto|human   上架房源列表（含检查点回填）
GET  /stats | /fixtures            指标卡 | 样本列表
```

## 目录结构

```
config/        策略配置（因子注册表/阈值/合规词库/灰度/prompts）
src/listing_guard/
  state.py     图状态（并行写入走 reducer；只存结构化结论）
  graph/       主图装配、路由纯函数、五个核验子图（subgraphs/）
  nodes/       材料接入/注入防护/五子图入口/政府核验/汇聚/决策/人工复审/补材料
  tools/       算子适配层（vision/video/ocr mock 与 live、政府核验异步、三库仓储）
  llm/         评判意见生成（mock 模板 / live 结构化输出）
  observability/  LangSmith 门控 + 指标沉淀（metrics.jsonl / P95 / 指标卡）
  api/         FastAPI（提交/状态/回调/裁决/补材料/指标）
  runner.py    执行器（提交 → 自动恢复挂起点）
web/           审核控制台单页（指标卡 + 因子表 + 挂起态按钮操作）
scripts/       run_case / resume_case / callback_sim / batch_eval / serve.sh
data/          fixtures（11 条样本含期望结论）/ seed 三库 / 检查点库 / metrics.jsonl
tests/         路由、汇聚、注入、降级、断点续跑、全量 fixture、API 集成
docs/          architecture.md
```

## 生产映射（demo 零依赖 ≠ 生产零能力）

| demo 载体 | 生产替换 | 改动面 |
|---|---|---|
| `SqliteSaver`（单文件） | `PostgresSaver`/`RedisSaver` | 换连接串，图代码零改动 |
| `data/seed/*.yaml` | MySQL 价格带 + Redis 黑名单缓存 | 只换 `tools/repository.py` |
| `gov_pending.json` 队列 | MQ + 真实住建/租赁监管平台 | 只换 `tools/gov_api.py` |
| mock 视觉算子 | 多模态大模型（`AUDIT_MODE=live`） | 只换 `tools/vision.py` |

## 测试样本（data/fixtures，11 条）

normal_01（优质含实拍视频自动上架）｜price_anomaly_02（价格极端偏离强因子转人工）｜stolen_image_03（盗图否决驳回）｜
text_image_mismatch_04（图文不符转人工）｜prompt_injection_05（注入强制转人工）｜banned_contact_06（飞单否决驳回）｜
missing_material_07（缺材料挂起→补拍续跑）｜low_confidence_08（低置信转人工+AI 意见）｜gov_timeout_09（算子超时降级不误杀）｜
video_fraud_10（视频造假+ASR 风控否决驳回）｜duplicate_listing_11（历史库查重强因子转人工）
