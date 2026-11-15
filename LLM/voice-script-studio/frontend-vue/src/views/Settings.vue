<script setup>
/** 设置：引擎卡片（Fish 云端 / 本地 / Mock）+ 全局合成参数。

本地引擎暖机门禁（2026-11-15 用户定稿）：暖机=唯一加载入口；三态按钮
（🔥暖机 / 暖机中… / ⏹释放）；ready 态显示 30 分钟自动释放倒计时；
每次合成/试听自动续期（倒计时会跳回满值，属正常表现）。
*/
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import { useEngineStore } from "../stores/engine.js";
import { api } from "../api/client.js";

const engines = useEngineStore();
const forms = reactive({});      // {pid: {enabled, api_key, model, proxy, fp16, idle_kill_s}}
const msgs = reactive({});       // {pid: {text, ok}}
const busy = reactive({});       // {pid: "save"|"test"|"warm"|"release"}
const synth = reactive({
  chars_per_segment: 80, max_retries: 3,
  pause_between: 0.35, pause_between_paragraphs: 0.8,
});
const synthMsg = ref("");
const synthBusy = ref(false);

const KIND_LABEL = { cloud: "云端引擎", local: "本地引擎", mock: "离线自测" };
const enabledCount = computed(() => engines.providers.filter((p) => p.enabled).length);

// 本地引擎暖机三态（store 的后端权威快照驱动）
const localWarm = computed(() => engines.warm);          // {state, remaining_s, busy}
const warmClock = computed(() => engines.warmClock);     // mm:ss / 暖机中… / 未暖机
const warmWarn = computed(() =>
  engines.warm.state === "ready" && engines.localRemain <= 300);  // 最后 5 分钟黄色

onMounted(async () => {
  engines.watchWarm();                     // 倒计时 ticker（30s 重同步 + 1s 外推）
  await engines.load(true);
  for (const p of engines.providers) {
    forms[p.id] = {
      enabled: p.enabled,
      api_key: "",
      model: p.model || "s2.1-pro-free",
      proxy: p.proxy || "",
      fp16: p.fp16 ?? false,
      idle_kill_s: p.idle_kill_s ?? 1800,
    };
  }
  try {
    const d = await api("/api/config/synthesis");
    Object.assign(synth, d.synthesis);
  } catch (e) { synthMsg = e.message; }
});
onUnmounted(() => engines.unwatchWarm());

function setMsg(pid, text, ok = true) { msgs[pid] = { text, ok }; }

async function save(p) {
  busy[p.id] = "save";
  setMsg(p.id, "保存中…");
  try {
    const f = forms[p.id];
    const body = { enabled: f.enabled };
    if (p.id === "fish_cloud") {
      if (f.api_key.trim()) body.api_key = f.api_key.trim();
      body.model = f.model;
      body.proxy = f.proxy.trim();
    }
    if (p.id === "cosyvoice_local") {
      body.fp16 = f.fp16;
      body.idle_kill_s = Number(f.idle_kill_s) || 300;
    }
    await api(`/api/providers/${p.id}`, {
      method: "PUT", body: JSON.stringify(body),
    });
    setMsg(p.id, "已保存（立即生效，无需重启）");
    forms[p.id].api_key = "";
    await engines.load(true);
  } catch (e) { setMsg(p.id, e.message, false); }
  busy[p.id] = "";
}

async function testConn(p) {
  busy[p.id] = "test";
  setMsg(p.id, "测试中…");
  try {
    const r = await api(`/api/providers/${p.id}/test`, { method: "POST" });
    setMsg(p.id, (r.ok ? "✓ " : "✗ ") + r.message, r.ok);
  } catch (e) { setMsg(p.id, e.message, false); }
  busy[p.id] = "";
}

async function warmup(p) {
  busy[p.id] = "warm";
  setMsg(p.id, "暖机中…（本地引擎首次约 80 秒，页面可离开）");
  try {
    const r = await api(`/api/providers/${p.id}/warmup`, { method: "POST" });
    setMsg(p.id, (r.ok ? "✓ " : "✗ ") + r.message, r.ok);
    engines.syncWarm();
  } catch (e) { setMsg(p.id, e.message, false); engines.syncWarm(); }
  busy[p.id] = "";
}

async function releaseLocal(p) {
  busy[p.id] = "release";
  try {
    const r = await api("/api/providers/cosyvoice_local/release", { method: "POST" });
    setMsg(p.id, "✓ " + r.message, true);
  } catch (e) { setMsg(p.id, e.message, false); }
  engines.syncWarm();
  busy[p.id] = "";
}

async function saveSynth() {
  synthBusy.value = true;
  synthMsg.value = "";
  try {
    const r = await api("/api/config/synthesis", {
      method: "PUT", body: JSON.stringify(synth),
    });
    Object.assign(synth, r.synthesis);
    synthMsg.value = "已保存（新任务生效；进行中的任务不受影响）";
  } catch (e) { synthMsg.value = e.message; }
  synthBusy.value = false;
}
</script>

<template>
  <div class="set-wrap">

    <div class="panel" v-for="p in engines.providers" :key="p.id">
      <div class="engine-head">
        <span class="t">
          {{ p.kind === "cloud" ? "☁️" : p.kind === "local" ? "💻" : "🧪" }}
          {{ p.display_name }}
          <span class="badge" :class="p.enabled ? 'ok' : 'info'">
            {{ p.enabled ? "已启用" : "已停用" }}</span>
        </span>
        <span class="badge info">{{ KIND_LABEL[p.kind] || p.kind }}
          <template v-if="p.max_concurrency"> · 并发 ≤{{ p.max_concurrency }}</template>
        </span>
      </div>

      <!-- Fish 云端专属 -->
      <template v-if="p.id === 'fish_cloud'">
        <label class="ep-label">API Key
          <span class="hint-inline" v-if="p.api_key_configured">
            已配置 {{ p.api_key_masked }}（留空保持不变）</span></label>
        <input class="ep-input" type="password" v-model="forms[p.id].api_key"
               :placeholder="p.api_key_configured ? '已配置（留空保持不变）' : '粘贴 fish.audio 的 API Key'" />
        <div class="row2" style="margin-top:4px">
          <div>
            <label class="ep-label">模型</label>
            <select class="ep-select" v-model="forms[p.id].model">
              <option value="s2.1-pro-free">s2.1-pro-free（免费档，至 2026-11-30）</option>
              <option value="s2.1-pro">s2.1-pro（付费 $15/百万字节）</option>
            </select>
          </div>
          <div>
            <label class="ep-label">出网代理（沙箱环境必填）</label>
            <input class="ep-input" v-model="forms[p.id].proxy"
                   placeholder="如 http://127.0.0.1:7890，留空走系统环境变量" />
          </div>
        </div>
      </template>

      <!-- 本地引擎专属 -->
      <template v-if="p.id === 'cosyvoice_local'">
        <div class="meta-block">{{ p.notes }}</div>
        <!-- 暖机状态行 -->
        <div class="warm-bar" :class="localWarm.state">
          <span class="dot"></span>
          <template v-if="localWarm.state === 'ready'">
            <b>引擎已就绪</b>
            <span class="clock" :class="{warn: warmWarn}">⏳ {{ warmClock }}</span>
            <span class="hint">后自动释放 · 每次合成/试听自动续期 30 分钟</span>
          </template>
          <template v-else-if="localWarm.state === 'loading'">
            <b>暖机中…</b>
            <span class="hint">正在加载权重（首次约 80 秒）</span>
          </template>
          <template v-else>
            <b>未暖机</b>
            <span class="hint">模型未加载——合成/试听前需先点「暖机引擎」</span>
          </template>
        </div>
        <div class="row2">
          <div>
            <label class="ep-label">半精度推理 fp16（提速约 30%）</label>
            <div class="switch" :class="{on: forms[p.id].fp16}"
                 @click="forms[p.id].fp16 = !forms[p.id].fp16">
              <span class="track"></span>{{ forms[p.id].fp16 ? "开启" : "关闭（稳妥）" }}
            </div>
          </div>
          <div>
            <label class="ep-label">空闲自动释放（分钟，使用后续期）</label>
            <input class="ep-input" type="number" min="5" step="5"
                   v-model.number="forms[p.id].idle_kill_s" />
          </div>
        </div>
        <div class="tip info">修改 fp16 / 回收时间并保存后，已暖机的引擎会立即释放，需重新暖机生效新配置</div>
      </template>

      <!-- Mock / 其他 -->
      <div v-if="p.id === 'mock'" class="meta-block">{{ p.notes }}</div>

      <div class="ops">
        <button class="ep-btn primary" :disabled="!!busy[p.id]" @click="save(p)">
          {{ busy[p.id] === "save" ? "保存中…" : "保存" }}</button>
        <button class="ep-btn" :disabled="!!busy[p.id]" @click="testConn(p)">
          {{ busy[p.id] === "test" ? "测试中…" : "测试连接" }}</button>
        <!-- 本地引擎三态按钮：cold=暖机 / loading=禁用 / ready=释放 -->
        <button v-if="p.id === 'cosyvoice_local' && localWarm.state === 'ready'"
                class="ep-btn danger" :disabled="!!busy[p.id] || localWarm.busy"
                @click="releaseLocal(p)">
          {{ busy[p.id] === "release" ? "释放中…" : "⏹ 释放引擎" }}</button>
        <button v-else-if="p.id === 'cosyvoice_local'" class="ep-btn"
                :disabled="!!busy[p.id] || localWarm.state === 'loading'" @click="warmup(p)">
          {{ localWarm.state === "loading" || busy[p.id] === "warm"
              ? "暖机中…（约 80 秒）" : "🔥 暖机引擎" }}</button>
        <span class="msg" :class="{bad: msgs[p.id] && !msgs[p.id].ok}">
          {{ msgs[p.id]?.text || "" }}</span>
      </div>
    </div>

    <!-- 全局合成参数 -->
    <div class="panel">
      <h2>全局合成参数
        <span class="sub">所有引擎共用的默认值（任务级可覆盖）</span></h2>
      <div class="row2">
        <div>
          <label class="ep-label">分句目标上限（字/段）</label>
          <input class="ep-input" type="number" min="30" max="200" step="10"
                 v-model.number="synth.chars_per_segment" />
        </div>
        <div>
          <label class="ep-label">段级最大重试</label>
          <input class="ep-input" type="number" min="1" max="5" v-model.number="synth.max_retries" />
        </div>
      </div>
      <div class="row2" style="margin-top:4px">
        <div>
          <label class="ep-label">段间停顿（秒）</label>
          <input class="ep-input" type="number" min="0" max="2" step="0.05"
                 v-model.number="synth.pause_between" />
        </div>
        <div>
          <label class="ep-label">段落间停顿（秒）</label>
          <input class="ep-input" type="number" min="0" max="3" step="0.1"
                 v-model.number="synth.pause_between_paragraphs" />
        </div>
      </div>
      <div class="ops">
        <button class="ep-btn primary" :disabled="synthBusy" @click="saveSynth">
          {{ synthBusy ? "保存中…" : "保存全局参数" }}</button>
        <span class="msg">{{ synthMsg }}</span>
      </div>
    </div>

  </div>
</template>

<style scoped>
.set-wrap { max-width: 880px; margin: 20px auto; padding: 0 28px 40px; }
.set-wrap > .panel { margin-bottom: 16px; }
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.engine-head { display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 12px; gap: 8px; }
.engine-head .t { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.meta-block { font-size: 12.5px; color: #909399; line-height: 1.8; margin-bottom: 4px; }
.hint-inline { font-weight: 400; color: #909399; font-size: 12px; }
.ops { display: flex; gap: 8px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
.msg { font-size: 12.5px; color: #67c23a; }
.msg.bad { color: #f56c6c; }
.switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 13px; color: #606266; }
.switch .track { width: 38px; height: 20px; border-radius: 10px; background: #dcdfe6;
  position: relative; transition: background .2s; }
.switch .track::after { content: ""; position: absolute; top: 2px; left: 2px; width: 16px;
  height: 16px; border-radius: 50%; background: #fff; transition: left .2s;
  box-shadow: 0 1px 2px rgba(0,0,0,.15); }
.switch.on .track { background: #409eff; }
.switch.on .track::after { left: 20px; }
/* 暖机状态条 */
.warm-bar { display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border-radius: 6px; font-size: 13px; margin-bottom: 10px;
  background: #f4f4f5; color: #909399; border: 1px solid #e9e9eb; }
.warm-bar.ready { background: #f0f9eb; color: #67c23a; border-color: #e1f3d8; }
.warm-bar.loading { background: #fdf6ec; color: #e6a23c; border-color: #faecd8; }
.warm-bar .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.warm-bar b { color: inherit; }
.warm-bar .clock { font-variant-numeric: tabular-nums; font-weight: 600; color: #67c23a; }
.warm-bar .clock.warn { color: #e6a23c; }
.warm-bar .hint { font-size: 12px; color: #a8abb2; }
</style>
