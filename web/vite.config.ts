import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 8080,
    proxy: {
      // 后端 API 代理：开发期前端与 API 同源，生产由 Nginx /api/ 转发。
      // T8.2：上传 body 不额外设限——node http/Vite 代理对流式透传无体积上限，
      // 实际上限由后端 MAX_CONTENT_LENGTH（110MB）兜底，与生产 Nginx
      // client_max_body_size 口径一致
      '/api': {
        target: 'http://localhost:8081',
        changeOrigin: true,
      },
    },
  },
})
