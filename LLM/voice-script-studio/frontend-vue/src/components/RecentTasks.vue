<script setup>
/** 最近合成：历史任务卡（试听/三格式下载），点击卡片把任务载入左侧监控区。 */
import { computed, ref } from "vue";
import { useTaskStore } from "../stores/task.js";

const store = useTaskStore();
const showAll = ref(false);
const audioEl = new Audio();

const list = computed(() =>
  showAll.value ? store.history : store.history.slice(0, 3));

const ST_BADGE = {
  done: ["badge ok", "已完成"],
  partial_failed: ["badge miss", "部分失败"],
  failed: ["badge miss", "失败"],
  running: ["badge info", "合成中"],
  pending: ["badge info", "排队中"],
};
function badgeOf(st) { return ST_BADGE[st] || ["badge info", st || ""]; }

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function loadToMonitor(id) { store.startPolling(id); }
function playFinal(id) {
  audioEl.src = `/api/tasks/${id}/audio?format=wav`;
  audioEl.play();
}
function dlUrl(id, fmt) { return `/api/tasks/${id}/audio?format=${fmt}`; }
const mp3Ok = computed(() => store.exportsAvail.mp3 !== false);
</script>

<template>
  <div class="panel">
    <h2>最近合成 <span class="sub">共 {{ store.history.length }}</span></h2>
    <div class="history-scroll" v-if="store.history.length">
      <div v-for="t in list" :key="t.id" class="card" @click="loadToMonitor(t.id)"
           :class="{sel: t.id === store.activeTaskId}" style="cursor:pointer">
        <div class="row1">
          <b class="t">{{ t.title }}</b>
          <span class="badge" :class="badgeOf(t.status)[0]">{{ badgeOf(t.status)[1] }}</span>
        </div>
        <div class="meta">
          {{ t.provider_id }} · {{ t.ok_segments }}/{{ t.total_segments }} 段
          <template v-if="t.failed_segments"> · 失败 {{ t.failed_segments }}</template>
          · {{ fmtTime(t.created_at) }}
        </div>
        <div class="ops" @click.stop>
          <button class="ep-btn small" v-if="t.ok_segments" @click="playFinal(t.id)">▶ 试听</button>
          <a class="ep-btn small" v-if="t.status === 'done'" :href="dlUrl(t.id, 'wav')">WAV</a>
          <a class="ep-btn small" v-if="t.status === 'done'" :href="dlUrl(t.id, 'm4a')">M4A</a>
          <a class="ep-btn small" v-if="t.status === 'done'" :class="{disabled: !mp3Ok}"
             :href="mp3Ok ? dlUrl(t.id, 'mp3') : '#'"
             :title="mp3Ok ? '' : 'MP3 导出不可用：缺少 lameenc 依赖'"
             @click.prevent="!mp3Ok && alert($event.target.title)">MP3</a>
        </div>
      </div>
    </div>
    <div v-else class="tip info" style="text-align:center">
      暂无历史任务；合成完成后出现在这里（含断点续跑）
    </div>
    <button class="ep-btn" v-if="store.history.length > 3" style="width:100%"
            @click="showAll = !showAll">
      {{ showAll
          ? "收起"
          : `更多历史任务（还有 ${store.history.length - 3} 条${store.history.length > 10 ? "，展开后滚动查看" : ""}）` }}
    </button>
  </div>
</template>

<style scoped>
.row1 { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.row1 .t { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row1 .badge { white-space: nowrap; flex-shrink: 0; }
.meta { font-size: 12px; color: #909399; margin: 6px 0 8px; }
a.ep-btn { display: inline-flex; align-items: center; text-decoration: none; }
a.ep-btn.disabled { opacity: .45; cursor: not-allowed; }
/* 展开态：最多直显 10 条卡片，更多条数纵向滚动查看；
   同时不超过一屏高度，避免长列表把页面撑太长 */
.history-scroll { max-height: min(1120px, calc(100vh - 240px)); overflow-y: auto;
  padding-right: 4px; overscroll-behavior: contain; }
.history-scroll::-webkit-scrollbar { width: 6px; }
.history-scroll::-webkit-scrollbar-thumb { background: #dcdfe6; border-radius: 3px; }
</style>
