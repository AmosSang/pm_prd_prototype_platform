import { createRouter, createWebHistory } from 'vue-router'
import Home from '../views/Home.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: Home },
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

export default router
