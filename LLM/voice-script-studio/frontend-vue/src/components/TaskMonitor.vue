<script setup>
/** 任务监控：总进度置顶 + 段列表（折叠滚动/只看失败段/段级播放与重试）+ 三格式下载 + 续跑。 */
import { computed, ref } from "vue";
import { useTaskStore } from "../stores/task.js";

const store = useTaskStore();
const onlyFail = ref(false);
const expanded = ref(false);
const audioEl = new Audio();

const STATUS_BADGE = {
  done: ["badge ok", "已完成"],
  partial_failed: ["badge miss", "部分失败"],
  failed: ["badge miss", "失败"],
  pending: ["badge info", "排队中"],
  running: ["badge info", "合成中"],
};
const badge = computed(() => {
  const st = store.task?.status;
  return STATUS_BADGE[st] || ["badge info", st || ""];
});
const ST_ICON = { ok: "✓", failed: "✗", running: "⟳", pending: "·" };

const okSegs = computed(() => store.segments.filter((s) => s.status === "ok"));
const tailSegs = computed(() =>
  store.segments.filter((s) => s.status !== "ok"));
/** 折叠视图：成功段收起为首尾折叠条，其余（失败/合成中/待合成）正常展示 */
const shownSegs = computed(() => {
  let list = expanded.value ? store.segments : tailSegs.value;
  if (onlyFail.value) list = list.filter((s) => s.status === "failed");
  return list;
});

const hasOutput = computed(() => !!store.task?.output_path);
const mp3Ok = computed(() => store.exportsAvail.mp3 !== false);
const statusText = computed(() => {
  const st = { okCount: store.okCount, failCount: store.failCount };
  const extra = store.task?.output_path ? " · 整稿已生成" : "";
  return `完成 ${store.okCount}/${store.segments.length} 段` +
    (st.failCount ? ` · 失败 ${st.failCount}` : "") + extra;
});

function playSeq(seq) {
  audioEl.src = store.segAudioUrl(seq);
  audioEl.play();
}
function dlUrl(fmt) { return store.downloadUrl(fmt); }
</script>

<template>
  <div class="panel" v-if="store.task">
    <h2>任务监控
      <span class="sub">{{ store.finished ? "已结束" : "进行中" }}</span>
      <span style="margin-left:auto; display:inline-flex; gap:8px">
        <button class="ep-btn small" :class="{primary: onlyFail}"
                @click="onlyFail = !onlyFail">只看失败段</button>
        <button class="ep-btn small" v-if="store.segments.length > 30"
                @click="expanded = !expanded">{{ expanded ? "全部折叠" : "全部展开" }}</button>
      </span>
    </h2>

    <div class="progress" style="margin-bottom:10px"><i :style="{width: store.pct + '%'}"></i></div>
    <div class="task-head">
      <b>{{ store.task.title }}</b>
      <span class="badge" :class="badge[0]">{{ badge[1] }} · {{ statusText }}</span>
    </div>
    <div class="meta-line">
      {{ store.task.provider_id }} · 已完成 {{ store.pct }}%
      <template v-if="store.failCount">
        · <a href="#" @click.prevent="store.retryAll()">失败段全部重试</a>
      </template>
    </div>

    <div class="seg-scroll">
      <div class="seg-fold" v-if="!expanded && okSegs.length" @click="expanded = true">
        ▲ 已折叠 {{ okSegs.length }} 个成功段（点击展开）
      </div>
      <template v-for="s in shownSegs" :key="s.seq">
        <div class="seg-row">
          <span class="st" :class="s.status">{{ ST_ICON[s.status] || "?" }}</span>
          <span class="tx" :title="s.text">{{ s.text }}</span>
          <button v-if="s.status === 'ok'" class="ep-btn small" @click="playSeq(s.seq)">播放</button>
          <button v-if="s.status === 'failed'" class="ep-btn small"
                  @click="store.retrySegment(s.seq)">重试</button>
        </div>
      </template>
      <div class="seg-fold" v-if="onlyFail && shownSegs.length === 0"
            @click="onlyFail = false">
        无失败段 · 全部 {{ store.segments.length }} 段正常（点此取消过滤）
      </div>
    </div>

    <div class="dl-row" v-if="hasOutput">
      <a class="ep-btn small" :href="dlUrl('wav')">WAV</a>
      <a class="ep-btn small" :href="dlUrl('m4a')">M4A</a>
      <a class="ep-btn small" :class="{disabled: !mp3Ok}" :href="mp3Ok ? dlUrl('mp3') : '#'"
         :title="mp3Ok ? '' : 'MP3 导出不可用：缺少 lameenc 依赖'"
         @click.prevent="!mp3Ok && alert($event.target.title)">MP3</a>
    </div>
  </div>
  <div class="panel" v-else>
    <h2>任务监控</h2>
    <div class="tip info" style="text-align:center">
      点「开始合成」后任务出现在这里；也可点击右侧「最近合成」载入历史任务续看进度
    </div>
  </div>
</template>

<style scoped>
.task-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.meta-line { font-size: 12.5px; color: #909399; margin: 6px 0 4px; }
.seg-scroll { max-height: 260px; overflow-y: auto; overflow-x: auto;
  border: 1px solid #f0f2f5; border-radius: 6px; padding: 4px 10px; margin-top: 8px; }
.seg-scroll::-webkit-scrollbar { height: 6px; width: 6px; }
.seg-scroll::-webkit-scrollbar-thumb { background: #dcdfe6; border-radius: 3px; }
.seg-fold { text-align: center; font-size: 12.5px; color: #909399; padding: 7px 0;
  cursor: pointer; }
.seg-fold:hover { color: #409eff; }
.seg-row { display: flex; gap: 8px; align-items: center; padding: 5px 0;
  border-bottom: 1px dashed #f5f7fa; font-size: 12.5px;
  width: max-content; min-width: 100%; white-space: nowrap; }
.seg-row .st { width: 18px; text-align: center; flex-shrink: 0; font-weight: 600; }
.seg-row .st.ok { color: #67c23a; }
.seg-row .st.failed { color: #f56c6c; }
.seg-row .st.running { color: #409eff; }
.seg-row .st.pending { color: #c0c4cc; }
.seg-row .tx { flex: 1; white-space: nowrap; }
.dl-row { display: flex; gap: 8px; margin-top: 10px; }
a.ep-btn { display: inline-flex; align-items: center; text-decoration: none; }
a.ep-btn.disabled { opacity: .45; cursor: not-allowed; }
</style>
