import { expect, test } from '@playwright/test'
import { createProjectWithContent, type ProjectInfo } from './helpers'

/**
 * T3.2 反向联动 E2E。
 *
 * 验收点（任务卡）：文档「定位」→ 原型滚动闪烁；跨页目标先切页再定位。
 * 场景（T8.1 本地上传）：多页原型（login.html / home.html）+ 带页面地图
 * 的 PRD → 打开分屏（默认 login 页）→ 点文档段落「定位」→ 原型侧滚动+闪烁；
 * 点另一个页面的锚点「定位」→ iframe 自动切到 home.html 再滚动闪烁。
 */

const PROTO = {
  // 登录页：form 在 150vh 之下（验证滚动）
  'pages/login.html': `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: system-ui, sans-serif; margin: 0; }
  .filler { height: 150vh; }
</style></head>
<body>
  <main data-pa="page-login">
    <h2>登录页</h2>
    <div class="filler">填充高度（验证滚动定位）</div>
    <form data-pa="login-form">
      <input id="account" data-pa="login-account" placeholder="账号">
    </form>
  </main>
</body></html>`,
  // 首页：独立页面文件（验证跨页切换）
  'pages/home.html': `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: system-ui, sans-serif; margin: 0; }
  .filler { height: 150vh; }
</style></head>
<body>
  <main data-pa="page-home">
    <h2>首页</h2>
    <div class="filler">填充高度（验证滚动定位）</div>
    <section data-pa="home-stats">
      <p>统计区</p>
    </section>
  </main>
</body></html>`,
}

const PRD = {
  name: '需求.md',
  content: `# 反向联动测试 PRD

## 4 页面地图

| 页面 | 原型文件 | 页面锚点 |
|------|---------|---------|
| 登录页 | prototype/pages/login.html | page-login |
| 首页 | prototype/pages/home.html | page-home |

## 5 功能需求

### 5.1 登录页 <!-- pa: page-login -->

#### 5.1.1 登录表单 <!-- pa: login-form -->

账号输入 <!-- pa: login-account -->：支持手机号。

### 5.2 首页 <!-- pa: page-home -->

#### 5.2.1 统计区 <!-- pa: home-stats -->

统计卡片。
`,
}

test.describe('T3.2 反向联动', () => {
  let proj: ProjectInfo

  test.beforeEach(async ({ page, request }) => {
    proj = await createProjectWithContent(request, '反向联动E2E', {
      protoFiles: PROTO,
      prdFile: PRD,
    })

    // 按 slug 精确定位（并发 worker 下同名卡片多张；slug 数据库唯一）
    await page.goto('/')
    await page
      .locator('.card')
      .filter({ hasText: proj.project_id })
      .getByTestId('open-project')
      .click()
    await expect(page).toHaveURL(new RegExp(`/project/${proj.project_id}`))
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })
    // 右侧 PRD 渲染完成（5.1 是 h3：### 5.1 登录页）
    await expect(
      page.getByTestId('prd-content').locator('h3[data-pa="page-login"]'),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('页面地图解析：overview 返回 map 条目', async ({ page }) => {
    // 直接断言页面地图数据（API 层，按本用例项目的 slug 过滤）
    const map = await page.evaluate(async (slug) => {
      const res = await fetch('/api/projects', { credentials: 'include' })
      const { data } = await res.json()
      const hit = data.find((p: { project_id: string }) => p.project_id === slug)
      const ov = await fetch(`/api/projects/${hit.id}/overview`, { credentials: 'include' })
      const body = await ov.json()
      return body.data.page_map
    }, proj.project_id)
    expect(map).toEqual(
      expect.arrayContaining([
        { name: '登录页', proto: 'prototype/pages/login.html', anchor: 'page-login' },
        { name: '首页', proto: 'prototype/pages/home.html', anchor: 'page-home' },
      ]),
    )
  })

  test('同页反向定位：点文档「定位」→ 原型滚动到锚点并闪烁', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
    // 默认入口按文件名排序是 home.html（home < login）——先切到 login
    await page.getByTestId('proto-select').click()
    await page.getByRole('option', { name: 'prototype/pages/login.html' }).click()
    await expect(protoFrame.locator('[data-pa="login-form"]')).toBeVisible({ timeout: 10_000 })
    const form = protoFrame.locator('[data-pa="login-form"]')

    // 点文档的「登录表单」段落顶部的「定位」按钮（坐标：段落顶部 28px 内）
    const para = page.getByTestId('prd-content').locator('h4[data-pa="login-form"]')
    await para.scrollIntoViewIfNeeded()
    await para.hover()
    const box = await para.boundingBox()
    expect(box).not.toBeNull()
    // 点段落顶部（定位按钮区域）
    await page.mouse.click(box!.x + 20, box!.y + 8)

    // 原型侧：login-form 滚动进视口 + 闪烁 class 出现
    await expect(form).toBeInViewport({ timeout: 5_000 })
    await expect(form).toHaveClass(/pp-anchor-flash/, { timeout: 3_000 })
  })

  test('跨页反向定位：点首页锚点「定位」→ iframe 切页 + 滚动闪烁', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')

    // 当前在 home.html（默认入口）；点文档「统计区」段落定位（home.html 内锚点）
    // ——场景设计：先切到 login 页，再点 home 的锚点，验证自动切回
    await page.getByTestId('proto-select').click()
    await page.getByRole('option', { name: 'prototype/pages/login.html' }).click()
    await expect(protoFrame.locator('[data-pa="login-form"]')).toBeVisible({ timeout: 10_000 })

    const para = page.getByTestId('prd-content').locator('h4[data-pa="home-stats"]')
    await para.scrollIntoViewIfNeeded()
    await para.hover()
    const box = await para.boundingBox()
    expect(box).not.toBeNull()
    await page.mouse.click(box!.x + 20, box!.y + 8)

    // iframe 自动切回 home.html（login 的 form 消失，home-stats 出现）
    await expect(protoFrame.locator('[data-pa="home-stats"]')).toBeVisible({ timeout: 10_000 })
    // 跨页后闪烁 class 出现（READY 后补发 HIGHLIGHT_ANCHOR）
    await expect(protoFrame.locator('[data-pa="home-stats"]')).toHaveClass(/pp-anchor-flash/, {
      timeout: 5_000,
    })
    // 滚动到位
    await expect(protoFrame.locator('[data-pa="home-stats"]')).toBeInViewport({ timeout: 3_000 })
  })

  test('正向联动：点原型锚点 icon → 右侧文档定位（当前文档命中）', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
    // 切到 login 页（默认入口是 home.html）
    await page.getByTestId('proto-select').click()
    await page.getByRole('option', { name: 'prototype/pages/login.html' }).click()
    await protoFrame.locator('[data-pa="login-account"]').scrollIntoViewIfNeeded()
    await protoFrame.locator('[data-pa="login-account"]').hover()
    const icon = protoFrame.locator('.pp-anchor-icon')
    await expect(icon).toBeVisible({ timeout: 5_000 })
    await icon.click()
    // 右侧：账号输入段落（markdown 段落内行尾注释 → p data-pa）高亮
    const target = page.getByTestId('prd-content').locator('p[data-pa="login-account"]')
    await expect(target).toHaveClass(/anchor-highlight/, { timeout: 3_000 })
  })
})
