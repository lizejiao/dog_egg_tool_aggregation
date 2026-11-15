<template>
  <div class="panel">
    <h2>上传克隆音色 <span class="sub">10~30 秒干净单人声，逐字稿一字不差</span></h2>

    <label class="ep-label">参考音频（wav / mp3 / m4a / opus）</label>
    <div class="upload-zone" :class="{drag: dragging}" @click="pickFile"
         @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false"
         @drop.prevent="onDrop">
      <template v-if="!file">{{ dragging ? "释放以选择文件" : "📄 拖拽文件到此处或点击上传 · ≤20MB · ≤30 秒" }}</template>
      <template v-else>🎵 {{ file.name }}（{{ (file.size / 1024 / 1024).toFixed(1) }}MB
        <template v-if="audioDur > 0"> · {{ audioDur.toFixed(1) }}s</template>）
        <a href="#" @click.stop.prevent="clearFile">移除</a></template>
    </div>
    <input ref="fileInput" type="file" accept=".wav,.mp3,.m4a,.opus" hidden @change="onFileChange" />

    <div v-if="audioDur > 30" class="tip warn">
      ⚠️ {{ audioDur.toFixed(1) }} 秒已超 30 秒上限（CosyVoice3 要求 ≤30s），保存将被拦截——请截短后再上传
    </div>

    <label class="ep-label">逐字稿（录音里说的话）</label>
    <textarea class="ep-textarea" rows="2" v-model="refText" style="resize:vertical"
      placeholder="与录音内容完全一致的文字"></textarea>

    <label class="ep-label">声音素材名称 <span class="req">*</span>
      <span class="field-when">（保存后进入素材库；在「音色管理」注册为可用音色）</span></label>
    <input class="ep-input" v-model="matName" placeholder="例：访谈录音-李苟蛋" />

    <div style="margin-top:14px; display:flex; gap:8px">
      <button class="ep-btn primary" :disabled="saving || !canSave" @click="save">
        {{ saving ? "保存中…" : "保存素材" }}
      </button>
    </div>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'warn'" style="margin-top:8px">{{ msg }}</div>
    <div class="tip info" style="margin-top:10px">
      上传的素材进入下方素材库；要用于合成，请到「音色管理」→ 注册新音色。仅克隆本人或已授权声线。
    </div>
  </div>
</template>

<script setup>
/** 上传面板（纯素材采集）：文件 + 逐字稿 + 素材名 → POST /api/voices（register_provider=none）。
 *  30 秒上限前置拦截：解码 object URL 读时长（wav/mp3/m4a 均可），超限禁保存。 */
import { computed, ref } from "vue";
import { api } from "../api/client.js";
import { useMaterialStore } from "../stores/material.js";

const emit = defineEmits(["saved"]);
const materialStore = useMaterialStore();

const file = ref(null);
const refText = ref("");
const matName = ref("");
const dragging = ref(false);
const saving = ref(false);
const msg = ref("");
const msgOk = ref(true);
const audioDur = ref(0);
const fileInput = ref(null);

const canSave = computed(() =>
  !!file.value && audioDur.value <= 30 && refText.value.trim() && matName.value.trim());

function setMsg(text, ok = true) { msg.value = text; msgOk.value = ok; }

function pickFile() { fileInput.value?.click(); }

function clearFile() {
  file.value = null;
  audioDur.value = 0;
}

function onFileChange(ev) { acceptFile(ev.target.files?.[0]); ev.target.value = ""; }
function onDrop(ev) { dragging.value = false; acceptFile(ev.dataTransfer?.files?.[0]); }

function acceptFile(f) {
  audioDur.value = 0;
  if (!f) return;
  const ext = "." + (f.name.split(".").pop() || "").toLowerCase();
  if (![".wav", ".mp3", ".m4a", ".opus"].includes(ext)) {
    setMsg("仅支持 wav / mp3 / m4a / opus，收到 " + (ext || "无扩展名"), false);
    return;
  }
  if (f.size > 20 * 1024 * 1024) { setMsg("文件超过 20MB 上限", false); return; }
  setMsg("");
  file.value = f;
  // 前置时长校验：浏览器解码读时长（30 秒上限）
  const url = URL.createObjectURL(f);
  const probe = new Audio();
  probe.preload = "metadata";
  probe.onloadedmetadata = () => {
    audioDur.value = isFinite(probe.duration) ? probe.duration : 0;
    URL.revokeObjectURL(url);
  };
  probe.onerror = () => URL.revokeObjectURL(url);
  probe.src = url;
}

async function save() {
  const fd = new FormData();
  fd.append("name", matName.value.trim());
  fd.append("ref_text", refText.value.trim());
  fd.append("audio", file.value);
  fd.append("register_provider", "none");   // 纯素材采集：注册统一走「音色管理」
  saving.value = true;
  try {
    const r = await api("/api/voices", { method: "POST", body: fd });
    setMsg(`素材「${r.name}」已保存到声音素材库`);
    file.value = null;
    refText.value = "";
    matName.value = "";
    audioDur.value = 0;
    await materialStore.load();
    emit("saved");
  } catch (e) {
    setMsg("保存失败：" + e.message, false);
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.upload-zone { border: 1px dashed #c0c4cc; border-radius: 6px; padding: 20px 0;
  text-align: center; color: #909399; font-size: 13px; cursor: pointer; transition: all .15s; }
.upload-zone:hover, .upload-zone.drag { border-color: #409eff; color: #409eff; }
.req { color: #f56c6c; font-weight: 400; font-size: 12px; }
.field-when { font-size: 12px; color: #909399; font-weight: 400; }
</style>
