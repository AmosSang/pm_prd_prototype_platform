import { defineConfig } from '@playwright/test'

const WEB_PORT = process.env.WEB_PORT || '8080'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 0,
  reporter: 'line',
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    headless: true,
  },
  // 环境由 make smoke 负责（先起服务再跑用例再清理），此处不内置 webServer
})
