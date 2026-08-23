import { expect, test } from '@playwright/test'
import fs from 'node:fs'

/**
 * T2.2 登录流 E2E。
 * 其余用例经 storageState 默认带登录态；本文件用例显式清空 storageState
 * 测「未登录 → 登录」全流程。
 * 邮箱隔离：globalSetup 用 e2e@test.local（60s 频控内只发一次），
 * 本文件每个用例用独立邮箱，互不干扰。
 */

test.use({ storageState: { cookies: [], origins: [] } })

function readCodeFromMailbox(email: string): string {
  const text = fs.readFileSync(`/tmp/ppp-fake-mailbox/${email}`, 'utf-8')
  const m = /\b(\d{6})\b/.exec(text)
  if (!m) throw new Error(`mailbox 里没找到 ${email} 的验证码`)
  return m[1]
}

test.describe('T2.2 登录与拦截', () => {
  test('未登录访问首页 → 跳转登录页', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
    await expect(page.getByTestId('login-card')).toBeVisible()
  })

  test('未登录调 API → 401', async ({ request }) => {
    const res = await request.get('/api/auth/me')
    expect(res.status()).toBe(401)
  })

  test('完整登录流：发码 → 输码 → 登录成功 → 回跳来源页', async ({ page }) => {
    const email = 'e2e-flow@test.local'
    // 带 back 参数访问受保护页 → 登录后应回到该页
    await page.goto('/demo/shot?scene=login')
    await expect(page).toHaveURL(/\/login\?back=/)

    await page.fill('[data-testid="login-email"]', email)
    await page.click('[data-testid="login-send"]')
    await expect(page.getByText('验证码已发送')).toBeVisible({ timeout: 10_000 })

    const code = readCodeFromMailbox(email)
    await page.fill('[data-testid="login-code"]', code)
    await page.click('[data-testid="login-submit"]')

    await expect(page.getByTestId('current-user')).toBeVisible({ timeout: 10_000 })
    await expect(page).toHaveURL(/\/demo\/shot/)
  })

  test('60s 频控 UI：发送后按钮进入倒计时', async ({ page }) => {
    const email = 'e2e-rate@test.local'
    await page.goto('/login')
    await page.fill('[data-testid="login-email"]', email)
    await page.click('[data-testid="login-send"]')
    await expect(page.getByText('s 后可重发')).toBeVisible({ timeout: 8_000 })
  })
})
