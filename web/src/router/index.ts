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
  ],
})

export default router
