import { expect, test } from '@playwright/test'
import { createProjectWithContent, type ProjectInfo } from './helpers'

/**
 * T10.2 目录大纲 E2E（PRD §5.4.3）：
 *
 * 1. 渲染后从 DOM 提取 h1–h4 生成大纲（含中文 slug id）
 * 2. 点击大纲项 → 文档滚动到对应标题
 * 3. scrollspy：滚动后高亮跟随
 * 4. 展开收起规则：split 默认展开、可手动收起；三栏（评论抽屉打开）强制收起
 * 5. 无标题文档 → 大纲隐藏
 */
const LONG_PRD = `# 产品需求文档

## 1 背景与目标

背景内容段落。

### 1.1 现状分析

现状内容段落。

#### 1.1.1 竞品对比

竞品内容段落。

## 2 功能需求

### 2.1 登录模块

登录需求段落。

## 3 非功能需求

性能要求段落。

${'填充段落。\\n\\n'.repeat(60)}
## 9 附录

附录内容。
`

test('T10.2 大纲全链路：生成 → 点击跳转 → scrollspy → 收起规则 → 无标题隐藏', async ({
  page,
  request,
}) => {
  // 建两个项目：一个有标题（大纲可见）、一个无标题（大纲隐藏）
  const proj: ProjectInfo = await createProjectWithContent(request, '大纲E2E项目', {
    protoFiles: {
      'index.html': '<html><body><main data-pa="page-login">登录页</main></body></html>',
    },
    prdFile: { name: '需求.md', content: LONG_PRD },
  })
  const projBare: ProjectInfo = await createProjectWithContent(request, '大纲无标题E2E', {
    protoFiles: { 'index.html': '<html><body>无标题文档的原型</body></html>' },
    prdFile: { name: '笔记.md', content: '这是一段没有标题的纯文本内容。\n\n再来一段。\n' },
  })

  await page.goto(`/project/${proj.project_id}`)
  await expect(page.getByTestId('prd-content')).toBeVisible({ timeout: 15_000 })

  // ── 1. 大纲生成：h1–h4 全部提取、中文 slug id 可点击 ──
  const toc = page.getByTestId('toc-sidebar')
  await expect(toc).toBeVisible()
  await expect(toc.getByTestId('toc-item-产品需求文档')).toBeVisible()
  await expect(toc.getByTestId('toc-item-1-背景与目标')).toBeVisible()
  await expect(toc.getByTestId('toc-item-1.1.1-竞品对比')).toBeVisible()
  await expect(toc.getByTestId('toc-item-9-附录')).toBeVisible()

  // ── 2. 点击跳转：点「9 附录」→ 滚动容器滚到该标题附近 ──
  const scrollEl = page.locator('.prd-scroll')
  const beforeTop = await scrollEl.evaluate((el) => el.scrollTop)
  await toc.getByTestId('toc-item-9-附录').click()
  // 平滑滚动进行中：等 scrollTop 稳定（两次采样一致且超过阈值才算到位）
  await expect
    .poll(
      async () => {
        const a = await scrollEl.evaluate((el) => el.scrollTop)
        await page.waitForTimeout(150)
        const b = await scrollEl.evaluate((el) => el.scrollTop)
        return Math.abs(a - b) < 2 ? b : -1 // 未稳定返回 -1 继续轮询
      },
      { timeout: 5_000 },
    )
    .toBeGreaterThan(beforeTop + 100)
  const afterTop = await scrollEl.evaluate((el) => el.scrollTop)
  expect(afterTop).toBeGreaterThan(beforeTop + 100)

  // ── 3. scrollspy：跳到「9 附录」后该项获得 active ──
  await expect(toc.getByTestId('toc-item-9-附录')).toHaveClass(/active/, { timeout: 3_000 })

  // ── 4. 展开收起规则 ──
  // 4a. split 模式默认展开 → 手动收起 → 列表隐藏但侧栏按钮仍在
  await page.getByTestId('toc-toggle').click()
  await expect(page.getByTestId('toc-list')).toBeHidden()
  // 4b. 再展开
  await page.getByTestId('toc-toggle').click()
  await expect(page.getByTestId('toc-list')).toBeVisible()

  // 4c. 三栏（评论抽屉打开）强制收起：先重新展开再开抽屉
  await page.locator('.v-head').getByTestId('drawer-toggle').click()
  await expect(page.getByTestId('toc-list')).toBeHidden({ timeout: 3_000 })
  // 关抽屉 → 恢复（回到 split 且手动偏好是展开）
  await page.locator('.v-head').getByTestId('drawer-toggle').click()
  await expect(page.getByTestId('toc-list')).toBeVisible({ timeout: 3_000 })

  // ── 5. 无标题文档 → 大纲隐藏 ──
  await page.goto(`/project/${projBare.project_id}`)
  await expect(page.getByTestId('prd-content')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('toc-sidebar')).toHaveCount(0)
})
