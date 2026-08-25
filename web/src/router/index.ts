import { createRouter, createWebHistory } from 'vue-router'
import { authChecked, currentUser } from '../auth'
import Home from '../views/Home.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: Home },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/Login.vue'),
    },
    {
      path: '/demo/bridge',
      name: 'bridge-demo',
      component: () => import('../views/BridgeDemo.vue'),
    },
    {
      path: '/demo/shot',
      name: 'shot-demo',
      component: () => import('../views/ShotDemo.vue'),
    },
    {
      // T2.4：分屏查看器（slug = project_id，DB 主键不进 URL 防猜测）
      path: '/project/:slug',
      name: 'viewer',
      component: () => import('../views/Viewer.vue'),
    },
    {
      // T2.1 用户管理（仅超管）
      path: '/users',
      name: 'users',
      component: () => import('../views/UserManage.vue'),
      meta: { requiresAdmin: true },
    },
  ],
})

// T2.2：全局守卫——未登录一律去 /login（带 back 来源）；
// requiresAdmin 路由：非超管重定向回首页
router.beforeEach((to) => {
  if (to.name === 'login') return true
  if (!currentUser.value) {
    return { name: 'login', query: { back: to.fullPath } }
  }
  if (to.meta.requiresAdmin && !currentUser.value.is_admin) {
    return { name: 'home' }
  }
  return true
})

export default router
