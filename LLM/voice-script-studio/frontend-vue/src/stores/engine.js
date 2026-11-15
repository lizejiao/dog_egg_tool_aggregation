/** 引擎 store：列表 + 能力矩阵（Caps）+ 连接状态文案 + 本地引擎暖机状态。

暖机倒计时设计（2026-11-15 用户定稿）：
- 后端为唯一权威：warm_state（cold/loading/ready）+ remaining_s 由
  GET /api/providers/warm-state 下发
- 前端每 30s + 页面聚焦时向后端重同步；间隔期内本地每秒 -1 外推展示
- 到 00:00 主动再拉一次状态（此刻后端已自动释放，状态回 cold）
*/
import { defineStore } from "pinia";
import { api } from "../api/client.js";

const RESYNC_MS = 30_000;   // 后端重同步间隔
let _timer = null;          // 全局唯一 ticker（多组件订阅同一份）

export const useEngineStore = defineStore("engine", {
  state: () => ({
    providers: [],       // [{id, kind, display_name, enabled, supported_params...}]
    loaded: false,
    // 暖机（本地引擎）
    warm: { state: "cold", remaining_s: 0, busy: false },  // 后端权威快照
    localRemain: 0,      // 本地外推的剩余秒（每秒 -1）
    _lastSyncAt: 0,      // 上次后端同步的本地时刻（ms）
  }),
  getters: {
    usable: (s) => s.providers.filter((p) => p.enabled && p.implemented !== false),
    anyConnected: (s) => s.loaded && s.providers.length > 0,
    statusText: (s) => s.providers.filter((p) => p.enabled).map((p) => p.display_name).join(" · ") || "无启用引擎",
    byId: (s) => (pid) => s.providers.find((p) => p.id === pid) || null,
    /** 引擎能力（P2-0 后端补 supported_params；缺省时回退内置矩阵） */
    capsOf() {
      const FALLBACK = {
        fish_cloud: { speed: true, volume: false },
        cosyvoice_local: { speed: false, volume: false },
        mock: { speed: false, volume: false },
      };
      return (pid) => {
        const p = this.byId(pid);
        if (p && p.supported_params) return p.supported_params;
        return FALLBACK[pid] || { speed: false, volume: false };
      };
    },
    /** 本地引擎是否可立即使用（工作台开始合成守卫用） */
    localReady: (s) => s.warm.state === "ready",
    /** 倒计时展示文案：ready → mm:ss；其他态 → 状态字 */
    warmClock() {
      if (this.warm.state === "ready") {
        const s = Math.max(0, this.localRemain);
        return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
      }
      if (this.warm.state === "loading") return "暖机中…";
      return "未暖机";
    },
  },
  actions: {
    async load(force = false) {
      if (this.loaded && !force) return;
      try {
        const { providers } = await api("/api/providers");
        this.providers = providers;
        this.loaded = true;
      } catch (e) {
        console.error("引擎列表加载失败", e);
      }
    },
    async syncWarm() {
      try {
        const d = await api("/api/providers/warm-state");
        this.warm = { state: d.warm_state, remaining_s: d.remaining_s || 0,
                      busy: !!d.busy };
        this.localRemain = this.warm.remaining_s;
        this._lastSyncAt = Date.now();
      } catch { /* 查询失败保持原状，下轮再试 */ }
    },
    /** 订阅暖机状态：启动 1s ticker + 30s 重同步（多组件共享，幂等）。 */
    watchWarm() {
      this.syncWarm();
      if (_timer) return;
      _timer = setInterval(async () => {
        if (this.warm.state === "ready" && this.localRemain > 0) {
          this.localRemain -= 1;
        }
        const stale = Date.now() - this._lastSyncAt >= RESYNC_MS;
        const expired = this.warm.state === "ready" && this.localRemain <= 0;
        if (stale || expired || this.warm.state !== "ready") {
          await this.syncWarm();
        }
      }, 1000);
    },
    unwatchWarm() {
      if (_timer) { clearInterval(_timer); _timer = null; }
    },
  },
});
