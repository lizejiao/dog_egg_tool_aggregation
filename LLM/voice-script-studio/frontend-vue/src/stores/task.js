/** 任务 store：创建 / 轮询 / 重试 / 历史（轮询收敛于此，组件只订阅）。 */
import { defineStore } from "pinia";
import { api } from "../api/client.js";

export const useTaskStore = defineStore("task", {
  state: () => ({
    activeTaskId: null,     // 当前跟踪任务（工作台监控区）
    activeTask: null,       // {task, segments}
    history: [],            // 最近任务列表
    polling: null,          // 定时器句柄
    exportsAvail: {},       // {mp3: bool, m4a: bool}（/api/meta/exports 前置探测）
  }),
  getters: {
    task: (s) => s.activeTask?.task || null,
    segments: (s) => s.activeTask?.segments || [],
    okCount() {
      return this.segments.filter((x) => x.status === "ok").length;
    },
    failCount() {
      return this.segments.filter((x) => x.status === "failed").length;
    },
    pct() {
      const n = this.segments.length;
      return n ? Math.round((this.okCount / n) * 100) : 0;
    },
    finished: (s) =>
      ["done", "partial_failed", "failed"].includes(s.activeTask?.task?.status || ""),
  },
  actions: {
    async create(payload) {
      const r = await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      this.activeTaskId = r.task_id;
      this.startPolling(r.task_id);
      this.loadHistory();
      return r;
    },
    async refresh() {
      if (!this.activeTaskId) return;
      this.activeTask = await api(`/api/tasks/${this.activeTaskId}`);
    },
    startPolling(taskId, intervalMs = 2000) {
      this.stopPolling();
      this.activeTaskId = taskId;
      this.refresh().catch(() => {});
      this.polling = setInterval(async () => {
        try {
          await this.refresh();
          if (this.finished) this.stopPolling();
        } catch (e) { console.error("轮询失败", e); }
      }, intervalMs);
    },
    stopPolling() {
      if (this.polling) clearInterval(this.polling);
      this.polling = null;
    },
    async retrySegment(seq) {
      if (!this.activeTaskId) return;
      await api(`/api/tasks/${this.activeTaskId}/segments/${seq}/retry`, { method: "POST" });
      this.startPolling(this.activeTaskId);
    },
    async retryAll() {
      if (!this.activeTaskId) return;
      await api(`/api/tasks/${this.activeTaskId}/retry`, { method: "POST" });
      this.startPolling(this.activeTaskId);
    },
    async loadHistory() {
      const d = await api("/api/tasks");
      this.history = d.tasks || [];
    },
    async loadExports() {
      try { this.exportsAvail = await api("/api/meta/exports"); }
      catch (e) { this.exportsAvail = {}; }
    },
    downloadUrl(fmt) {
      return this.activeTaskId
        ? `/api/tasks/${this.activeTaskId}/audio?format=${fmt}` : "#";
    },
    segAudioUrl(seq) {
      return this.activeTaskId
        ? `/api/tasks/${this.activeTaskId}/segments/${seq}/audio` : "#";
    },
  },
});
