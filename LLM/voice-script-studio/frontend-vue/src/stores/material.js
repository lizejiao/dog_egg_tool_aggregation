/** 素材与音色 store：两级实体（P2-0 已上线）。
 *  GET /api/voices 返回：
 *    clones         —— 素材（原材料）
 *    voice_entities —— 音色（素材注册引擎后的产物，同引擎名唯一）
 *    presets        —— 各引擎预置音色
 *  级联/页签视图统一消费 voicesByEngine。 */
import { defineStore } from "pinia";
import { api } from "../api/client.js";

export const useMaterialStore = defineStore("material", {
  state: () => ({
    materials: [],        // [{id, name, ref_text, status}]
    voiceEntities: [],    // [{id, name, engine, material_id, voice_key, state}]
    presetsByEngine: {},  // {pid: [{voice_key, name, state}]}（引擎异常时归一化为 []）
    presetErrors: {},     // {pid: "拉取失败原因"}
    loaded: false,
  }),
  getters: {
    /** 各引擎音色 = 我的音色（注册产物）∪ 预置音色 */
    voicesOf() {
      return (pid) => [
        ...this.voiceEntities
          .filter((e) => e.engine === pid)
          .map((e) => ({
            name: e.name, preset: false, state: e.state,
            voice_key: `voiceent:${e.id}`,   // 任务/试听统一用音色实体键
          })),
        ...(this.presetsByEngine[pid] || []).map((m) => ({
          name: m.name, preset: true, state: m.state, voice_key: m.voice_key,
        })),
      ];
    },
    /** 工作台级联：某引擎可用音色（合成级就绪的） */
    usableVoicesOf() {
      return (pid) => this.voicesOf(pid).filter((v) => v.state === "trained");
    },
    /** 素材的音色依赖（删素材前的提示用） */
    voicesOfMaterial() {
      return (mid) => this.voiceEntities.filter((e) => e.material_id === mid);
    },
  },
  actions: {
    async load(refresh = false) {
      const d = await api(`/api/voices${refresh ? "?refresh=true" : ""}`);
      this.materials = d.clones || [];
      this.voiceEntities = d.voice_entities || [];
      // 预置音色：引擎异常时后端返回 {error: "..."}，统一归一化为空数组（页面不崩）
      const raw = d.presets || {};
      const norm = {};
      for (const [pid, val] of Object.entries(raw)) {
        norm[pid] = Array.isArray(val) ? val : [];
        if (val && val.error) this.presetErrors = { ...this.presetErrors, [pid]: val.error };
      }
      this.presetsByEngine = norm;
      this.loaded = true;
    },
  },
});
