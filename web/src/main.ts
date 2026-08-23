import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import { initAuth } from './auth'
import router from './router'

async function boot() {
  // 先拉登录态再挂路由（守卫依赖 currentUser）
  await initAuth()
  const app = createApp(App)
  app.use(ElementPlus)
  app.use(router)
  app.mount('#app')
}

boot()
