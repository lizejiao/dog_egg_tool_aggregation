<script setup>
/** 工作台：左=步骤栏（引擎→级联音色→指引）｜中=稿件+动态参数+合成｜右=最近合成。

本地引擎暖机门禁（2026-11-15 用户定稿）：选中本地引擎时，引擎条目右侧显示
暖机徽章（未暖机/暖机中/mm:ss 倒计时，最后 5 分钟变黄）；未暖机点「开始合成」
弹确认引导去设置页暖机（后端同样硬拦 409）。
*/
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useEngineStore } from "../stores/engine.js";
import { useMaterialStore } from "../stores/material.js";
import { useTaskStore } from "../stores/task.js";
import { api } from "../api/client.js";
import TaskMonitor from "../components/TaskMonitor.vue";
import RecentTasks from "../components/RecentTasks.vue";

const engines = useEngineStore();
const materials = useMaterialStore();
const tasks = useTaskStore();
const route = useRoute();

const engine = ref("");
const voiceKey = ref("");
const title = ref("");
const text = ref("");
const speed = ref(1.0), volumeDb = ref(0);
const pauseSeg = ref(0.35), pausePara = ref(0.8);
const uploadMsg = ref("");
const startMsg = ref("");
const fileInput = ref(null);
const starting = ref(false);

onMounted(async () => {
  engines.watchWarm();        // 暖机徽章/倒计时 ticker
  await Promise.all([engines.load(true), materials.load(), tasks.loadHistory(), tasks.loadExports()]);
  engine.value = engines.usable[0]?.id || "";
  // 深链：#/?task=<id> 直接载入该任务到监控区（右侧历史卡片点击同效）
  const t = route.query.task;
  if (t) tasks.startPolling(String(t));
});
onUnmounted(() => engines.unwatchWarm());

/* ---- 本地引擎暖机徽章 ---- */
const LOCAL_ID = "cosyvoice_local";
const isLocal = computed(() => engine.value === LOCAL_ID);
const warmBadge = computed(() => {
  if (!isLocal.value) return null;
  const st = engines.warm.state;
  if (st === "ready") return {
    cls: engines.localRemain <= 300 ? "badge warn-warm" : "badge ok",
    text: "⏳ " + engines.warmClock };
  if (st === "loading") return { cls: "badge info", text: "暖机中…" };
  return { cls: "badge miss", text: "未暖机" };
});
const localBlocked = computed(() =>
  isLocal.value && engines.warm.state !== "ready");

/* ---- 级联：切引擎重置音色为该引擎首个可用音色 ---- */
const voicesOfEngine = computed(() => materials.usableVoicesOf(engine.value));
watch(engine, () => { voiceKey.value = voicesOfEngine.value[0]?.voice_key || ""; },
  { immediate: true });
const selectedVoiceName = computed(() =>
  voicesOfEngine.value.find((v) => v.voice_key === voiceKey.value)?.name || "");
const caps = computed(() => engines.capsOf(engine.value) || {});
const engineName = computed(() =>
  engines.byId(engine.value)?.display_name || "");
const hasVoices = computed(() => voicesOfEngine.value.length > 0);

/* ---- 预估（段数=按 chars_per_segment≈80 估；时长≈4.3 字/秒 ÷ 语速） ---- */
const segCount = computed(() =>
  Math.max(1, Math.ceil(text.value.trim().length / 80)));
const estDur = computed(() => {
  const s = text.value.trim().length / 4.3 / (speed.value || 1);
  return text.value.trim()
    ? `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒` : "—";
});

/* ---- 上传 txt/md ---- */
async function onUploadFile(ev) {
  const f = ev.target.files[0];
  if (!f) return;
  uploadMsg.value = "解析中…";
  try {
    const fd = new FormData();
    fd.append("file", f);
    const r = await api("/api/upload/script", { method: "POST", body: fd });
    text.value = r.cleaned_text;
    if (!title.value) title.value = r.title;
    uploadMsg.value = `已解析：约 ${r.segment_count} 段`;
  } catch (e) { uploadMsg.value = e.message; }
  ev.target.value = "";
}

/* ---- 开始合成 ---- */
async function startSynth() {
  startMsg.value = "";
  if (!text.value.trim()) return (startMsg.value = "请输入或上传演讲稿");
  if (!hasVoices.value) return (startMsg.value = "该引擎暂无可用音色，请先到素材库注册");
  // 暖机门禁：本地引擎未 ready → 确认弹窗引导去设置页（后端亦 409 硬拦）
  if (localBlocked.value) {
    const loading = engines.warm.state === "loading";
    const go = confirm(loading
      ? "本地引擎正在暖机中（约 80 秒），请稍候再开始合成。\n\n点「确定」前往设置页查看进度。"
      : "本地引擎未暖机，无法开始合成。\n\n请先到 设置 → 暖机引擎 加载模型（首次约 80 秒）。\n点「确定」前往设置页。");
    if (go) window.location.hash = "#/settings";
    startMsg.value = loading ? "引擎暖机中，请稍候…" : "本地引擎未暖机，请先到设置页暖机";
    return;
  }
  starting.value = true;
  try {
    await tasks.create({
      title: title.value.trim() || "未命名稿件",
      text: text.value.trim(),
      voice_key: voiceKey.value,
      provider_id: engine.value,
      params: {
        speed: speed.value,
        pause_between: pauseSeg.value,
        pause_between_paragraphs: pausePara.value,
        volume_db: volumeDb.value,
      },
    });
    startMsg.value = "任务已启动，进度见下方监控区";
  } catch (e) { startMsg.value = e.message; }
  starting.value = false;
}
const volLabel = computed(() =>
  (volumeDb.value > 0 ? "+" : "") + volumeDb.value + " dB");
</script>

<template>
  <div class="page">
    <div class="wb-grid">

      <!-- 左：合成步骤 -->
      <div class="panel">
        <h2>合成步骤</h2>

        <div class="pick-title"><span class="step-no on">1</span>语音引擎
          <span v-if="engine" class="badge ok">已选</span></div>
        <div v-for="e in engines.usable" :key="e.id" class="engine-item"
             :class="{sel: engine === e.id}" @click="engine = e.id">
          <div class="name"><span>{{ e.display_name }}</span>
            <span class="name-badges">
              <!-- 本地引擎暖机徽章（未暖机/暖机中/倒计时，≤5min 变黄） -->
              <span v-if="e.id === LOCAL_ID && warmBadge" :class="warmBadge.cls"
                    style="font-variant-numeric:tabular-nums">{{ warmBadge.text }}</span>
              <span v-if="engine === e.id" class="badge ok">✓</span>
            </span>
          </div>
          <div class="desc">{{ e.notes }}</div>
        </div>
        <div v-if="!engines.usable.length" class="tip warn">
          无可用引擎——请到 设置 页启用（本地引擎或配置 Fish API Key）
        </div>

        <div class="pick-title" style="margin-top:12px"><span class="step-no on">2</span>音色（已按引擎过滤）
          <span class="badge info">{{ voicesOfEngine.length }} 个可用</span></div>
        <template v-if="voicesOfEngine.length">
          <div v-for="v in voicesOfEngine" :key="v.voice_key" class="voice-item"
               :class="{sel: voiceKey === v.voice_key}" @click="voiceKey = v.voice_key">
            <div class="row"><span>{{ v.name }}</span>
              <span class="meta">{{ v.preset ? "预置" : "我的音色" }}</span></div>
          </div>
        </template>
        <div v-else class="tip warn">
          该引擎暂无可用音色——请到 <router-link to="/library">素材库 → 音色管理</router-link> 为其注册
        </div>

        <div class="pick-title" style="margin-top:12px"><span class="step-no">3</span>合成
          <span class="badge info">在中间栏输入稿件</span></div>
        <div class="guide">
          选好引擎与音色后，在中间栏粘贴或上传稿件，点击「开始合成」；任务进度展示在稿件区下方，历史见右侧「最近合成」。
        </div>
      </div>

      <!-- 中：主栏 -->
      <div class="wb-main">
        <div class="panel">
          <h2>稿件输入 <span class="sub">粘贴文本或上传 .txt / .md</span></h2>
          <label class="ep-label">任务标题</label>
          <input class="ep-input" v-model="title" placeholder="未命名稿件" />

          <label class="ep-label">演讲稿内容（Markdown 自动清洗、智能分句）</label>
          <textarea class="ep-textarea" rows="7" v-model="text"
            style="resize:vertical; min-height:150px"
            placeholder="粘贴演讲稿，或上传 txt/md 文件"></textarea>

          <div class="tip info" style="display:flex; justify-content:space-between">
            <span>📄 已解析：约 <b>{{ segCount }}</b> 段 · 预计时长 <b>{{ estDur }}</b>
              （长稿自动分批合成）</span>
            <a href="#" @click.prevent="fileInput.click()">📎 上传 txt/md</a>
            <input type="file" accept=".txt,.md" ref="fileInput" style="display:none"
                   @change="onUploadFile" />
          </div>
          <div v-if="uploadMsg" class="tip" style="margin-top:6px">{{ uploadMsg }}</div>

          <!-- 合成参数：按引擎能力动态渲染（不支持的不显示，能力来自后端 Caps） -->
          <label class="ep-label">合成参数
            <span style="font-weight:400; color:#909399; font-size:12px">
              （按 {{ engineName }} 能力展示）</span></label>
          <div class="param-row" v-if="caps.speed">语速
            <input type="range" min="0.5" max="2" step="0.1" v-model.number="speed" />
            <b>{{ speed.toFixed(1) }}×</b></div>
          <div class="param-row">段间停顿
            <input type="range" min="0" max="1.5" step="0.05" v-model.number="pauseSeg" />
            <b>{{ pauseSeg.toFixed(2) }}s</b></div>
          <div class="param-row">段落停顿
            <input type="range" min="0" max="2.5" step="0.1" v-model.number="pausePara" />
            <b>{{ pausePara.toFixed(1) }}s</b></div>
          <div class="param-row" v-if="caps.volume">音量
            <input type="range" min="-20" max="20" step="1" v-model.number="volumeDb" />
            <b>{{ volLabel }}</b></div>

          <!-- 空音色守卫 -->
          <div v-if="!hasVoices" class="tip warn" style="margin-top:10px">
            ⚠️ 该引擎暂无可用音色——请到 <router-link to="/library">素材库 → 音色管理</router-link>，
            为该引擎注册音色后再回来合成
          </div>
          <button class="ep-btn primary" style="width:100%; height:38px; margin-top:12px; font-size:14px"
                  :disabled="!hasVoices || !voiceKey || starting" @click="startSynth">
            {{ starting ? "⟳ 正在创建任务…" :
               `▶ 开始合成${engineName ? "（" + engineName : ""}${hasVoices && selectedVoiceName ? " · " + selectedVoiceName : ""}${engineName ? "）" : ""}` }}
          </button>
          <div v-if="startMsg" class="tip" style="margin-top:8px">{{ startMsg }}</div>
        </div>

        <TaskMonitor />
      </div>

      <!-- 右：最近合成 -->
      <RecentTasks />
    </div>
  </div>
</template>

<style scoped>
.wb-grid { display: grid; grid-template-columns: 250px minmax(0, 1fr) 300px; gap: 16px;
  align-items: start; max-width: 1400px; margin: 20px auto; padding: 0 28px 40px; }
.wb-main { min-width: 0; }
.wb-main > .panel { margin-bottom: 16px; }
.engine-item, .voice-item { padding: 10px 12px; border: 1px solid #ebeef5; border-radius: 6px;
  margin-bottom: 8px; cursor: pointer; transition: all .15s; background: #fff; }
.engine-item:hover, .voice-item:hover { border-color: #a0cfff; }
.engine-item.sel, .voice-item.sel { border-color: #409eff; background: #ecf5ff; }
.engine-item .name, .voice-item .row { display: flex; justify-content: space-between;
  align-items: center; font-weight: 600; font-size: 13.5px; }
.engine-item .desc { font-size: 12px; color: #909399; margin-top: 4px; line-height: 1.7; }
.name-badges { display: inline-flex; gap: 5px; align-items: center; flex-shrink: 0; }
.badge.warn-warm { background: #fdf6ec; color: #e6a23c; border-color: #f5dab1; }
.voice-item .meta { font-size: 12px; color: #909399; font-weight: 400; }
.pick-title { font-size: 13px; font-weight: 600; color: #303133; margin: 4px 0 8px;
  display: flex; align-items: center; gap: 6px; }
.pick-title .badge { white-space: nowrap; flex-shrink: 0; }
.step-no { width: 20px; height: 20px; border-radius: 50%; background: #e4e7ed; color: #909399;
  display: inline-flex; align-items: center; justify-content: center; font-size: 12px;
  font-weight: 600; flex-shrink: 0; }
.step-no.on { background: #409eff; color: #fff; }
.guide { font-size: 12px; color: #909399; line-height: 1.7; }
.param-row { display: flex; gap: 10px; align-items: center; margin: 10px 0; font-size: 13px; }
.param-row input[type=range] { flex: 1; accent-color: #409eff; }
.param-row b { min-width: 40px; text-align: right; }
</style>
