<template>
  <div class="panel">
    <h2>音色管理 <span class="sub">按引擎查看与管理；含预置音色</span></h2>

    <!-- 注册新音色（素材 → 音色 的质变入口） -->
    <div class="reg-form">
      <div class="reg-title">＋ 注册新音色
        <span class="field-when">（选素材 → 选引擎 → 命名，音色名称同一引擎内必填且唯一）</span>
      </div>
      <div class="row3">
        <div>
          <label class="ep-label" style="margin:0 0 4px">声音素材</label>
          <select class="ep-select" style="width:100%; height:32px" v-model="regMat">
            <option v-for="m in materialStore.materials" :key="m.id" :value="m.id">{{ m.name }}</option>
          </select>
        </div>
        <div>
          <label class="ep-label" style="margin:0 0 4px">引擎</label>
          <select class="ep-select" style="width:100%; height:32px" v-model="regEngine">
            <option v-for="p in engineStore.usable" :key="p.id" :value="p.id">{{ p.display_name }}</option>
          </select>
        </div>
        <div>
          <label class="ep-label" style="margin:0 0 4px">音色名称 <span class="req">*</span></label>
          <input class="ep-input" style="height:32px" v-model="regName"
                 placeholder="同一引擎内不可重名" />
        </div>
        <button class="ep-btn primary" style="height:32px" :disabled="registering || !regName.trim()"
                @click="doRegister">{{ registering ? "注册中…" : "注册" }}</button>
      </div>
    </div>

    <!-- 引擎页签 -->
    <div class="tabs" style="align-items:center">
      <div v-for="p in engineStore.usable" :key="p.id" class="tab"
           :class="{active: tab === p.id}" @click="tab = p.id">
        {{ p.kind === "cloud" ? "☁️" : p.kind === "local" ? "💻" : "🧪" }} {{ p.display_name }}
      </div>
      <span v-if="tab === 'fish_cloud'" style="margin-left:auto; display:inline-flex; gap:8px; align-items:center">
        <button class="ep-btn small" :disabled="syncing" @click="syncCloud">{{ syncing ? "同步中…" : "🔄 同步云端音色" }}</button>
      </span>
    </div>
    <div v-if="tab === 'fish_cloud'" class="tip info" style="margin:0 0 10px">
      📌 云端音色（含新建/删除）在
      <a href="https://fish.audio/zh-CN/app/my-voices/" target="_blank" rel="noopener">fish.audio 我的音色页</a>
      管理；网页端有变动后点「同步云端音色」拉取最新列表（免费档 API 无创建配额，建议在网页端创建）。
    </div>

    <!-- 我的音色（注册产物） -->
    <div v-for="e in myVoices" :key="e.id" class="card">
      <div class="head">
        <b style="font-size:13.5px">{{ e.name }}</b>
        <span style="display:inline-flex; gap:6px">
          <span class="badge ok">我的音色</span>
          <span class="badge info">{{ stateLabel(e.state) }}</span>
        </span>
      </div>
      <div class="desc">来自素材：{{ materialName(e.material_id) }} · 可用于合成</div>
      <div class="ops">
        <button class="ep-btn small" @click="previewEntity(e)">▶ 试听</button>
        <button class="ep-btn small danger" @click="delEntity(e)">删除</button>
      </div>
    </div>

    <!-- 预置音色（引擎专属） -->
    <div v-for="v in presets" :key="v.voice_key" class="card">
      <div class="head">
        <b style="font-size:13.5px">{{ v.name }}</b>
        <span style="display:inline-flex; gap:6px">
          <span class="badge own">预置</span>
          <span v-if="v.state" class="badge info">{{ stateLabel(v.state) }}</span>
        </span>
      </div>
      <div class="desc">云端音色库 · {{ engineName }} 专属</div>
      <div class="ops">
        <button class="ep-btn small" :disabled="!isReady(v.state)" @click="previewPreset(v)">▶ 试听</button>
      </div>
    </div>

    <div v-if="!myVoices.length && !presets.length" class="tip info">
      该引擎暂无音色——用上方「注册新音色」表单，从素材注册{{ engineKindHint }}。
    </div>
    <div v-else-if="presetsError" class="tip warn">{{ presetsError }}</div>
    <div class="tip info" style="margin-top:8px">
      预置音色来自云端音色库（仅所属引擎可用）；删除我的音色不影响素材库中的素材。
    </div>
  </div>
</template>

<script setup>
/** 音色管理：唯一注册入口（素材→引擎→命名）+ 引擎页签（我的音色 ∪ 预置音色）。
 *  Fish 页签含「同步云端音色」（refresh=true 强制回源）。 */
import { computed, ref, watch } from "vue";
import { api } from "../api/client.js";
import { previewVoice } from "../api/preview.js";
import { useEngineStore } from "../stores/engine.js";
import { useMaterialStore } from "../stores/material.js";

const engineStore = useEngineStore();
const materialStore = useMaterialStore();

const tab = ref(engineStore.usable[0]?.id || "cosyvoice_local");
const regMat = ref("");
const regEngine = ref("");
const regName = ref("");
const registering = ref(false);
const syncing = ref(false);

const myVoices = computed(() => materialStore.voiceEntities.filter((e) => e.engine === tab.value));
const presetsRaw = computed(() => materialStore.presetsByEngine[tab.value]);
const presets = computed(() => Array.isArray(presetsRaw.value) ? presetsRaw.value : []);
const presetsError = computed(() =>
  presetsRaw.value && !Array.isArray(presetsRaw.value) ? presetsRaw.value.error : "");
const engineName = computed(() => engineStore.byId(tab.value)?.display_name || tab.value);
const engineKindHint = computed(() =>
  tab.value === "fish_cloud" ? "（免费 API 档无创建配额，建议网页端创建后同步）" : "");

// 表单默认值跟随数据与页签
watch(() => materialStore.materials, (ms) => {
  if (!regMat.value && ms.length) regMat.value = ms[0].id;
}, { immediate: true });
watch(tab, (t) => { regEngine.value = t; }, { immediate: true });
// 引擎列表异步加载：页签不在可用列表时落到第一个可用引擎
watch(() => engineStore.usable, (list) => {
  if (!list.length) return;
  if (!list.some((p) => p.id === tab.value)) tab.value = list[0].id;
  if (!regEngine.value || !list.some((p) => p.id === regEngine.value)) {
    regEngine.value = tab.value;
  }
}, { immediate: true });

const materialName = (mid) => materialStore.materials.find((m) => m.id === mid)?.name || "（素材已删除）";

function stateLabel(state) {
  if (state === "trained" || !state) return "就绪";
  if (state === "training" || state === "created") return "⏳ 训练中";
  return state;
}
const isReady = (state) => state === "trained" || !state;

async function doRegister() {
  const name = regName.value.trim();
  if (!regMat.value) { alert("请选择声音素材"); return; }
  if (!regEngine.value) { alert("请选择引擎"); return; }
  if (!name) { alert("请填写音色名称（同一引擎内必填且唯一）"); return; }
  // 前端查重（后端同引擎唯一索引兜底，409）
  if (myVoices.value.some((e) => e.name === name) || presets.value.some((p) => p.name === name)) {
    alert("该引擎已存在同名音色，请换一个名字"); return;
  }
  registering.value = true;
  try {
    await api(`/api/voices/${regMat.value}/register/${regEngine.value}`, {
      method: "POST",
      body: JSON.stringify({ voice_name: name }),
    });
    regName.value = "";
    await materialStore.load();
    tab.value = regEngine.value;   // 跳到目标引擎页签看结果
  } catch (e) {
    alert("注册失败：" + e.message + "——可换引擎重试（推荐 CosyVoice3 本地）");
  } finally {
    registering.value = false;
  }
}

async function syncCloud() {
  syncing.value = true;
  try {
    await materialStore.load(true);
  } catch (e) {
    alert("同步失败（已保留当前列表）：" + e.message);
  } finally {
    syncing.value = false;
  }
}

function previewEntity(e) {
  previewVoice(e.engine, `voiceent:${e.id}`).catch((err) => alert("试听失败：" + err.message));
}
function previewPreset(v) {
  previewVoice(tab.value, v.voice_key).catch((err) => alert("试听失败：" + err.message));
}

async function delEntity(e) {
  if (!confirm(`确定删除音色「${e.name}」？（${engineName.value} 侧模型一并删除，素材不受影响）`)) return;
  try {
    await api(`/api/voice-entities/${e.id}`, { method: "DELETE" });
    await materialStore.load();
  } catch (err) {
    alert("删除失败：" + err.message + "（引擎侧模型可能需手动清理）");
  }
}
</script>

<style scoped>
.reg-form { border: 1px solid #e1f0ff; background: #f7fbff; border-radius: 8px;
  padding: 12px 14px; margin-bottom: 14px; }
.reg-title { font-size: 13px; font-weight: 600; color: #303133; margin-bottom: 8px; }
.reg-form .row3 { display: grid; grid-template-columns: 0.9fr 1.35fr 1.1fr auto;
  gap: 8px; align-items: end; }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid #ebeef5; margin-bottom: 12px; flex-wrap: wrap; }
.tabs .tab { padding: 7px 16px; font-size: 13px; color: #606266; cursor: pointer;
  border-radius: 6px 6px 0 0; margin-bottom: -1px; border: 1px solid transparent; }
.tabs .tab.active { color: #409eff; border-color: #ebeef5 #ebeef5 #fff;
  background: #fff; font-weight: 600; }
.head { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.desc { font-size: 12px; color: #909399; margin: 6px 0 8px; }
.req { color: #f56c6c; font-weight: 400; font-size: 12px; }
.field-when { font-size: 12px; color: #909399; font-weight: 400; }
</style>
