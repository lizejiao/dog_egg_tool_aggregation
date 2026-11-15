/** 后端 REST 调用封装（自旧版 frontend/static/app.js 平移）。
 *  统一 JSON 解析 + 错误消息提取（后端 FastAPI {detail: "..."} 约定）。 */
export async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try {
      const j = await resp.json();
      if (j && j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (_) { /* 非 JSON 响应就用状态码 */ }
    throw new Error(msg);
  }
  return resp.json();
}
