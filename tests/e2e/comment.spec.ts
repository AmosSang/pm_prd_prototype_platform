import { expect, test, type Page } from '@playwright/test'
import { execSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/**
 * T4.1 评论模式元素采集 E2E。
 *
 * 验收点（任务卡）：开启评论模式点元素，采到的 payload 断言字段齐全。
 * 场景：开启评论模式 → hover 蓝色高亮 → click 捕获拦截 → ELEMENT_SELECTED
 * payload 面板字段断言（target_type / prototype_page / anchor_id /
 * nearest_anchor_id / css_path / outer_html / text_excerpt / interaction_state）。
 * 另含：非锚点与页面根目标、点击拦截（原型行为不触发）、modal_open 检测、
 * 开关关闭、切页后模式保持（READY 重发）、ROUTE_CHANGE 上报。
 */

const REPO_DIR = path.join(os.tmpdir(), 'ppp-e2e-comment-repo')

/** 造带锚点/表单/弹窗的原型与 PRD（每次运行强制重建，防上轮残留）。 */
function ensureCommentRepo() {
  fs.rmSync(REPO_DIR, { recursive: true, force: true })
  const work = path.join(os.tmpdir(), 'ppp-e2e-comment-work')
  fs.rmSync(work, { recursive: true, force: true })
  fs.mkdirSync(path.join(work, 'prototype', 'pages'), { recursive: true })
  fs.mkdirSync(path.join(work, 'prd'), { recursive: true })

  // 首页：锚点 + 表单提交行为标记（拦截验证）+ body padding（页面评论
  // 点击落点）+ 高 spacer 与第二锚点区块（scroll_y 采集验证）
  fs.writeFileSync(
    path.join(work, 'prototype', 'index.html'),
    `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 24px; }
  section { padding: 12px; border-bottom: 1px solid #eee; }
  input { display: block; margin: 6px 0; }
  .btn { padding: 6px 16px; border: 1px solid #d9d9d9; background: #fff; cursor: pointer; }
</style></head>
<body>
  <section data-pa="page-login">
    <h2>登录页</h2>
    <form data-pa="login-form" id="login-form">
      <input id="account" data-pa="login-account" placeholder="账号">
      <input id="captcha" data-pa="login-captcha" placeholder="验证码">
      <button class="btn" id="submit-btn" type="submit">登录</button>
    </form>
  </section>
  <div style="height:120vh"></div>
  <section data-pa="page-extra"><h2>补充区块</h2></section>
  <script>
    // 提交行为标记：评论模式拦截验证用（拦截生效时点击后不应新增）
    document.getElementById('login-form').addEventListener('submit', function (e) {
      e.preventDefault()
      var m = document.createElement('div')
      m.id = 'submit-marker'
      m.textContent = 'submitted'
      document.body.appendChild(m)
    })
  </script>
</body></html>`,
  )

  // 弹窗页：modal_open 检测 + 拦截（评论模式点「再想想」不应关弹窗）
  fs.writeFileSync(
    path.join(work, 'prototype', 'pages', 'modal.html'),
    `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 24px; }
  .row { background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 16px; margin-bottom: 12px; }
  .btn { padding: 6px 16px; border: 1px solid #d9d9d9; border-radius: 4px; background: #fff; cursor: pointer; }
  .modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: none; align-items: center; justify-content: center; }
  .modal-mask.open { display: flex; }
  .modal { background: #fff; border-radius: 8px; width: 320px; padding: 24px; }
</style></head>
<body>
  <main data-pa="page-settings">
    <h2>账号设置</h2>
    <div class="row" data-pa="settings-profile">
      <button class="btn" data-pa="settings-profile-edit">编辑资料</button>
    </div>
    <div class="row" data-pa="settings-deregister">
      <button class="btn" id="open-modal" data-pa="settings-deregister-btn">注销账号</button>
    </div>
  </main>
  <div class="modal-mask" id="modal-mask" data-pa="deregister-modal">
    <div class="modal">
      <h3>确认注销？</h3>
      <button class="btn" id="cancel" data-pa="deregister-cancel">再想想</button>
    </div>
  </div>
  <script>
    document.getElementById('open-modal').addEventListener('click', function () {
      document.getElementById('modal-mask').classList.add('open')
    })
    document.getElementById('cancel').addEventListener('click', function () {
      document.getElementById('modal-mask').classList.remove('open')
    })
  </script>
</body></html>`,
  )

  fs.writeFileSync(
    path.join(work, 'prd', '需求.md'),
    `# 评论采集 E2E PRD

## 5.1 登录页 <!-- pa: page-login -->

#### 5.1.1 登录表单 <!-- pa: login-form -->

- 账号输入 <!-- pa: login-account -->：支持手机号
- 验证码输入 <!-- pa: login-captcha -->：6 位数字

## 5.2 补充区块 <!-- pa: page-extra -->

补充说明段落。

## 5.3 设置页 <!-- pa: page-settings -->

设置页需求。
`,
  )

  execSync(`git init -b main -q "${work}"`)
  execSync(`git -C "${work}" config user.email t@t.local`)
  execSync(`git -C "${work}" config user.name t`)
  execSync(`git -C "${work}" add -A`)
  execSync(`git -C "${work}" commit -qm init`)
  execSync(`git clone -q --bare "${work}" "${REPO_DIR}"`)
  fs.rmSync(work, { recursive: true, force: true })
  return REPO_DIR
}

test.beforeAll(() => ensureCommentRepo())

/** 绑定项目并打开查看器，返回原型 frameLocator（双侧就绪）。 */
async function openViewer(page: Page) {
  await page.goto('/')
  await page.getByTestId('new-project').click()
  await page.getByTestId('form-name').fill('评论E2E项目')
  await page.getByTestId('form-repo-url').fill(REPO_DIR)
  await page.getByTestId('form-token').fill('glpat-e2e')
  await page.getByTestId('form-submit').click()
  await expect(page.getByText('绑定成功')).toBeVisible({ timeout: 30_000 })
  // 按 slug 精确定位（并发 worker 下同名卡片多张、列表顶部归属不确定，
  // 见 anchor.spec.ts 同款注释）
  const mySlug = (await page
    .locator('.card', { hasText: '评论E2E项目' })
    .first()
    .locator('.meta')
    .textContent())!.split(' ')[0]
  await page
    .locator('.card', { hasText: '评论E2E项目' })
    .filter({ hasText: mySlug })
    .getByTestId('open-project')
    .click()
  await expect(page).toHaveURL(new RegExp(`/project/${mySlug}`))
  const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
  await expect(protoFrame.locator('[data-pa="page-login"]')).toBeVisible({ timeout: 15_000 })
  return protoFrame
}

/** 开评论模式，等 bridge 生效（html 加 pp-comment-mode class——SET_COMMENT_MODE
 * 是异步 postMessage，不等就 hover/click 会竞速假失败）。 */
async function enableCommentMode(page: Page) {
  await page.getByTestId('comment-mode').click()
  await expect(
    page.frameLocator('[data-testid="viewer-proto-frame"]').locator('html'),
  ).toHaveClass(/pp-comment-mode/, { timeout: 5_000 })
}

async function disableCommentMode(page: Page) {
  await page.getByTestId('comment-mode').click()
  await expect(
    page.frameLocator('[data-testid="viewer-proto-frame"]').locator('html'),
  ).not.toHaveClass(/pp-comment-mode/, { timeout: 5_000 })
}

test.describe('T4.1 评论模式元素采集', () => {
  test('点锚点元素 → payload 字段齐全（核心验收）', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)

    // hover 高亮（评论模式的视觉反馈）
    const account = protoFrame.locator('[data-pa="login-account"]')
    await account.hover()
    await expect(account).toHaveClass(/pp-comment-hover/)

    // 点击采集 → 宿主面板出现，逐字段断言
    await account.click()
    await expect(page.getByTestId('payload-panel')).toBeVisible()
    await expect(page.getByTestId('payload-target-type')).toHaveText('dom')
    await expect(page.getByTestId('payload-page')).toHaveText('index.html')
    await expect(page.getByTestId('payload-anchor')).toHaveText('login-account')
    await expect(page.getByTestId('payload-nearest')).toHaveText('login-form')
    // 目标自身带 data-pa 时 cssPathOf 短路为属性选择器（T3.1 既有逻辑）
    await expect(page.getByTestId('payload-css-path')).toHaveText('[data-pa="login-account"]')
    await expect(page.getByTestId('payload-text')).toHaveText('账号')
    await expect(page.getByTestId('payload-modal-open')).toHaveText('false')
    await expect(page.getByTestId('payload-viewport')).toHaveText(/^\d+x\d+$/)
    await expect(page.getByTestId('payload-scroll-y')).toHaveText('0')
    await expect(page.getByTestId('payload-route')).toHaveText('index.html')
    // outer_html：目标 + 祖先上下文（在 <details> 内，textContent 可断言）
    await expect(page.getByTestId('payload-outer-html')).toContainText('data-pa="login-account"')
    await expect(page.getByTestId('payload-outer-html')).toContainText('data-pa="login-form"')

    // 滚动后采集另一锚点 → scroll_y 反映真实滚动位置。
    // click 落点在 section 内 h2 上（无 data-pa）→ anchor 空、nearest 命中
    // section 锚点——顺带覆盖「点锚点容器内部子元素」的采集语义
    const extra = protoFrame.locator('[data-pa="page-extra"]')
    await extra.scrollIntoViewIfNeeded()
    await extra.click()
    await expect(page.getByTestId('payload-anchor')).toHaveText('（无）')
    await expect(page.getByTestId('payload-nearest')).toHaveText('page-extra')
    await expect(page.getByTestId('payload-scroll-y')).toHaveText(/^[1-9]\d*$/)
  })

  test('点非锚点元素 → anchor_id 空 + 最近祖先锚点命中', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)
    await protoFrame.locator('h2').first().click()
    await expect(page.getByTestId('payload-panel')).toBeVisible()
    await expect(page.getByTestId('payload-target-type')).toHaveText('dom')
    await expect(page.getByTestId('payload-anchor')).toHaveText('（无）')
    await expect(page.getByTestId('payload-nearest')).toHaveText('page-login')
    await expect(page.getByTestId('payload-text')).toHaveText('登录页')
  })

  test('点 body 空白区 → 页面评论（target_type=page）', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)
    // body padding 区（fixture 设 24px 边距）落点是 body 本身
    await protoFrame.locator('body').click({ position: { x: 5, y: 5 } })
    await expect(page.getByTestId('payload-panel')).toBeVisible()
    await expect(page.getByTestId('payload-target-type')).toHaveText('page')
    await expect(page.getByTestId('payload-page')).toHaveText('index.html')
    await expect(page.getByTestId('payload-css-path')).toHaveText('body')
    await expect(page.getByTestId('payload-anchor')).toHaveText('（无）')
    await expect(page.getByTestId('payload-nearest')).toHaveText('（无）')
  })

  test('评论模式点击拦截：原型自身交互不触发', async ({ page }) => {
    const protoFrame = await openViewer(page)
    // 控制组：模式关闭时点提交 → 表单 submit 触发（标记出现）
    await protoFrame.locator('#submit-btn').click()
    await expect(protoFrame.locator('#submit-marker')).toHaveCount(1)
    // 实验组：模式开启后点击 → 拦截（不新增标记）+ 采集正常
    await enableCommentMode(page)
    await protoFrame.locator('#submit-btn').click()
    await expect(page.getByTestId('payload-panel')).toBeVisible()
    await expect(page.getByTestId('payload-nearest')).toHaveText('login-form')
    await expect(protoFrame.locator('#submit-marker')).toHaveCount(1)
  })

  test('modal_open 检测：弹窗打开后采集 interaction_state', async ({ page }) => {
    const protoFrame = await openViewer(page)
    // 切到弹窗页
    await page.getByTestId('proto-select').click()
    await page.getByRole('option', { name: 'prototype/pages/modal.html' }).click()
    await expect(protoFrame.locator('[data-pa="page-settings"]')).toBeVisible({ timeout: 15_000 })

    // 模式关闭时正常交互：打开弹窗（modal-mask 为 fixed inset:0 无 z-index）
    await protoFrame.locator('#open-modal').click()
    await expect(protoFrame.locator('#modal-mask')).toHaveClass(/open/)

    // 开启评论模式后点弹窗内按钮 → modal_open=true 且拦截（弹窗不关闭）
    await enableCommentMode(page)
    await protoFrame.locator('[data-pa="deregister-cancel"]').click()
    await expect(page.getByTestId('payload-panel')).toBeVisible()
    await expect(page.getByTestId('payload-modal-open')).toHaveText('true')
    await expect(page.getByTestId('payload-anchor')).toHaveText('deregister-cancel')
    await expect(page.getByTestId('payload-page')).toHaveText('pages/modal.html')
    await expect(protoFrame.locator('#modal-mask')).toHaveClass(/open/)
  })

  test('关闭评论模式 → 不再采集，面板清除', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)
    await protoFrame.locator('[data-pa="login-account"]').click()
    await expect(page.getByTestId('payload-panel')).toBeVisible()

    await disableCommentMode(page)
    // 开关关闭即清面板
    await expect(page.getByTestId('payload-panel')).toBeHidden()
    // 点击不再采集（面板不重现）
    await protoFrame.locator('[data-pa="login-captcha"]').click()
    await expect(page.getByTestId('payload-panel')).toBeHidden()
  })

  test('切页后评论模式保持（READY 重发开关）', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)
    await page.getByTestId('proto-select').click()
    await page.getByRole('option', { name: 'prototype/pages/modal.html' }).click()
    await expect(protoFrame.locator('[data-pa="page-settings"]')).toBeVisible({ timeout: 15_000 })
    // iframe 重载后 bridge 状态归零，宿主 READY 时重发 SET_COMMENT_MODE
    await expect(protoFrame.locator('html')).toHaveClass(/pp-comment-mode/, { timeout: 5_000 })
    await protoFrame.locator('[data-pa="settings-profile-edit"]').click()
    await expect(page.getByTestId('payload-panel')).toBeVisible()
    await expect(page.getByTestId('payload-page')).toHaveText('pages/modal.html')
    await expect(page.getByTestId('payload-anchor')).toHaveText('settings-profile-edit')
  })

  test('ROUTE_CHANGE：SPA hash 变化上报宿主', async ({ page }) => {
    const protoFrame = await openViewer(page)
    // iframe 内 hash 导航（SPA 路由模拟；fragment 导航不重载页面）→
    // bridge hashchange 上报 → 宿主显示当前路由（pp-nonce 被剔除）。
    // frameLocator 无 evaluate，经 locator.evaluate 在 frame 上下文执行。
    await protoFrame.locator('body').evaluate(() => {
      window.location.hash = '#/spa-view'
    })
    await expect(page.getByTestId('current-page')).toHaveText('index.html#/spa-view')
  })
})
