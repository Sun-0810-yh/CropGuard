import { createRouter, createWebHistory } from 'vue-router'

// 路由与服务端一一对应：api_server 对这些路径都返回同一个 index.html。
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/WorkbenchView.vue'), meta: { title: '检测台' } },
    // 现场评分：同一个检测台组件的「评分作业」模式（GUI 的 20 样本流程）
    { path: '/eval', component: () => import('./views/WorkbenchView.vue'), meta: { title: '现场评分', mode: 'eval' } },
    { path: '/eval/review', component: () => import('./views/EvalReviewView.vue'), meta: { title: '样本评测' } },
    { path: '/stream', component: () => import('./views/LiveView.vue'), meta: { title: '实时监测' } },
    { path: '/ask', component: () => import('./views/AskView.vue'), meta: { title: '农技问答' } },
    { path: '/spray', component: () => import('./views/SprayView.vue'), meta: { title: '喷淋控制' } },
    { path: '/system', component: () => import('./views/SystemView.vue'), meta: { title: '设备与接入' } },
    { path: '/dashboard', component: () => import('./views/DashboardView.vue'), meta: { title: '数据看板' } },
    { path: '/m', component: () => import('./views/MobileView.vue'), meta: { title: '手机端', mobile: true } },
    { path: '/:rest(.*)*', redirect: '/' },
  ],
})

router.afterEach((to) => {
  document.title = `${to.meta.title || '株得CropGuard'} · 株得CropGuard`
})
