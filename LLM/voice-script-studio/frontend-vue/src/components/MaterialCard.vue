<template>
  <div class="card">
    <div class="head">
      <b style="font-size:13.5px">{{ m.name }}</b>
      <span style="display:inline-flex; gap:6px">
        <span v-for="e in deps" :key="e.id" class="badge ok"
              :title="`${engineName(e.engine)} · 可用于合成`">✓ {{ engineName(e.engine) }}</span>
        <span v-if="!deps.length" class="badge miss" title="未注册，不可用于合成">未注册</span>
      </span>
    </div>
    <div class="desc">逐字稿：{{ m.ref_text?.slice(0, 40) }}…</div>
    <div class="ops">
      <button class="ep-btn small" :disabled="!deps.length" @click="preview"
              :title="deps.length ? '' : '素材未注册引擎，先到「音色管理」注册'">▶ 试听</button>
      <button class="ep-btn small" @click="download">下载参考音频</button>
      <button class="ep-btn small" @click="copyText">复制逐字稿</button>
      <button class="ep-btn small danger" @click="del">删除</button>
    </div>
  </div>
</template>

<script setup>
/** 素材卡：素材级操作（试听/下载参考音频/复制逐字稿/删除）。
 *  删素材提示音色依赖（P0 定稿交互：删素材时展示依赖数）。 */
import { computed } from "vue";
import { api } from "../api/client.js";
import { previewVoice } from "../api/preview.js";
import { useEngineStore } from "../stores/engine.js";
import { useMaterialStore } from "../stores/material.js";

const props = defineProps({ m: { type: Object, required: true } });
const emit = defineEmits(["deleted"]);

const engineStore = useEngineStore();
const materialStore = useMaterialStore();

const deps = computed(() => materialStore.voicesOfMaterial(props.m.id));
const engineName = (pid) => engineStore.byId(pid)?.display_name || pid;

function preview() {
  const e = deps.value[0];
  if (!e) return;
  previewVoice(e.engine, `voiceent:${e.id}`).catch((err) => alert("试听失败：" + err.message));
}

function download() {
  window.open(`/api/voices/${props.m.id}/audio`, "_blank");
}

async function copyText() {
  try {
    await navigator.clipboard.writeText(props.m.ref_text || "");
    alert("逐字稿已复制——可粘贴到 fish.audio 网页端创建音色时的文本框");
  } catch (_) {
    prompt("请手动复制逐字稿：", props.m.ref_text);
  }
}

async function del() {
  const n = deps.value.length;
  const hint = n
    ? `删除素材「${props.m.name}」将同时删除其 ${n} 个已注册音色（${deps.value.map((d) => engineName(d.engine)).join("、")}），引擎侧模型一并删除。`
    : `确定删除素材「${props.m.name}」？（未注册，无音色依赖）`;
  if (!confirm(hint)) return;
  try {
    await api(`/api/voices/${props.m.id}`, { method: "DELETE" });
    await materialStore.load();
    emit("deleted", props.m.id);
  } catch (e) {
    alert("删除失败：" + e.message + (e.message.includes("delete_errors") ? "（部分引擎侧模型可能需手动清理）" : ""));
  }
}
</script>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.desc { font-size: 12.5px; color: #909399; margin: 8px 0; }
</style>
