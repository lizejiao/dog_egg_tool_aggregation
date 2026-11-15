import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 开发期代理：前端请求直连本地 FastAPI 后端
      "/api": { target: "http://127.0.0.1:8410", changeOrigin: true },
    },
  },
  build: { outDir: "dist" },
});
