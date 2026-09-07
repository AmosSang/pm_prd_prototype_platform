import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './base.css'
// T10.1 渲染基线：github-markdown-css（light 固定主题）+ highlight.js github 主题
import 'github-markdown-css/github-markdown-light.css'
import 'highlight.js/styles/github.css'
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
