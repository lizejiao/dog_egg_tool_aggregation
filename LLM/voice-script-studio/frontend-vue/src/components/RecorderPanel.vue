<template>
  <div class="panel">
    <h2>直接录音克隆 <span class="sub">照提示词朗读，逐字稿一字不差</span></h2>

    <label class="ep-label">录音提示词（可编辑；提交时即逐字稿）</label>
    <textarea class="ep-textarea" rows="3" v-model="recPrompt" style="resize:vertical"></textarea>

    <div class="rec-bar">
      <button class="ep-btn" :class="{primary: !running, danger: running}" @click="running ? stopRecording() : startRecording()">
        {{ running ? "■ 停止录音" : "● 开始录音" }}
      </button>
      <span class="rec-time">{{ recTimeText }}</span>
      <div class="rec-level">
        <i v-for="(h, i) in levels" :key="i" :style="{height: h + 'px'}"></i>
      </div>
      <span v-if="durationS > 0" class="badge" :class="over ? 'miss' : 'ok'">
        {{ durationS.toFixed(1) }}s {{ over ? "超限" : "合规" }}
      </span>
    </div>

    <div v-if="over" class="tip warn">
      ⚠️ 超过 30 秒上限（CosyVoice3 要求 ≤30s），保存将被拦截——请重新录音
    </div>
    <div v-else-if="durationS > 0 && durationS < 10" class="tip warn">
      ⚠️ 偏短：建议 15 秒以上，克隆相似度会更好
    </div>

    <!-- 复核区：录完试听 + 命名 + 保存 -->
    <div v-if="!running && durationS > 0" class="rec-review">
      <audio :src="blobUrl" controls style="width:100%; margin:8px 0"></audio>

      <label class="ep-label">声音素材名称 <span class="req">*</span>
        <span class="field-when">（保存后进入素材库；在「音色管理」注册为可用音色）</span></label>
      <input class="ep-input" v-model="matName" placeholder="例如：狗蛋录音-2026.11" />

      <div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap">
        <button class="ep-btn primary" :disabled="saving || over || !matName.trim()" @click="save">
          {{ saving ? "保存中…" : "保存素材" }}
        </button>
        <button class="ep-btn" @click="resetReview">重新录音</button>
      </div>
    </div>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'warn'" style="margin-top:8px">{{ msg }}</div>

    <div class="tip info" style="margin-top:10px">
      保存的素材在下方素材库；要用于合成，请到「音色管理」→ 注册新音色（选素材 + 选引擎 + 命名）。
      录音需浏览器麦克风权限（仅 localhost / HTTPS 可用）。
    </div>
  </div>
</template>

<script setup>
/**
 * 录音面板（自旧版 frontend/static/app.js 的录音模块整体平移，不重写）。
 * 链路：getUserMedia → AudioWorklet 采集 PCM（失败回退 ScriptProcessor）
 *      → 前端编码 16-bit 单声道 WAV → 试听复核 → POST /api/voices（register_provider=none，纯素材采集）。
 * 设计要点：录音提示词 = 提交时的逐字稿，保证两者一字不差（克隆质量关键）。
 * 30 秒上限为前置拦截（P0 定稿交互）。
 */
import { computed, onBeforeUnmount, ref } from "vue";
import { api } from "../api/client.js";
import { useMaterialStore } from "../stores/material.js";

const emit = defineEmits(["saved"]);
const materialStore = useMaterialStore();

const REC_PROMPT_TEMPLATE = "大家好，我是狗蛋。今天想花几分钟，和大家分享一段我最近一直在思考的内容。从 2026 年开始，人工智能，特别是大模型和 Agent 技术，正在两个维度上快速演进：一个是 GPU 算力的成本下降，另一个是 token 消耗的工程优化。举个具体的例子，当我把一篇 3000 字的演讲稿交给语音系统，它可以在 1 到 2 秒内完成分句、合成与拼接。这件事听起来简单，背后却是整条链路的重构。好的，谢谢大家，我们开始吧。";

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

const LEVEL_BARS = 24;

const recPrompt = ref(REC_PROMPT_TEMPLATE);
const matName = ref("");
const msg = ref("");
const msgOk = ref(true);
const saving = ref(false);

const running = ref(false);
const recTimeText = ref("00:00.0");
const levels = ref(new Array(LEVEL_BARS).fill(3));
const durationS = ref(0);
const blobUrl = ref("");
const over = computed(() => durationS.value > 30);

// 录音态（非响应式：音频节点不需要触发渲染）
let rec = {
  ctx: null, stream: null, node: null, analyser: null,
  chunks: [], sampleRate: 0, startTime: 0,
  raf: 0, timer: 0, history: new Array(LEVEL_BARS).fill(0),
  blob: null,
};

function setMsg(text, ok = true) {
  msg.value = text;
  msgOk.value = ok;
}

async function startRecording() {
  setMsg("");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setMsg("当前浏览器不支持录音（需 localhost 或 HTTPS 页面，且浏览器较新）", false);
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
    setMsg("无法开始录音：" + reason, false);
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

  running.value = true;
  rec.startTime = performance.now();
  rec.history.fill(0);
  rec.timer = setInterval(() => {
    const s = (performance.now() - rec.startTime) / 1000;
    const m = Math.floor(s / 60), sec = s - m * 60;
    recTimeText.value = String(m).padStart(2, "0") + ":" + (sec < 10 ? "0" : "") + sec.toFixed(1);
  }, 100);
  levelLoop();
}

function levelLoop() {
  if (!running.value) return;
  const data = new Uint8Array(rec.analyser.frequencyBinCount);
  rec.analyser.getByteFrequencyData(data);
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i];
  const v = Math.min(1, (sum / data.length / 255) * 2.2);  // 放大显示，便于观察嘴部距离
  rec.history.push(v);
  rec.history.shift();
  levels.value = rec.history.map((h) => Math.max(3, h * 32));
  rec.raf = requestAnimationFrame(levelLoop);
}

function stopRecording() {
  running.value = false;
  cancelAnimationFrame(rec.raf);
  clearInterval(rec.timer);
  try { rec.node.disconnect(); } catch (_) { /* 已断开 */ }
  rec.stream.getTracks().forEach((t) => t.stop());
  const sampleRate = rec.sampleRate;
  rec.ctx.close().catch(() => {});
  rec.history.fill(0);
  levels.value = rec.history.map(() => 3);
  recTimeText.value = "00:00.0";

  // 合并 PCM 并编码 WAV
  const samples = mergeFloat32(rec.chunks);
  if (samples.length < sampleRate) {  // < 1 秒视为误触
    setMsg("录音太短（不足 1 秒），请重新录制", false);
    return;
  }
  durationS.value = samples.length / sampleRate;
  rec.blob = new Blob([encodeWav(samples, sampleRate)], { type: "audio/wav" });
  if (blobUrl.value) URL.revokeObjectURL(blobUrl.value);
  blobUrl.value = URL.createObjectURL(rec.blob);
}

function resetReview() {
  if (blobUrl.value) { URL.revokeObjectURL(blobUrl.value); blobUrl.value = ""; }
  rec.blob = null;
  durationS.value = 0;
  setMsg("");
}

async function save() {
  const name = matName.value.trim();
  if (!name) { setMsg("请先填写声音素材名称", false); return; }
  if (!rec.blob) { setMsg("请先完成录音", false); return; }
  const refText = recPrompt.value.trim();
  if (!refText) { setMsg("录音提示词为空，请填写朗读内容", false); return; }
  if (durationS.value > 30) { setMsg(`录音 ${durationS.value.toFixed(1)} 秒已超 30 秒上限，请重录`, false); return; }

  const fd = new FormData();
  fd.append("name", name);
  fd.append("ref_text", refText);   // 提示词即逐字稿
  fd.append("audio", new File([rec.blob], "recording.wav", { type: "audio/wav" }));
  fd.append("register_provider", "none");   // 纯素材采集：注册统一走「音色管理」

  saving.value = true;
  try {
    await api("/api/voices", { method: "POST", body: fd });
    setMsg(`素材「${name}」已保存到声音素材库`);
    matName.value = "";
    resetReview();
    await materialStore.load();
    emit("saved");
  } catch (e) {
    setMsg("保存失败：" + e.message, false);
  } finally {
    saving.value = false;
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

onBeforeUnmount(() => {
  if (running.value) stopRecording();
});
</script>

<style scoped>
.rec-bar { display: flex; align-items: center; gap: 12px; margin: 12px 0; padding: 12px;
  border: 1px solid #ebeef5; border-radius: 8px; background: #fafbfc; }
.rec-time { font-family: ui-monospace, Menlo, monospace; font-size: 15px; color: #444; min-width: 66px; }
.req { color: #f56c6c; font-weight: 400; font-size: 12px; }
.field-when { font-size: 12px; color: #909399; font-weight: 400; }
</style>
