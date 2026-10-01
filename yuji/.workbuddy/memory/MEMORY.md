# 项目记忆 — 悦己瑜伽小程序（yuji）

## 云开发环境
- appid: `wxc79b79c31d72336c`
- 云开发环境 ID: `cloud1-d3g9bmd6v8c98fd90`（已写入 `utils/config.js` 的 CLOUD_ENV）
- 云函数共 7 个：login、init、course、booking、member、package、notice，位于 `cloudfunctions/`
- 云函数统一返回结构：`{ code: 0, data, message }`，调用封装在 `utils/api.js`

## 排查记录（2026-10-02）
- 曾报 `cloud.callFunction:fail errCode` 为空 → 根因是开发者工具 cloudfunctions「未选择环境」+ 云函数未部署
- 解决：选择云环境 → 填 CLOUD_ENV → 右键各云函数「上传并部署：云端安装依赖」
