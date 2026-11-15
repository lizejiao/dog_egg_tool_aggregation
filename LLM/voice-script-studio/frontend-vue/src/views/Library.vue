<template>
  <div class="lib-wrap">
    <!-- 行 1：录音 | 上传（纯素材采集入口） -->
    <div class="lib-row cols2">
      <RecorderPanel />
      <UploadPanel />
    </div>

    <!-- 行 2：素材库 | 音色管理 -->
    <div class="lib-row cols2">
      <div class="panel">
        <h2>我的声音素材库
          <span class="sub">录音+逐字稿=素材；注册引擎后才成为音色</span>
        </h2>
        <div v-if="!materialStore.loaded" class="tip info">加载中…</div>
        <template v-else>
          <MaterialCard v-for="m in materialStore.materials" :key="m.id" :m="m" />
          <div v-if="!materialStore.materials.length" class="tip info">
            暂无素材——从上方录音或上传创建。
          </div>
        </template>
      </div>

      <VoiceManager />
    </div>
  </div>
</template>

<script setup>
/** 素材库页：两行四区（录音｜上传 / 素材库｜音色管理），P0 定稿布局。 */
import { onMounted } from "vue";
import RecorderPanel from "../components/RecorderPanel.vue";
import UploadPanel from "../components/UploadPanel.vue";
import MaterialCard from "../components/MaterialCard.vue";
import VoiceManager from "../components/VoiceManager.vue";
import { useMaterialStore } from "../stores/material.js";

const materialStore = useMaterialStore();
onMounted(() => materialStore.load().catch((e) => console.error("素材加载失败", e)));
</script>

<style scoped>
.lib-wrap { max-width: 1400px; margin: 20px auto; padding: 0 28px 40px; }
.lib-row { display: grid; gap: 16px; align-items: stretch; margin-bottom: 16px; }
.lib-row.cols2 { grid-template-columns: 1fr 1fr; }
@media (max-width: 1080px) { .lib-row.cols2 { grid-template-columns: 1fr; } }
</style>
