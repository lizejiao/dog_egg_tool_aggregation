/** 试听：POST 生成（同音色只真实合成一次）→ 共享 <audio> 播放缓存。

本地引擎前置拦截（2026-11-15 用户定稿）：未暖机直接提示去设置页，
不发请求（后端对未暖机试听同样 409 硬拦）。
*/
import { useEngineStore } from "../stores/engine.js";

let audioEl = null;

export async function previewVoice(providerId, voiceKey) {
  if (providerId === "cosyvoice_local") {
    const engines = useEngineStore();
    if (engines.warm.state !== "ready") {
      const loading = engines.warm.state === "loading";
      throw new Error(loading
        ? "本地引擎正在暖机中，请稍候再试听"
        : "本地引擎未暖机：请到 设置 → 暖机引擎 后再试听");
    }
  }
  const r = await fetch(
    `/api/voices/preview?provider_id=${encodeURIComponent(providerId)}` +
    `&voice_key=${encodeURIComponent(voiceKey)}`,
    { method: "POST" });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* 非 JSON */ }
    throw new Error(msg);
  }
  const { preview_url } = await r.json();
  if (!audioEl) audioEl = new Audio();
  audioEl.src = preview_url;
  audioEl.play();
}
