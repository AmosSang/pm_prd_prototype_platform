import { expect, test } from '@playwright/test'

test('首页：项目列表 + 后端连接状态', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveTitle('产品方案展示平台')
  await expect(page.getByRole('heading', { name: '项目列表' })).toBeVisible()

  // 后端连接状态最终应显示「正常」（Home.vue 的 /api/health 探测）
  await expect(page.locator('.ok')).toHaveText('正常', { timeout: 10_000 })
})
