import { createRouter, createWebHashHistory } from "vue-router";
// hash 模式：FastAPI 静态挂载时无需服务端路由回退配置
import Workbench from "./views/Workbench.vue";
import Library from "./views/Library.vue";
import Settings from "./views/Settings.vue";

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "workbench", component: Workbench },
    { path: "/library", name: "library", component: Library },
    { path: "/settings", name: "settings", component: Settings },
  ],
});
