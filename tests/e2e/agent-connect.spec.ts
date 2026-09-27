import { expect, test } from '@playwright/test'

/**
 * T12.2 Agent 接入页 E2E：
 * - 顶栏「Agent 接入」入口（任意登录用户可见）→ 页面打开
 * - 生成 Token → 一次性明文展示（ppp_ + 64 hex）→ 列表出现（有效）
 * - 撤销 → 状态变为「已撤销」，刷新后仍为已撤销（落库生效）
 * - MCP 地址与配置模板可见（可读性由人眼验收）
 *
 * 默认 storageState = e2e@test.local（普通用户即可，Token 属自助能力）。
 * Token 名用唯一后缀避免残留数据干扰；run-smoke.sh 统一清理 e2e- 前缀 Token。
 */

test.describe('T12.2 Agent 接入页', () => {
  test('生成 → 列表 → 撤销全链路', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('app-bar')).toBeVisible()
    await page.click('[data-testid="agent-connect"]')
    await expect(page.locator('h1')).toContainText('Agent 接入')

    // MCP 地址与配置模板可见
    await expect(page.getByTestId('agent-mcp-url')).toBeVisible()
    await expect(page.getByTestId('agent-config-template')).toContainText('mcpServers')
    await expect(page.getByTestId('agent-config-template')).toContainText('Authorization')

    // 生成 Token
    const name = `e2e-agent-${Date.now().toString(36)}`
    await page.click('[data-testid="agent-token-create-open"]')
    await page.fill('[data-testid="agent-token-name"]', name)
    await page.click('[data-testid="agent-token-create-submit"]')

    const plain = page.getByTestId('agent-token-plaintext')
    await expect(plain).toBeVisible({ timeout: 5_000 })
    const tokenText = (await plain.textContent())?.trim() || ''
    expect(tokenText).toMatch(/^ppp_[0-9a-f]{64}$/)

    // 粘贴即用配置内含同一 Token
    await expect(page.getByTestId('agent-token-config')).toContainText(tokenText)

    await page.click('[data-testid="agent-token-done"]')

    // 列表出现，状态「有效」
    let row = page.locator('tr', { hasText: name })
    await expect(row).toBeVisible({ timeout: 5_000 })
    await expect(row.getByText('有效')).toBeVisible()

    // 撤销 → 状态变化
    await row.getByTestId('agent-token-revoke').click()
    const confirmBtn = page.locator('.el-message-box__btns .el-button--primary')
    await expect(confirmBtn).toBeVisible({ timeout: 5_000 })
    await confirmBtn.click()
    await expect(page.getByText('已撤销', { exact: false }).first()).toBeVisible({ timeout: 5_000 })
    row = page.locator('tr', { hasText: name })
    await expect(row.getByText('已撤销')).toBeVisible()

    // 刷新后仍为已撤销（列表来自后端而非内存）
    await page.reload()
    row = page.locator('tr', { hasText: name })
    await expect(row.getByText('已撤销')).toBeVisible({ timeout: 5_000 })
    await expect(row.getByTestId('agent-token-revoke')).toHaveCount(0)
  })

  test('普通用户顶栏可见 Agent 接入入口', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('agent-connect')).toBeVisible()
    // 普通用户不可见用户管理（回归对照）
    await expect(page.getByTestId('user-manage')).toHaveCount(0)
  })
})
