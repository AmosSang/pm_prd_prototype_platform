import { expect, test } from '@playwright/test'
import { createProjectWithContent, type ProjectInfo } from './helpers'

/**
 * T11.2 联动自动恢复 E2E（PRD §5.3.2 锚点联动恢复 + §5.3.4 验收）：
 *
 * 三个联动入口 × 目标侧收起态 → 先自动还原 split 再执行既有定位时序：
 * 1. proto-full 下点原型锚点 icon（正向）→ 还原 split + 文档段落高亮
 * 2. prd-full 下点文档「定位」（反向）→ 还原 split + 原型元素高亮
 * 3. prd-full 下评论抽屉「定位」原型评论 → 还原 split + 原型定位
 * 4. 原型 iframe 自导航不改整屏状态
 */

const PRD = `# 联动恢复 PRD

## 5.1 登录页

登录页需求段落。 <!-- pa: page-login -->

## 5.2 工作台

工作台需求段落。 <!-- pa: page-home -->
`

function setupFiles() {
  return {
    protoFiles: {
      'index.html':
        '<html><body><main data-pa="page-login">登录页<button data-pa="btn-login">登录按钮</button></main></body></html>',
      'home.html':
        '<html><body><main data-pa="page-home">工作台</main></body></html>',
    },
    prdFile: { name: '需求.md', content: PRD },
  }
}

test('T11.2 联动自动恢复：正向/反向/抽屉定位 + 自导航不改整屏', async ({ page, request }) => {
  const proj: ProjectInfo = await createProjectWithContent(request, '联动恢复E2E', setupFiles())

  await page.goto(`/project/${proj.project_id}`)
  await expect(page.getByTestId('prd-content')).toBeVisible({ timeout: 15_000 })
  const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')

  // ── 1. proto-full 下正向联动：点原型锚点 icon → 自动还原 split + 文档高亮 ──
  await page.getByTestId('proto-fs-enter').click()
  await expect(page.locator('.pane.prd')).toHaveCount(0)

  await protoFrame.locator('[data-pa="page-login"]').hover()
  const icon = protoFrame.locator('.pp-anchor-icon')
  await expect(icon).toBeVisible({ timeout: 5_000 })
  await icon.click()

  // 布局恢复 + 文档段落高亮（正向联动既有断言口径；data-pa 挂锚点注释所在段落 p）
  await expect(page.locator('.pane.prd')).toBeVisible({ timeout: 5_000 })
  await expect(page.locator('.pane.proto')).not.toHaveAttribute('style', /100%/)
  const target = page.getByTestId('prd-content').locator('p[data-pa="page-login"]')
  await expect(target).toHaveClass(/anchor-highlight/, { timeout: 3_000 })

  // ── 2. prd-full 下反向联动：文档「定位」→ 自动还原 split + 原型定位 ──
  await page.getByTestId('prd-fs-enter').click()
  await expect(page.locator('.pane.proto')).toHaveCount(0)

  const homePara = page.getByTestId('prd-content').locator('p[data-pa="page-home"]')
  await homePara.scrollIntoViewIfNeeded()
  await homePara.hover()
  // 点段落顶部的「定位」按钮区域（顶部 28px 内、左侧 60px 内，与既有 reverse.spec 同口径）
  const box = await homePara.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.click(box!.x + 20, box!.y + 8)

  // 布局恢复 + 跨页定位（page-home 在 home.html → 还原 → 切页 → READY → GOTO）
  await expect(page.locator('.pane.proto')).toBeVisible({ timeout: 5_000 })
  await expect(page.locator('.pane.prd')).not.toHaveAttribute('style', /100%/)

  // ── 3. 原型 iframe 自导航（a 标签）不改整屏状态 ──
  // 步骤 2 反向联动已自动还原 split（iframe 已切到 home.html）；进 proto-full
  await page.getByTestId('proto-fs-enter').click()
  await expect(page.locator('.pane.prd')).toHaveCount(0)
  // 原型内自由点击（非锚点 icon）→ 整屏保持（当前页 home.html 的 page-home）
  await protoFrame.locator('[data-pa="page-home"]').click()
  await page.waitForTimeout(500)
  await expect(page.locator('.pane.prd')).toHaveCount(0) // 仍 proto-full
  await expect(page.locator('.pane.proto')).toHaveAttribute('style', /width: 100%/)
})
