import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'

/**
 * T2.1 用户管理 E2E：
 * - 超管登录 → 顶栏「用户管理」入口 → 建用户/改名/停用/启用
 * - 停用账号登录 → toast「账号已停用」（不发验证码）
 * - 普通用户看不到「用户管理」入口
 * 前置：run-smoke.sh 启动后端时 ADMIN_EMAIL=e2e-admin@test.local 种子超管。
 */

const ADMIN_EMAIL = 'e2e-admin@test.local'
const DISABLED_EMAIL = 'um-disabled@test.local'

function readCodeFromMailbox(email: string): string {
  const text = fs.readFileSync(`/tmp/ppp-fake-mailbox/${email}`, 'utf-8')
  const m = /\b(\d{6})\b/.exec(text)
  if (!m) throw new Error(`mailbox 里没找到 ${email} 的验证码`)
  return m[1]
}

async function loginAs(page: Page, email: string) {
  await page.goto('/login')
  await page.fill('[data-testid="login-email"]', email)
  await page.click('[data-testid="login-send"]')
  await expect(page.getByText('验证码已发送')).toBeVisible({ timeout: 10_000 })
  const code = readCodeFromMailbox(email)
  await page.fill('[data-testid="login-code"]', code)
  await page.click('[data-testid="login-submit"]')
  await expect(page.getByTestId('current-user')).toBeVisible({ timeout: 10_000 })
}

async function confirmDialog(page: Page) {
  const btn = page.locator('.el-message-box__btns .el-button--primary')
  await expect(btn).toBeVisible({ timeout: 5_000 })
  await btn.click()
}

test.describe('T2.1 用户管理（超管）', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('超管创建/改名/停用/启用 + 预置停用用户', async ({ page }) => {
    await loginAs(page, ADMIN_EMAIL)
    await expect(page.getByTestId('user-manage')).toBeVisible()
    await page.click('[data-testid="user-manage"]')
    await expect(page.getByTestId('user-table')).toBeVisible()

    const suffix = Date.now()
    const createEmail = `um-create-${suffix}@test.local`

    await page.click('[data-testid="user-create-open"]')
    await page.fill('[data-testid="user-create-email"]', createEmail)
    await page.fill('[data-testid="user-create-name"]', 'E2E创建用户')
    await page.click('[data-testid="user-create-submit"]')
    await expect(page.getByText('用户「E2E创建用户」创建成功')).toBeVisible({ timeout: 5_000 })
    let row = page.locator('tr', { hasText: createEmail })
    await expect(row).toBeVisible()

    await row.getByTestId('user-rename').click()
    await page.fill('[data-testid="user-rename-name"]', 'E2E改名后')
    await page.click('[data-testid="user-rename-submit"]')
    await expect(page.getByText('已更新「E2E改名后」')).toBeVisible({ timeout: 5_000 })
    row = page.locator('tr', { hasText: createEmail })
    await expect(row.getByText('E2E改名后')).toBeVisible()

    await row.getByTestId('user-disable').click()
    await confirmDialog(page)
    await expect(page.getByText('已停用')).toBeVisible({ timeout: 5_000 })
    row = page.locator('tr', { hasText: createEmail })
    await expect(row.getByText('停用')).toBeVisible()

    await row.getByTestId('user-enable').click()
    await confirmDialog(page)
    await expect(page.getByText('已启用')).toBeVisible({ timeout: 5_000 })
    row = page.locator('tr', { hasText: createEmail })
    await expect(row.getByText('启用')).toBeVisible()

    // 预置停用测试用户（下一用例用；已存在则查 ID 再停用）
    const createRes = await page.request.post('/api/users', {
      data: { email: DISABLED_EMAIL, name: '待停用用户' },
    })
    let id = -1
    if (createRes.ok()) {
      id = (await createRes.json()).data.id
    } else {
      const list = await (await page.request.get('/api/users')).json()
      id = list.data.find((u: { email: string }) => u.email === DISABLED_EMAIL).id
    }
    await page.request.patch(`/api/users/${id}/status`, { data: { disabled: true } })
  })

  test('停用账号登录 → toast 账号已停用', async ({ page }) => {
    await page.goto('/login')
    await page.fill('[data-testid="login-email"]', DISABLED_EMAIL)
    await page.click('[data-testid="login-send"]')
    await expect(page.getByText('账号已停用，请联系管理员')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('验证码已发送')).toHaveCount(0)
  })
})

test.describe('T2.1 用户管理入口可见性', () => {
  test('普通用户看不到用户管理入口', async ({ page }) => {
    // 默认 storageState = e2e@test.local（普通用户）
    await page.goto('/')
    await expect(page.getByTestId('app-bar')).toBeVisible()
    await expect(page.getByTestId('user-manage')).toHaveCount(0)
  })
})
