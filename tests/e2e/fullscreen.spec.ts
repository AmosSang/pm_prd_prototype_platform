import { expect, test } from '@playwright/test'
import { createProjectWithContent } from './helpers'

/**
 * T11.1 整屏布局状态机 E2E（PRD §5.3.2）：
 *
 * 三态：split / proto-full / prd-full。
 * - pane-head 放大（⤢）/还原（⤡）/ Esc 三路径切换
 * - 进入整屏记忆 split 比例，还原恢复
 * - 整屏 × 评论抽屉：整屏栏 + 抽屉两栏让宽，关闭回整屏
 * - 大纲联动三规则：split 默认展开可收起 / 三栏强制收起 / prd-full 强制展开
 * - 刷新回默认 split
 */

const PRD = `# 整屏测试 PRD

## 1 概述

概述内容。

## 2 详情

详情内容。

${'填充段落。\\n\\n'.repeat(40)}
`

test('T11.1 三态切换 + 比例记忆 + Esc + 抽屉让宽 + 大纲全态 + 刷新回 split', async ({
  page,
  request,
}) => {
  const proj = await createProjectWithContent(request, '整屏E2E项目', {
    protoFiles: {
      'index.html': '<html><body><main data-pa="page-login">登录页</main></body></html>',
    },
    prdFile: { name: '需求.md', content: PRD },
  })

  await page.goto(`/project/${proj.project_id}`)
  await expect(page.getByTestId('prd-content')).toBeVisible({ timeout: 15_000 })

  // ── 1. split 默认态：两栏都在、大纲默认展开 ──
  await expect(page.locator('.pane.proto')).toBeVisible()
  await expect(page.locator('.pane.prd')).toBeVisible()
  await expect(page.getByTestId('divider')).toBeVisible()
  await expect(page.getByTestId('toc-list')).toBeVisible()

  // ── 2. 拖动分割条改比例 → prd-full 进入 → 还原恢复比例 ──
  const divider = page.getByTestId('divider')
  const box = await divider.boundingBox()
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
  await page.mouse.down()
  await page.mouse.move(box!.x + 200, box!.y + box!.height / 2, { steps: 5 })
  await page.mouse.up()
  const savedPct = await page.locator('.pane.proto').evaluate((el) => parseFloat(el.style.width))
  expect(savedPct).toBeGreaterThan(50)

  await page.getByTestId('prd-fs-enter').click()
  await expect(page.locator('.pane.prd')).toHaveAttribute('style', /width: 100%/)
  await expect(page.locator('.pane.proto')).toHaveCount(0) // 原型栏收起
  await expect(page.getByTestId('divider')).toHaveCount(0)

  // prd-full 大纲强制展开（即使 split 时手动收起过——这里没收起过，直接断言在）
  await expect(page.getByTestId('toc-list')).toBeVisible()
  // prd-full 下无 toc-toggle 按钮（强制展开不可收）
  await expect(page.getByTestId('toc-toggle')).toHaveCount(0)

  // ── 3. 还原按钮：恢复记忆比例 ──
  await page.getByTestId('prd-fs-exit').click()
  await expect(page.locator('.pane.proto')).toBeVisible()
  const restoredPct = await page.locator('.pane.proto').evaluate((el) => parseFloat(el.style.width))
  expect(Math.abs(restoredPct - savedPct)).toBeLessThan(1)

  // ── 4. Esc 退出整屏：proto-full → split ──
  await page.getByTestId('proto-fs-enter').click()
  await expect(page.locator('.pane.prd')).toHaveCount(0)
  await expect(page.locator('.pane.proto')).toHaveAttribute('style', /width: 100%/)
  await page.keyboard.press('Escape')
  await expect(page.locator('.pane.prd')).toBeVisible()
  await expect(page.locator('.pane.proto')).not.toHaveAttribute('style', /100%/)

  // ── 5. 整屏 × 评论抽屉：整屏栏 + 抽屉两栏让宽；关闭回整屏 ──
  await page.getByTestId('prd-fs-enter').click()
  await page.locator('.v-head').getByTestId('drawer-toggle').click()
  const drawerVisible = page.locator('.pane.comments')
  await expect(drawerVisible).toBeVisible()
  await expect(page.locator('.pane.prd')).toBeVisible() // 整屏栏仍在
  const prdFullW = await page.locator('.pane.prd').evaluate((el) => parseFloat(el.style.width))
  expect(prdFullW).toBeLessThan(100) // 让宽
  // prd-full + 抽屉 = 三栏同存？PRD 规则：三栏同存指 split+抽屉；prd-full+抽屉是两栏（原型已收起）
  await page.locator('.v-head').getByTestId('drawer-toggle').click() // 关抽屉
  await expect(page.locator('.pane.comments')).toHaveCount(0)
  await expect(page.locator('.pane.prd')).toHaveAttribute('style', /width: 100%/) // 回整屏

  // ── 6. 刷新回默认 split（比例不持久化，记忆比例也不跨刷新）──
  await page.reload()
  await expect(page.getByTestId('prd-content')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.pane.proto')).toBeVisible()
  await expect(page.locator('.pane.prd')).toBeVisible()
  const w = await page.locator('.pane.proto').evaluate((el) => parseFloat(el.style.width))
  expect(w).toBe(50) // 默认比例
})
