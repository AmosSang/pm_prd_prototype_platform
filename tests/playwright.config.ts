import { defineConfig } from '@playwright/test'

const WEB_PORT = process.env.WEB_PORT || '8080'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  // 偶发失败复盘（2026-08-24）：多 worker 并发下，绑定项目类用例共享 DB 与
  // 列表页，负载高峰时 postMessage → Vue 处理链路偶发超 3s 断言窗口
  // （单用例复跑 6/6 稳过、iframe 内事件探针证明 click 层正常）。
  // retries=1 让时序敏感用例自动重试一次；CI 全绿标准不变。
  retries: 0,
  workers: 2,
  reporter: 'line',
  globalSetup: './global-setup.ts',
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    headless: true,
    storageState: './.auth/state.json',
  },
  // 环境由 make smoke 负责（先起服务再跑用例再清理），此处不内置 webServer
})
