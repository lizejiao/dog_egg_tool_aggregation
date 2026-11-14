/**
 * Voice Script Studio —— 单页前端（原生 JS，无构建）
 *
 * 四视图：音色库 / 合成工作台 / 引擎设置 / 任务历史
 * 约定：所有数据交互走 /api/*，任务进度用 2s 轮询。
 */
"use strict";

/* ---------------- 通用工具 ---------------- */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

async function api(path, options = {}) {
  const resp = await fetch(path, options);
  let data = null;
  try { data = await resp.json(); } catch (_) { /* 非JSON响应（文件下载） */ }
  if (!resp.ok) {
    throw new Error((data && data.detail) || `HTTP ${resp.status}`);
  }
  return data;
}

function setMsg(sel, text, ok = true) {
  const el = $(sel);
  el.textContent = text || "";
  el.className = "msg " + (ok ? "ok" : "err");
}

function esc(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- 视图切换 ---------------- */

let activeTaskId = null;   // 工作台当前跟踪的任务
let pollTimer = null;      // 轮询定时器

$$(".tab").forEach((tab) => tab.addEventListener("click", () => {
  $$(".tab").forEach((t) => t.classList.remove("active"));
  tab.classList.add("active");
  const view = tab.dataset.view;
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  stopPolling();
  if (view === "voices") loadVoices();
  if (view === "workbench") { loadProviderOptions(); if (activeTaskId) { startPolling(activeTaskId); } }
  if (view === "settings") loadSettings();
  if (view === "history") loadHistory();
}));

/* ---------------- 音色库 ---------------- */

let presetCache = null; // {clones: [], presets: {pid: []}}

async function loadVoices(refresh = false) {
  try {
    presetCache = await api(refresh ? "/api/voices?refresh=1" : "/api/voices");
    renderVoices();
  } catch (e) {
    setMsg("#voice-msg", "加载失败：" + e.message, false);
  }
}

// 预置状态 → 界面标注；非 trained 的音色禁用试听（训练中的模型合成会失败）
function presetStateLabel(state) {
  if (state === "trained") return { label: "就绪", ready: true };
  if (state === "training" || state === "created")
    return { label: "⏳ 训练中（稍后点同步音色库再试）", ready: false };
  return { label: `状态：${state}`, ready: false };
}

// 手动刷新引擎音色库：清后端缓存强制回源；失败保留旧列表，只提示错误
let refreshingPresets = false;
async function refreshPresets() {
  if (refreshingPresets) return;           // 防连点
  const btn = $("#btn-refresh-presets");
  refreshingPresets = true;
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "同步中…";
  try {
    presetCache = await api("/api/voices?refresh=1");
    renderVoices();
    const n = Object.values(presetCache.presets || {})
      .filter(Array.isArray).reduce((s, a) => s + a.length, 0);
    setMsg("#voice-msg", `已同步引擎音色库（共 ${n} 个预置音色）`, true);
  } catch (e) {
    setMsg("#voice-msg", "同步失败（已保留当前列表）：" + e.message, false);
  } finally {
    refreshingPresets = false;
    btn.disabled = false;
    btn.textContent = old;
  }
}

function renderVoices() {
  // 我的克隆音色
  const cloneBox = $("#clone-list");
  if (!presetCache.clones.length) {
    cloneBox.innerHTML = `<p class="hint">暂无克隆音色，请从左侧上传创建。</p>`;
  } else {
    cloneBox.innerHTML = presetCache.clones.map((v) => {
      const regs = Object.entries(v.registrations || {})
        .map(([pid, r]) => `${pid}:${r.state}`).join("，");
      const unregHint = !regs
        ? `<br><b>未注册任何引擎</b>：可在下方下载参考音频、复制逐字稿，到 fish.audio 网页端
           「创建音色」上传（免费含 3 个槽位），创建后会自动同步到引擎预置音色`
        : "";
      return `<div class="voice-card" data-id="${v.id}">
        <div><div class="name">${esc(v.name)}</div>
          <div class="meta">逐字稿：${esc(v.ref_text.slice(0, 40))}…<br>注册状态：${esc(regs) || "未注册任何引擎"}${unregHint}</div>
        </div>
        <div class="ops">
          <button class="btn" onclick="previewVoice('clone:${v.id}')">试听</button>
          <button class="btn" onclick="downloadRefAudio('${v.id}')">下载参考音频</button>
          <button class="btn" onclick="copyRefText('${v.id}')">复制逐字稿</button>
          ${(v.registrations || {}).fish_cloud ? "" : `<button class="btn" onclick="registerVoice('${v.id}')">补注册到 Fish 云端</button>`}
          <button class="btn danger" onclick="deleteVoice('${v.id}','${esc(v.name)}')">删除</button>
        </div>
      </div>`;
    }).join("");
  }

  // 引擎预置音色
  const presetBox = $("#preset-list");
  const groups = [];
  for (const [pid, items] of Object.entries(presetCache.presets || {})) {
    if (Array.isArray(items) && items.length) {
      groups.push(`<p class="hint-inline">${esc(pid)} · ${items.length} 个音色</p>` +
        `<div class="voice-list">` + items.map((m) => {
          const st = presetStateLabel(m.state);
          return `
          <div class="voice-card">
            <div><div class="name">${esc(m.name)}</div>
                 <div class="meta">${esc(st.label)} · ${esc(m.voice_key)}</div></div>
            <div class="ops">${st.ready
              ? `<button class="btn" onclick="previewVoice('${esc(m.voice_key)}')">试听</button>`
              : `<button class="btn" disabled>试听</button>`}</div>
          </div>`;
        }).join("") + `</div>`);
    } else if (Array.isArray(items) && !items.length) {
      groups.push(`<p class="hint">引擎 ${esc(pid)}：无预置音色（克隆型引擎需上传参考音频）</p>`);
    } else if (items && items.error) {
      groups.push(`<p class="hint" style="color:#a32d2d">引擎 ${esc(pid)}：${esc(items.error)}</p>`);
    }
  }
  presetBox.innerHTML = groups.join("") || `<p class="hint">暂无启用的引擎。</p>`;
}

// 手动同步引擎音色库按钮
$("#btn-refresh-presets").addEventListener("click", () => refreshPresets());

$("#voice-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true; btn.textContent = "上传中…";
  try {
    const fd = new FormData(form);
    const result = await api("/api/voices", { method: "POST", body: fd });
    const errs = Object.entries(result.register_errors || {})
      .map(([pid, e]) => `${pid}: ${e}`).join("；");
    setMsg("#voice-msg", errs
      ? `音色已保存，但部分引擎注册失败——${errs}（稍后可在音色卡上补注册）`
      : "音色创建成功，已注册到可用引擎", !errs);
    form.reset();
    loadVoices();
  } catch (e) {
    setMsg("#voice-msg", e.message, false);
  } finally {
    btn.disabled = false; btn.textContent = "创建音色并注册引擎";
  }
});

async function deleteVoice(vid, name) {
  if (!confirm(`确定删除音色「${name}」？引擎侧模型将一并删除。`)) return;
  try {
    await api(`/api/voices/${vid}`, { method: "DELETE" });
    loadVoices();
  } catch (e) { alert("删除失败：" + e.message); }
}

// 下载归档的参考音频：用于免费 API 档无配额时，把同一段录音传到 fish.audio 网页端
function downloadRefAudio(vid) {
  window.open(`/api/voices/${vid}/audio`, "_blank");
}

// 复制逐字稿：与参考音频成对传到 fish.audio 网页端，保证稿音一致
async function copyRefText(vid) {
  const v = (presetCache.clones || []).find((c) => c.id === vid);
  if (!v) return;
  try {
    await navigator.clipboard.writeText(v.ref_text);
    alert("逐字稿已复制——粘贴到 fish.audio 网页端创建音色时的文本框即可");
  } catch (_) {
    // 剪贴板 API 不可用时降级为选中文本提示
    prompt("请手动复制逐字稿：", v.ref_text);
  }
}

// 补注册到 Fish 云端：失败原因（配额/网络）由后端以 400 返回并在此提示
async function registerVoice(vid) {
  try {
    await api(`/api/voices/${vid}/register/fish_cloud`, { method: "POST" });
    loadVoices();
  } catch (e) {
    alert("补注册失败：" + e.message);
  }
}

/* ---------------- 直接录音克隆 ----------------
 * 链路：getUserMedia → AudioWorklet 采集 PCM → 前端编码 16-bit 单声道 WAV
 *      → 试听复核 → 走既有 POST /api/voices（与文件上传同一后端入口）。
 * 设计要点：录音提示词 = 提交时的逐字稿，保证两者一字不差（克隆质量关键）。
 * 约束：浏览器仅允许在 localhost / HTTPS 页面调用麦克风（当前部署恰为 localhost）。
 */

// 默认提示词：约 200 字 ≈ 30~40 秒朗读，覆盖数字、英文术语与中英混读
const REC_PROMPT_TEMPLATE = "大家好，我是狗蛋。今天想花几分钟，和大家分享一段我最近一直在思考的内容。从 2026 年开始，人工智能，特别是大模型和 Agent 技术，正在两个维度上快速演进：一个是 GPU 算力的成本下降，另一个是 token 消耗的工程优化。举个具体的例子，当我把一篇 3000 字的演讲稿交给语音系统，它可以在 1 到 2 秒内完成分句、合成与拼接。这件事听起来简单，背后却是整条链路的重构。好的，谢谢大家，我们开始吧。";

// AudioWorklet 采集模块源码（Blob URL 动态加载，无需独立 JS 文件）
const REC_WORKLET_SRC = `
class RecProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) this.port.postMessage(input[0].slice(0));
    return true;
  }
}
registerProcessor('rec-processor', RecProcessor);
`;

const LEVEL_BARS = 24;      // 电平表滚动波形条数
const rec = {
  ctx: null, stream: null, node: null, analyser: null,
  chunks: [], sampleRate: 0, startTime: 0,
  raf: 0, timer: 0, running: false,
  history: new Array(LEVEL_BARS).fill(0),
  blob: null, blobUrl: null, durationS: 0,
};

// 初始化提示词与电平表 DOM
$("#rec-prompt").value = REC_PROMPT_TEMPLATE;
$("#rec-level").innerHTML = "<i></i>".repeat(LEVEL_BARS);

$("#rec-btn").addEventListener("click", () => (rec.running ? stopRecording() : startRecording()));
$("#rec-redo").addEventListener("click", resetReview);
$("#rec-submit").addEventListener("click", submitRecording);

async function startRecording() {
  setMsg("#rec-msg", "");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setMsg("#rec-msg", "当前浏览器不支持录音（需 localhost 或 HTTPS 页面，且浏览器较新）", false);
    return;
  }
  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    const reason = e.name === "NotAllowedError"
      ? "麦克风权限被拒绝——请点击地址栏麦克风图标允许后重试"
      : e.name === "NotFoundError" ? "未检测到麦克风设备" : e.message;
    setMsg("#rec-msg", "无法开始录音：" + reason, false);
    return;
  }
  rec.ctx = new (window.AudioContext || window.webkitAudioContext)();
  rec.sampleRate = rec.ctx.sampleRate;
  rec.chunks = [];
  const source = rec.ctx.createMediaStreamSource(rec.stream);

  // 电平表：AnalyserNode 只做可视化，不影响录音数据
  rec.analyser = rec.ctx.createAnalyser();
  rec.analyser.fftSize = 256;
  source.connect(rec.analyser);

  // 采集节点：优先 AudioWorklet，失败则回退 ScriptProcessor（旧浏览器兜底）
  let sink;
  try {
    const url = URL.createObjectURL(new Blob([REC_WORKLET_SRC], { type: "application/javascript" }));
    await rec.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    rec.node = new AudioWorkletNode(rec.ctx, "rec-processor");
    rec.node.port.onmessage = (ev) => rec.chunks.push(ev.data);
    sink = rec.node;
  } catch (_) {
    rec.node = rec.ctx.createScriptProcessor(4096, 1, 1);
    rec.node.onaudioprocess = (ev) => rec.chunks.push(ev.inputBuffer.getChannelData(0).slice(0));
    sink = rec.node;
  }
  source.connect(sink);
  // 采集节点须连到输出端才会被图驱动；经零增益节点避免扬声器回放啸叫
  const mute = rec.ctx.createGain();
  mute.gain.value = 0;
  sink.connect(mute);
  mute.connect(rec.ctx.destination);

  rec.running = true;
  rec.startTime = performance.now();
  $("#rec-btn").textContent = "停止录音";
  $("#rec-btn").classList.add("danger");
  $("#rec-level").classList.add("live");
  resetReview();
  rec.timer = setInterval(updateRecTimer, 100);
  levelLoop();
}

function updateRecTimer() {
  const s = (performance.now() - rec.startTime) / 1000;
  $("#rec-timer").textContent = fmtRecTime(s);
}

function fmtRecTime(s) {
  const m = Math.floor(s / 60), sec = s - m * 60;
  return String(m).padStart(2, "0") + ":" + (sec < 10 ? "0" : "") + sec.toFixed(1);
}

function levelLoop() {
  if (!rec.running) return;
  const data = new Uint8Array(rec.analyser.frequencyBinCount);
  rec.analyser.getByteFrequencyData(data);
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i];
  const v = Math.min(1, (sum / data.length / 255) * 2.2);  // 放大显示，便于观察嘴部距离
  rec.history.push(v);
  rec.history.shift();
  const bars = $("#rec-level").children;
  for (let i = 0; i < LEVEL_BARS; i++) {
    bars[i].style.height = Math.max(3, rec.history[i] * 32) + "px";
  }
  rec.raf = requestAnimationFrame(levelLoop);
}

function stopRecording() {
  rec.running = false;
  cancelAnimationFrame(rec.raf);
  clearInterval(rec.timer);
  try { rec.node.disconnect(); } catch (_) { /* 已断开 */ }
  rec.stream.getTracks().forEach((t) => t.stop());
  const sampleRate = rec.sampleRate;
  rec.ctx.close().catch(() => {});
  $("#rec-btn").textContent = "开始录音";
  $("#rec-btn").classList.remove("danger");
  $("#rec-level").classList.remove("live");
  rec.history.fill(0);
  const bars = $("#rec-level").children;
  for (let i = 0; i < LEVEL_BARS; i++) bars[i].style.height = "3px";

  // 合并 PCM 并编码 WAV
  const samples = mergeFloat32(rec.chunks);
  if (samples.length < sampleRate) {  // < 1 秒视为误触
    setMsg("#rec-msg", "录音太短（不足 1 秒），请重新录制", false);
    return;
  }
  rec.durationS = samples.length / sampleRate;
  rec.blob = new Blob([encodeWav(samples, sampleRate)], { type: "audio/wav" });
  if (rec.blobUrl) URL.revokeObjectURL(rec.blobUrl);
  rec.blobUrl = URL.createObjectURL(rec.blob);

  $("#rec-audio").src = rec.blobUrl;
  $("#rec-duration").textContent = rec.durationS.toFixed(1) + " 秒";
  if (rec.durationS < 10) {
    setMsg("#rec-warn", "　偏短：建议 15 秒以上，克隆相似度会更好", false);
  } else if (rec.durationS > 60) {
    setMsg("#rec-warn", "　偏长：建议控制在 40 秒以内", false);
  } else {
    setMsg("#rec-warn", "");
  }
  $("#rec-review").classList.remove("hidden");
}

function resetReview() {
  $("#rec-review").classList.add("hidden");
  setMsg("#rec-warn", "");
  if (rec.blobUrl) { URL.revokeObjectURL(rec.blobUrl); rec.blobUrl = null; }
  rec.blob = null;
  rec.durationS = 0;
}

async function submitRecording() {
  const name = $("#rec-name").value.trim();
  if (!name) { setMsg("#rec-msg", "请先填写音色名称", false); return; }
  if (!rec.blob) { setMsg("#rec-msg", "请先完成录音", false); return; }
  const refText = $("#rec-prompt").value.trim();
  if (!refText) { setMsg("#rec-msg", "录音提示词为空，请填写朗读内容", false); return; }

  const fd = new FormData();
  fd.append("name", name);
  fd.append("ref_text", refText);   // 提示词即逐字稿
  fd.append("audio", new File([rec.blob], "recording.wav", { type: "audio/wav" }));

  const btn = $("#rec-submit");
  btn.disabled = true; btn.textContent = "提交中…";
  try {
    const result = await api("/api/voices", { method: "POST", body: fd });
    const errs = Object.entries(result.register_errors || {})
      .map(([pid, e]) => `${pid}: ${e}`).join("；");
    setMsg("#rec-msg", errs
      ? `音色已保存，但部分引擎注册失败——${errs}（稍后可在音色卡上补注册）`
      : "音色创建成功，已注册到可用引擎", !errs);
    $("#rec-name").value = "";
    resetReview();
    loadVoices();
  } catch (e) {
    setMsg("#rec-msg", e.message, false);
  } finally {
    btn.disabled = false; btn.textContent = "创建音色并注册引擎";
  }
}

function mergeFloat32(chunks) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Float32Array(total);
  let offset = 0;
  for (const c of chunks) { out.set(c, offset); offset += c.length; }
  return out;
}

/** 标准 44 字节头的 16-bit 单声道 PCM WAV 编码（与后端拼接层约定一致） */
function encodeWav(samples, sampleRate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buf);
  const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);          // fmt 块大小
  view.setUint16(20, 1, true);           // PCM
  view.setUint16(22, 1, true);           // 单声道
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);  // 字节率 = 采样率 × 声道 × 2
  view.setUint16(32, 2, true);           // 块对齐
  view.setUint16(34, 16, true);          // 位深
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}


/* --- 试听：POST 生成（同音色只真实合成一次）→ <audio> 播放缓存 --- */
let audioEl = new Audio();

async function previewVoice(voiceKey) {
  // 预置音色带 provider 前缀信息（voice_key 为 preset:<model_id>，按引擎分组存放）
  // 克隆音色默认用其已注册的第一个引擎；此处从缓存的注册表推导
  let providerId = null;
  if (voiceKey.startsWith("clone:")) {
    const v = (presetCache.clones || []).find((c) => "clone:" + c.id === voiceKey);
    providerId = v && Object.keys(v.registrations || {})[0];
  } else {
    for (const [pid, items] of Object.entries(presetCache.presets || {})) {
      if (Array.isArray(items) && items.some((m) => m.voice_key === voiceKey)) { providerId = pid; break; }
    }
  }
  if (!providerId) { alert("无法确定该音色所属引擎"); return; }
  try {
    const r = await api(`/api/voices/preview?provider_id=${encodeURIComponent(providerId)}&voice_key=${encodeURIComponent(voiceKey)}`, { method: "POST" });
    audioEl.src = r.preview_url;
    audioEl.play();
  } catch (e) { alert("试听失败：" + e.message); }
}

/* ---------------- 工作台 ---------------- */

async function loadProviderOptions() {
  // 引擎下拉：仅显示已启用引擎
  try {
    const { providers } = await api("/api/providers");
    const usable = providers.filter((p) => p.enabled && p.implemented !== false);
    $("#provider-select").innerHTML = usable.length
      ? usable.map((p) => `<option value="${p.id}">${esc(p.display_name)}</option>`).join("")
      : `<option value="">（无已启用引擎，请到引擎设置开启）</option>`;
    renderVoiceSelect(providers);
    return providers;
  } catch (e) {
    $("#provider-select").innerHTML = `<option value="">加载失败</option>`;
    return [];
  }
}

function renderVoiceSelect(providers) {
  // 音色下拉：克隆音色（可用其引擎）+ 各引擎预置音色
  const groups = [];
  const clones = (presetCache ? presetCache.clones : null) || [];
  if (clones.length) {
    groups.push(`<optgroup label="我的克隆音色">` + clones.map((v) => {
      const pid = Object.keys(v.registrations || {})[0] || "";
      return `<option value="clone:${v.id}" data-provider="${pid}">${esc(v.name)}</option>`;
    }).join("") + `</optgroup>`);
  }
  for (const p of providers) {
    const items = presetCache && presetCache.presets && presetCache.presets[p.id];
    if (Array.isArray(items) && items.length) {
      groups.push(`<optgroup label="${esc(p.display_name)} 预置">` + items.map((m) =>
        `<option value="${esc(m.voice_key)}" data-provider="${p.id}">${esc(m.name)}</option>`
      ).join("") + `</optgroup>`);
    }
  }
  $("#voice-select").innerHTML = groups.join("") || `<option value="">（暂无可用音色）</option>`;
  // 切音色时联动引擎选择
  $("#voice-select").onchange = (ev) => {
    const opt = ev.target.selectedOptions[0];
    const pid = opt && opt.dataset.provider;
    if (pid) $("#provider-select").value = pid;
  };
}

$("#speed").addEventListener("input", (ev) => { $("#speed-val").textContent = ev.target.value; });

/* 稿件上传：仅 txt/md，上传后回填清洗文本与段数预估 */
$("#script-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  setMsg("#upload-msg", "解析中…");
  try {
    const fd = new FormData();
    fd.append("file", file);
    const r = await api("/api/upload/script", { method: "POST", body: fd });
    $("#task-text").value = r.cleaned_text;
    if (!$("#task-title").value) $("#task-title").value = r.title;
    setMsg("#upload-msg", `已解析：约 ${r.segment_count} 段`);
  } catch (e) {
    setMsg("#upload-msg", e.message, false);
  }
  ev.target.value = ""; // 允许重复上传同名文件
});

$("#start-btn").addEventListener("click", async () => {
  const text = $("#task-text").value.trim();
  const voiceKey = $("#voice-select").value;
  const providerId = $("#provider-select").value;
  if (!text) return setMsg("#start-msg", "请输入或上传演讲稿", false);
  if (!voiceKey) return setMsg("#start-msg", "请选择音色（先到音色库创建）", false);
  if (!providerId) return setMsg("#start-msg", "请选择引擎（先到引擎设置启用）", false);

  const body = {
    title: $("#task-title").value.trim() || "未命名稿件",
    text, voice_key: voiceKey, provider_id: providerId,
    params: {
      speed: parseFloat($("#speed").value),
      pause_between: parseFloat($("#pause").value) || 0.35,
    },
  };
  try {
    const r = await api("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setMsg("#start-msg", "任务已启动");
    activeTaskId = r.task_id;
    $("#progress-panel").classList.remove("hidden");
    startPolling(activeTaskId);
  } catch (e) {
    setMsg("#start-msg", e.message, false);
  }
});

/* ---------------- 任务进度轮询 ---------------- */

function startPolling(taskId) {
  stopPolling();
  pollTimer = setInterval(() => refreshProgress(taskId).catch(() => {}), 2000);
  refreshProgress(taskId).catch(() => {});
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function refreshProgress(taskId) {
  if (!taskId) return;   // 无活动任务时跳过（如仅刷新导出可用性）
  const { task, segments, running } = await api(`/api/tasks/${taskId}`);
  const done = ["done", "partial_failed", "failed"].includes(task.status);
  if (done) stopPolling();

  // 进度条与文本
  const total = segments.length || 1;
  const okCount = segments.filter((s) => s.status === "ok").length;
  $("#progress-fill").style.width = `${(okCount / total) * 100}%`;
  $("#progress-text").textContent =
    `状态：${task.status}　完成 ${okCount}/${segments.length} 段` +
    (task.output_path ? "　整稿已生成" : "");
  $("#progress-title").textContent = `· ${task.title}`;

  // 段列表
  const statusLabel = { ok: "✓ 成功", failed: "✗ 失败", running: "… 合成中", pending: "· 等待" };
  $("#segment-list").innerHTML = segments.map((s) => `
    <div class="seg-row">
      <span class="seg-status ${s.status}">${statusLabel[s.status] || s.status}</span>
      <span class="seg-text" title="${esc(s.text)}">${esc(s.text)}</span>
      ${s.status === "ok" ? `<button class="btn" style="padding:2px 10px;font-size:12px"
          onclick="playSegment('${taskId}', ${s.seq})">播放</button>` : ""}
      ${s.status === "failed" ? `<button class="btn" style="padding:2px 10px;font-size:12px"
          onclick="retrySegment('${taskId}', ${s.seq})">重试</button>` : ""}
    </div>`).join("");

  // 下载 / 续跑按钮（三种格式共享显隐，href 各自指向对应 format）
  const hasOutput = !!task.output_path;
  for (const fmt of ["wav", "m4a", "mp3"]) {
    const dl = $(`#download-${fmt}`);
    dl.href = `/api/tasks/${taskId}/audio?format=${fmt}`;
    dl.classList.toggle("hidden", !hasOutput);
    // MP3 依赖 lameenc：能力由 loadExportAvailability() 页面加载时前置探测，
    // 不可用直接置灰（不在此处做点击探测——HEAD 对该端点是 405，且 async
    // onclick 里 preventDefault 已错过浏览器默认下载时机，会误报）
    if (fmt === "mp3" && exportsAvailability && !exportsAvailability.mp3) {
      dl.classList.add("disabled");
      dl.title = "MP3 导出不可用：缺少 lameenc 依赖（backend/.venv/bin/pip install lameenc）";
      dl.onclick = (ev) => { ev.preventDefault(); alert(dl.title); };
    }
  }
  $("#retry-all-btn").classList.toggle("hidden", task.status === "done");
}

function playSegment(taskId, seq) {
  audioEl.src = `/api/tasks/${taskId}/segments/${seq}/audio`;
  audioEl.play();
}

async function retrySegment(taskId, seq) {
  try { await api(`/api/tasks/${taskId}/segments/${seq}/retry`, { method: "POST" }); startPolling(taskId); }
  catch (e) { alert("重试失败：" + e.message); }
}

$("#retry-all-btn").addEventListener("click", async () => {
  if (!activeTaskId) return;
  try { await api(`/api/tasks/${activeTaskId}/retry`, { method: "POST" }); startPolling(activeTaskId); }
  catch (e) { alert("续跑失败：" + e.message); }
});

/* ---------------- 引擎设置 ---------------- */

async function loadSettings() {
  try {
    const { providers } = await api("/api/providers");
    $("#settings-list").innerHTML = providers.map((p) => `
      <div class="panel provider-card" data-pid="${p.id}">
        <div class="head">
          <div><b>${esc(p.display_name)}</b> <span class="badge ${p.enabled ? "on" : ""}">${p.enabled ? "已启用" : "未启用"}</span>
          ${p.api_key_configured ? `<span class="badge">key ${esc(p.api_key_masked || "")}</span>` : ""}
          ${p.error ? `<span class="badge err">${esc(p.error)}</span>` : ""}</div>
          <label class="switch"><input type="checkbox" class="prov-enabled" ${p.enabled ? "checked" : ""}> 启用</label>
        </div>
        <p class="hint">${esc(p.notes || "")}</p>
        ${p.id === "fish_cloud" ? `
          <div class="settings-grid">
            <label>apiKey <input type="password" class="prov-key" placeholder="${p.api_key_configured ? "已配置（留空保持不变）" : "粘贴 fish.audio 的 API Key"}"></label>
            <label>模型串
              <select class="prov-model">
                <option value="s2.1-pro-free" ${p.model === "s2.1-pro-free" ? "selected" : ""}>s2.1-pro-free（免费档，至 2026-11-30）</option>
                <option value="s2.1-pro" ${p.model === "s2.1-pro" ? "selected" : ""}>s2.1-pro（付费 $15/百万字节）</option>
              </select>
            </label>
            <label>出网代理 <input type="text" class="prov-proxy" value="${esc(p.proxy || "")}" placeholder="如 http://127.0.0.1:7890，留空走系统环境变量"></label>
          </div>` : ""}
        <div class="row">
          <button class="btn primary" onclick="saveProvider('${p.id}')">保存</button>
          <button class="btn" onclick="testProvider('${p.id}')">测试连接</button>
          <span class="msg" id="settings-msg-${esc(p.id)}"></span>
        </div>
      </div>`).join("");
  } catch (e) {
    $("#settings-list").innerHTML = `<div class="panel">加载失败：${esc(e.message)}</div>`;
  }
}

async function saveProvider(pid) {
  const card = document.querySelector(`.provider-card[data-pid="${pid}"]`);
  const body = { enabled: card.querySelector(".prov-enabled").checked };
  const keyInput = card.querySelector(".prov-key");
  if (keyInput && keyInput.value.trim()) body.api_key = keyInput.value.trim();
  const modelSel = card.querySelector(".prov-model");
  if (modelSel) body.model = modelSel.value;
  const proxyInput = card.querySelector(".prov-proxy");
  if (proxyInput) body.proxy = proxyInput.value.trim();
  try {
    await api(`/api/providers/${pid}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setMsg(`#settings-msg-${pid}`, "已保存");
    loadSettings();
  } catch (e) {
    setMsg(`#settings-msg-${pid}`, e.message, false);
  }
}

async function testProvider(pid) {
  setMsg(`#settings-msg-${pid}`, "测试中…");
  try {
    const r = await api(`/api/providers/${pid}/test`, { method: "POST" });
    setMsg(`#settings-msg-${pid}`, (r.ok ? "✓ " : "✗ ") + r.message, r.ok);
  } catch (e) {
    setMsg(`#settings-msg-${pid}`, e.message, false);
  }
}

/* ---------------- 任务历史 ---------------- */

const STATUS_TEXT = {
  created: "已创建", splitting: "分句中", synthesizing: "合成中",
  concatenating: "拼接中", done: "完成", partial_failed: "部分失败", failed: "失败",
};

async function loadHistory() {
  try {
    const { tasks } = await api("/api/tasks");
    $("#task-table tbody").innerHTML = tasks.map((t) => {
      const time = new Date(t.created_at * 1000).toLocaleString("zh-CN", { hour12: false });
      const prog = `${t.ok_segments}/${t.total_segments}`;
      return `<tr>
        <td>${time}</td>
        <td>${esc(t.title)}</td>
        <td>${esc(t.provider_id)}</td>
        <td>${STATUS_TEXT[t.status] || t.status}</td>
        <td>${prog}</td>
        <td>
          <button class="btn" style="padding:2px 10px;font-size:12px"
            onclick="openTask('${t.id}')">查看</button>
        </td>
      </tr>`;
    }).join("");
  } catch (e) {
    $("#task-table tbody").innerHTML = `<tr><td colspan="6">加载失败：${esc(e.message)}</td></tr>`;
  }
}

function openTask(taskId) {
  // 从历史进入工作台的进度视图
  $$(".tab").forEach((t) => t.classList.remove("active"));
  document.querySelector('[data-view="workbench"]').classList.add("active");
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $("#view-workbench").classList.remove("hidden");
  activeTaskId = taskId;
  $("#progress-panel").classList.remove("hidden");
  startPolling(taskId);
}

/* ---------------- 启动 ---------------- */

// 导出格式可用性（{wav: true, mp3: bool, m4a: bool}；探测失败不阻塞下载）
let exportsAvailability = null;
async function loadExportAvailability() {
  try { exportsAvailability = await api("/api/meta/exports"); }
  catch (_) { exportsAvailability = null; }
  refreshProgress(activeTaskId).catch(() => {});
}

loadProviderOptions().then(() => {
  // 工作台首屏：加载音色下拉（presetCache 可能尚未就绪，延迟再渲染一次）
  loadVoicesQuietly();
  loadExportAvailability();
});

async function loadVoicesQuietly() {
  try { presetCache = await api("/api/voices"); renderVoiceSelect(await currentProviders()); }
  catch (_) { /* 音色未就绪不阻塞首屏 */ }
}

let _cachedProviders = null;
async function currentProviders() {
  if (!_cachedProviders) {
    try { _cachedProviders = (await api("/api/providers")).providers; }
    catch (_) { _cachedProviders = []; }
  }
  return _cachedProviders;
}
