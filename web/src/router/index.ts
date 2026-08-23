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
  ],
})

// T2.2：全局守卫——未登录一律去 /login（带 back 来源）
router.beforeEach((to) => {
  if (to.name === 'login') return true
  if (!currentUser.value) {
    return { name: 'login', query: { back: to.fullPath } }
  }
  return true
})

export default router
