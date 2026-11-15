<template>
  <div class="topbar">
    <span class="logo">🎙 Voice Script Studio</span>
    <nav class="nav">
      <router-link to="/">工作台</router-link>
      <router-link to="/library">素材库</router-link>
      <router-link to="/settings">设置</router-link>
    </nav>
    <span class="spacer"></span>
    <span class="status">
      <template v-if="engineStore.anyConnected">● {{ engineStore.statusText }}</template>
      <template v-else>○ 引擎加载中…</template>
    </span>
  </div>
  <router-view />
</template>

<script setup>
import { useEngineStore } from "./stores/engine.js";
const engineStore = useEngineStore();
engineStore.load();   // 首屏拉引擎列表（能力徽章/状态文案）
</script>

<style>
/* 顶栏导航激活态（router-link 自动加 .router-link-active） */
.topbar .nav a.router-link-active {
  background: #ecf5ff; color: #409eff; font-weight: 500;
}
</style>
