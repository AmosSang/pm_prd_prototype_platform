import { expect, test } from '@playwright/test'

/**
 * T2.3 / T8.1 项目创建 E2E。
 *
 * 验收点（T8.1 任务卡）：新建项目成功 → 目录骨架 + DB 记录 →
 * 列表出现卡片（创建者标记）；空名/超长名被拦截。
 * T8.1 去 Git 本地化：表单只填名称，无仓库/token/分支字段。
 */

test.describe('T8.1 项目创建', () => {
  test('新建项目成功 → 列表出现卡片（创建者标记）', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('project-card-demo')).toBeVisible()

    await page.getByTestId('new-project').click()
    await page.getByTestId('form-name').fill('E2E绑定项目')
    await page.getByTestId('form-submit').click()

    // 成功提示 + 新卡片出现（project_id 随机，用 name 定位卡片）
    await expect(page.getByText('创建成功')).toBeVisible({ timeout: 15_000 })
    const card = page.locator('.card', { hasText: 'E2E绑定项目' })
    await expect(card).toBeVisible()
    // slug 规则：中文名转 kebab 后只剩 'e2e' 前缀 + 随机后缀
    await expect(card.locator('.meta')).toContainText(/^e2e-[a-z0-9]+/i)
    // 创建者标记（当前登录用户 E2E测试员）
    await expect(card.locator('.meta')).toContainText('创建者 E2E测试员')
  })

  test('空名提交被拦截（按钮禁用）', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('new-project').click()
    await expect(page.getByTestId('form-name')).toHaveValue('')
    await expect(page.getByTestId('form-submit')).toBeDisabled()
  })
})
