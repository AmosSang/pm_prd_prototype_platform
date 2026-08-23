import { chromium, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

/**
 * E2E 全局登录：所有用例默认带登录态（storageState）。
 * 登录走真实 UI 流程，验证码从 SMTP_FAKE mailbox 文件读取。
 *
 * 注意：Playwright 会把本文件编译到独立目录运行，import.meta.url
 * 指向编译产物——写文件一律用 process.cwd()（playwright test 的
 * 执行目录固定为 config 所在目录，即 tests/）。
 */
const E2E_EMAIL = 'e2e@test.local'
const MAILBOX = '/tmp/ppp-fake-mailbox'
const STATE_FILE = path.join(process.cwd(), '.auth', 'state.json')

function readCode(): string {
  const text = fs.readFileSync(path.join(MAILBOX, E2E_EMAIL), 'utf-8')
  const m = /\b(\d{6})\b/.exec(text)
  if (!m) throw new Error(`mailbox 里没找到验证码：${text}`)
  return m[1]
}

export default async function globalSetup() {
  const browser = await chromium.launch()
  const baseURL = `http://localhost:${process.env.WEB_PORT || '8080'}`
  const page = await browser.newPage()

  await page.goto(`${baseURL}/login`)
  await page.fill('[data-testid="login-email"]', E2E_EMAIL)
  await page.click('[data-testid="login-send"]')
  await expect(page.getByText('验证码已发送')).toBeVisible({ timeout: 10_000 })

  const code = readCode()
  await page.fill('[data-testid="login-code"]', code)
  await page.click('[data-testid="login-submit"]')
  await expect(page.getByTestId('current-user')).toBeVisible({ timeout: 10_000 })

  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true })
  await page.context().storageState({ path: STATE_FILE })
  console.log(`[global-setup] 登录态已写入 ${STATE_FILE}`)
  await browser.close()
}
