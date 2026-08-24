import { expect, test, type Page } from '@playwright/test'
import { execSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/**
 * T4.1 评论模式元素采集 + T4.2 评论框提交链路 E2E。
 *
 * T4.1 验收点（任务卡）：开启评论模式点元素，采到的 payload 断言字段齐全。
 * 场景：开启评论模式 → hover 蓝色高亮 → click 捕获拦截 → ELEMENT_SELECTED
 * payload 在评论框目标摘要区断言（target_type / prototype_page / anchor_id /
 * nearest_anchor_id / css_path / outer_html / text_excerpt / interaction_state）。
 * 另含：非锚点与页面根目标、点击拦截（原型行为不触发）、modal_open 检测、
 * 开关关闭、切页后模式保持（READY 重发）、ROUTE_CHANGE 上报。
 *
 * T4.2 验收点（任务卡）：三类评论各提交一条，DB 与 reviews/ 文件均出现。
 * DB 由 POST /comments 响应代表（API 事务性，落库失败必 4xx/5xx）；
 * reviews/ 文件直接读本地 clone 断言（JSON 字段 + 截图 PNG + git log）。
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

## 5.4 通用说明

这段没有任何锚点标记，验证任意段落可评论（指纹定位）。
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
    await expect(page.getByTestId('comment-box')).toBeVisible()
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
    await expect(page.getByTestId('comment-box')).toBeVisible()
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
    await expect(page.getByTestId('comment-box')).toBeVisible()
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
    await expect(page.getByTestId('comment-box')).toBeVisible()
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
    await expect(page.getByTestId('comment-box')).toBeVisible()
    await expect(page.getByTestId('payload-modal-open')).toHaveText('true')
    await expect(page.getByTestId('payload-anchor')).toHaveText('deregister-cancel')
    await expect(page.getByTestId('payload-page')).toHaveText('pages/modal.html')
    await expect(protoFrame.locator('#modal-mask')).toHaveClass(/open/)
  })

  test('关闭评论模式 → 不再采集，面板清除', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)
    await protoFrame.locator('[data-pa="login-account"]').click()
    await expect(page.getByTestId('comment-box')).toBeVisible()

    await disableCommentMode(page)
    // 开关关闭即清面板
    await expect(page.getByTestId('comment-box')).toBeHidden()
    // 点击不再采集（面板不重现）
    await protoFrame.locator('[data-pa="login-captcha"]').click()
    await expect(page.getByTestId('comment-box')).toBeHidden()
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
    await expect(page.getByTestId('comment-box')).toBeVisible()
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

// ═══════════════════ T4.2 评论框提交链路（三类评论）═══════════════════

/** 从当前 URL 提取项目 slug（clone 目录名 = /data/repos/{slug}）。 */
function slugOf(page: Page): string {
  return page.url().match(/\/project\/([a-z0-9-]+)/)![1]
}

/** 本地 clone 根目录（Playwright 进程 cwd = tests/）。 */
function cloneDirOf(page: Page): string {
  return path.resolve(process.cwd(), '..', 'data', 'repos', slugOf(page))
}

/** 提交评论并等 POST /comments 响应，返回响应 data（含 comment_id）。 */
async function submitAndWait(page: Page, content: string): Promise<Record<string, any>> {
  await page.getByTestId('comment-content').fill(content)
  const respPromise = page.waitForResponse(
    (r) => r.url().includes('/comments') && r.request().method() === 'POST',
  )
  await page.getByTestId('comment-submit').click()
  const resp = await respPromise
  expect(resp.status()).toBe(200)
  const body = await resp.json()
  expect(body.code).toBe(0)
  return body.data
}

/** T4.3：git 落仓走异步队列——轮询 git log 直到目标 commit 出现。
 * 本地 commit 与远端 push 是同一任务的串行步骤，本地出现后远端随即跟上，
 * 两处都轮询（本地出 + 远端出 = push 生效）。 */
async function waitForGitLog(repoDir: string, message: string, timeout = 15_000): Promise<void> {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    try {
      const log = execSync(`git -C "${repoDir}" log --format=%s`).toString()
      if (log.includes(message)) return
    } catch {
      /* 读取竞态（worker 操作中）——重试 */
    }
    await new Promise((r) => setTimeout(r, 300))
  }
  throw new Error(`git log 未出现「${message}」（${repoDir}）`)
}

test.describe('T4.2 评论提交链路', () => {
  test('DOM 评论全链路：截图 + 成功态 + reviews/ 文件 + git commit/push', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)

    // 点锚点元素 → 评论框 → 填写提交（P1）
    await protoFrame.locator('[data-pa="login-account"]').click()
    await expect(page.getByTestId('comment-box')).toBeVisible()
    await page.getByTestId('comment-priority').selectOption('P1')
    const data = await submitAndWait(page, '验证码发送后按钮要进入 60s 倒计时禁用态')
    const cid: string = data.comment_id
    expect(cid).toMatch(/^c-\d{8}-\d{3}$/)
    expect(data.status).toBe('待确认')
    expect(data.author).toBe('E2E测试员')
    expect(data.git_task.status).toBe('pending') // T4.3：入队即返回

    // 成功态：comment_id 回显 + 截图预览（提交时自动生成，可查看不可编辑）
    await expect(page.getByTestId('submitted-cid')).toHaveText(cid)
    await expect(page.getByTestId('shot-preview')).toBeVisible()

    // reviews/ 评论 JSON（事实源）：payload 原样落 + doc 锚点匹配 + 截图引用
    const clone = cloneDirOf(page)
    const fj = JSON.parse(
      fs.readFileSync(path.join(clone, 'reviews', 'comments', `${cid}.json`), 'utf-8'),
    )
    expect(fj.comment_id).toBe(cid)
    expect(fj.status).toBe('待确认')
    expect(fj.author).toBe('E2E测试员')
    expect(fj.priority).toBe('P1')
    expect(fj.scope).toBe('prototype')
    expect(fj.content).toContain('倒计时禁用态')
    expect(fj.target_type).toBe('dom')
    expect(fj.anchor_id).toBe('login-account')
    expect(fj.nearest_anchor_id).toBe('login-form')
    expect(fj.interaction_state.viewport).toMatch(/^\d+x\d+$/)
    // doc 匹配：候选锚点 login-account 命中 PRD 锚点（E2E 播种用户名「E2E测试员」）
    expect(fj.doc_anchor_id).toBe('login-account')
    expect(fj.doc_excerpt).toContain('账号输入')
    // 截图：文件引用与 PNG 落盘 + 红框坐标
    expect(fj.screenshot).toBe(`shots/${cid}.png`)
    expect(fs.existsSync(path.join(clone, 'reviews', 'shots', `${cid}.png`))).toBe(true)
    expect(fj.highlight_rect).toBeTruthy()
    expect(fj.highlight_rect.w).toBeGreaterThan(0)

    // git：本地 clone 与远端裸仓库（push 生效）最新 commit——队列异步，
    // 轮询等待（任务卡验收：提交评论后 git log 出现对应 commit）
    await waitForGitLog(clone, `comment: ${cid} 创建`)
    await waitForGitLog(REPO_DIR, `comment: ${cid} 创建`)
    const msg = execSync(`git -C "${clone}" log -1 --format=%s`).toString().trim()
    expect(msg).toBe(`comment: ${cid} 创建`)

    // 完成按钮关闭评论框
    await page.getByTestId('comment-done').click()
    await expect(page.getByTestId('comment-box')).toBeHidden()
  })

  test('页面评论：「评论本页」按钮 → target_type=page + 截图无红框', async ({ page }) => {
    await openViewer(page)
    await enableCommentMode(page)

    await page.getByTestId('comment-page-btn').click()
    await expect(page.getByTestId('comment-box')).toBeVisible()
    await expect(page.getByTestId('payload-target-type')).toHaveText('page')
    await expect(page.getByTestId('payload-page')).toHaveText('index.html')
    await expect(page.getByTestId('payload-css-path')).toHaveText('body')

    const data = await submitAndWait(page, '本页首屏加载偏慢，需要骨架屏')
    const cid: string = data.comment_id

    await waitForGitLog(cloneDirOf(page), `comment: ${cid} 创建`)
    const fj = JSON.parse(
      fs.readFileSync(
        path.join(cloneDirOf(page), 'reviews', 'comments', `${cid}.json`),
        'utf-8',
      ),
    )
    expect(fj.target_type).toBe('page')
    expect(fj.css_path).toBe('body')
    // 页面评论不采 outer_html（整页 HTML 无定位意义——T4.2 修订）
    expect(fj.outer_html).toBe('')
    // 页面评论有整页截图但不框红（目标=页面根，框红无意义）
    expect(fj.screenshot).toBe(`shots/${cid}.png`)
    expect(fj.highlight_rect).toBeNull()
  })

  test('文档段落评论：doc_block + fingerprint + 无截图', async ({ page }) => {
    await openViewer(page)
    await enableCommentMode(page)

    // 文档段落 hover「评论」按钮（::after 右上角，坐标点击——伪元素非事件 target）
    const li = page.getByTestId('prd-content').locator('li[data-pa="login-account"]')
    await li.waitFor()
    const box = (await li.boundingBox())!
    // 先 hover 段落触发 pa-locate-hover（mouseover 委托加 class），再点右上区
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.click(box.x + box.width - 40, box.y + 8)

    await expect(page.getByTestId('comment-box')).toBeVisible()
    await expect(page.getByTestId('payload-target-type')).toHaveText('doc_block')
    await expect(page.getByTestId('payload-anchor')).toHaveText('login-account')

    const data = await submitAndWait(page, '账号输入需要补充支持邮箱登录的说明')
    const cid: string = data.comment_id

    await waitForGitLog(cloneDirOf(page), `comment: ${cid} 创建`)
    const fj = JSON.parse(
      fs.readFileSync(
        path.join(cloneDirOf(page), 'reviews', 'comments', `${cid}.json`),
        'utf-8',
      ),
    )
    expect(fj.target_type).toBe('doc_block')
    expect(fj.doc_anchor_id).toBe('login-account')
    expect(fj.doc_excerpt).toContain('账号输入')
    // fingerprint：标题路径 + 段落文本 sha1 前 16 位（hex）
    expect(fj.doc_block_fingerprint).toMatch(/^[0-9a-f]{16}$/)
    // scope 默认按宿主推断：文档评论 → doc
    expect(fj.scope).toBe('doc')
    // 文档评论无截图（目标是 PRD 段落，非原型）
    expect(fj.screenshot).toBeUndefined()
  })

  test('无锚点段落也可评论：指纹定位（doc_anchor_id 空）', async ({ page }) => {
    await openViewer(page)
    await enableCommentMode(page)

    // 5.4 通用说明的段落无锚点：hover 出「评论」按钮（右上，无「定位」）
    const p = page
      .getByTestId('prd-content')
      .locator('p:not([data-pa])', { hasText: '这段没有任何锚点标记' })
    await p.waitFor()
    const box = (await p.boundingBox())!
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.click(box.x + box.width - 40, box.y + 8)

    await expect(page.getByTestId('comment-box')).toBeVisible()
    await expect(page.getByTestId('payload-target-type')).toHaveText('doc_block')
    // 无锚点：doc_anchor_id 空标记 + doc_path（标题链）展示
    await expect(page.getByTestId('payload-anchor')).toHaveText('（无锚点，指纹定位）')
    await expect(page.getByTestId('payload-doc-path')).toHaveText('5.4 通用说明')

    const data = await submitAndWait(page, '这段的通用说明需要补充适用范围')
    const cid: string = data.comment_id

    await waitForGitLog(cloneDirOf(page), `comment: ${cid} 创建`)
    const fj = JSON.parse(
      fs.readFileSync(
        path.join(cloneDirOf(page), 'reviews', 'comments', `${cid}.json`),
        'utf-8',
      ),
    )
    expect(fj.target_type).toBe('doc_block')
    expect(fj.doc_anchor_id).toBe('')
    // 指纹 = sha1(标题链|段落文本)[:16]
    expect(fj.doc_block_fingerprint).toMatch(/^[0-9a-f]{16}$/)
    // 无锚点是正常场景，不标「无 PRD 锚点关联」
    expect(fj.doc_note).toBeUndefined()
    expect(fj.doc_excerpt).toContain('这段没有任何锚点标记')
  })
})

// ═══════════════════ T4.4 评论抽屉：批量确认 / 筛选 / 角标 ═══════════════════

test.describe('T4.4 评论列表抽屉', () => {
  test('批量确认：状态变更 + 落仓 JSON + git log（任务卡验收）', async ({ page }) => {
    await openViewer(page)
    await enableCommentMode(page)

    // 提交 2 条同锚点评论（login-account）+ 1 条另一锚点
    const cids: string[] = []
    for (const _ of [1, 2]) {
      await page.getByTestId('comment-page-btn').isVisible() // 稳定 UI
      const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
      await protoFrame.locator('[data-pa="login-account"]').click()
      await expect(page.getByTestId('comment-box')).toBeVisible()
      const data = await submitAndWait(page, `批量确认测试评论 ${cids.length + 1}`)
      cids.push(data.comment_id)
      await page.getByTestId('comment-done').click()
    }
    await Promise.all(cids.map((c) => waitForGitLog(cloneDirOf(page), `comment: ${c} 创建`)))

    // 打开抽屉：分组 + 同位置合并角标 ×2
    await page.getByTestId('drawer-toggle').click()
    await expect(page.getByTestId('comment-drawer')).toBeVisible()
    await expect(page.getByTestId('comment-group-title')).toHaveText('index.html')
    await expect(page.getByTestId('loc-count')).toHaveText('×2')

    // 勾选 2 条（合并组需先展开）
    await page.getByTestId('comment-loc').click() // 展开合并组
    for (const c of cids) {
      await page.getByTestId(`ck-${c}`).check()
    }
    // 批量确认
    const respPromise = page.waitForResponse(
      (r) => r.url().includes('/batch-status') && r.request().method() === 'POST',
    )
    await page.getByTestId('batch-confirm').click()
    const resp = await respPromise
    expect(resp.status()).toBe(200)
    expect((await resp.json()).data.updated).toHaveLength(2)

    // 状态 tag 变「已确认待修改」
    for (const c of cids) {
      await expect(page.locator(`[data-cid="${c}"] [data-testid="comment-status"]`)).toHaveText(
        '已确认待修改',
      )
    }

    // 落仓：JSON status 字段 + git log（轮询）
    await Promise.all(
      cids.map((c) => waitForGitLog(cloneDirOf(page), `comment: ${c} → 已确认待修改`)),
    )
    const clone = cloneDirOf(page)
    for (const c of cids) {
      const fj = JSON.parse(
        fs.readFileSync(path.join(clone, 'reviews', 'comments', `${c}.json`), 'utf-8'),
      )
      expect(fj.status).toBe('已确认待修改')
    }
  })

  test('筛选逻辑：宿主 + 状态', async ({ page }) => {
    await openViewer(page)
    await enableCommentMode(page)

    // 1 条原型评论 + 1 条文档评论
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
    await protoFrame.locator('[data-pa="login-account"]').click()
    const d1 = await submitAndWait(page, '原型侧评论')
    await page.getByTestId('comment-done').click()

    const li = page.getByTestId('prd-content').locator('li[data-pa="login-account"]')
    const box = (await li.boundingBox())!
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.click(box.x + box.width - 40, box.y + 8)
    const d2 = await submitAndWait(page, '文档侧评论')
    await page.getByTestId('comment-done').click()
    await Promise.all([
      waitForGitLog(cloneDirOf(page), `comment: ${d1.comment_id} 创建`),
      waitForGitLog(cloneDirOf(page), `comment: ${d2.comment_id} 创建`),
    ])

    await page.getByTestId('drawer-toggle').click()
    await expect(page.getByTestId('comment-drawer')).toBeVisible()

    // 宿主筛选：原型 → 只剩 index.html 组；文档 → 只剩 PRD 文档组
    await page.getByTestId('filter-host').selectOption('proto')
    await expect(page.getByTestId('comment-group-title')).toHaveText('index.html')
    await expect(page.locator(`[data-cid="${d1.comment_id}"]`)).toBeVisible()
    await expect(page.locator(`[data-cid="${d2.comment_id}"]`)).toBeHidden()

    await page.getByTestId('filter-host').selectOption('doc')
    await expect(page.getByTestId('comment-group-title')).toHaveText('PRD 文档')
    await expect(page.locator(`[data-cid="${d2.comment_id}"]`)).toBeVisible()
    await expect(page.locator(`[data-cid="${d1.comment_id}"]`)).toBeHidden()

    // 状态筛选：待确认 → 2 条；已修改 → 空
    await page.getByTestId('filter-host').selectOption('all')
    await page.getByTestId('filter-status').selectOption('待确认')
    await expect(page.getByTestId('comment-drawer').locator('.item')).toHaveCount(2)
    await page.getByTestId('filter-status').selectOption('已修改')
    await expect(page.getByTestId('comment-drawer').locator('.empty')).toBeVisible()
  })

  test('原型角标：提交后常显数量，点击打开抽屉', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)

    // 同锚点提交 2 条 → 角标 ×2
    for (const i of [1, 2]) {
      await protoFrame.locator('[data-pa="login-account"]').click()
      await expect(page.getByTestId('comment-box')).toBeVisible()
      await submitAndWait(page, `角标测试评论 ${i}`)
      await page.getByTestId('comment-done').click()
    }

    const badge = protoFrame.locator('.pp-comment-badge')
    await expect(badge).toBeVisible({ timeout: 10_000 })
    await expect(badge).toHaveText('2')

    // 点击角标 → 打开抽屉
    await badge.click()
    await expect(page.getByTestId('comment-drawer')).toBeVisible()
    await expect(page.getByTestId('loc-count')).toHaveText('×2')
  })

  test('截图隐藏注入物：角标截图时隐藏、提交后恢复（截图内无角标需人眼核验）', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)

    // 第 1 条提交 → 角标 ×1 出现
    await protoFrame.locator('[data-pa="login-account"]').click()
    await submitAndWait(page, '截图隐藏测试 1')
    await page.getByTestId('comment-done').click()
    const badge = protoFrame.locator('.pp-comment-badge')
    await expect(badge).toBeVisible({ timeout: 10_000 })
    await expect(badge).toHaveText('1')

    // 第 2 条提交（截图时角标被隐藏）→ 提交后角标恢复并显示 ×2
    // （若 bridge 不恢复，角标会保持 display:none——此断言即恢复逻辑的回归）
    await protoFrame.locator('[data-pa="login-account"]').click()
    await submitAndWait(page, '截图隐藏测试 2')
    await page.getByTestId('comment-done').click()
    await expect(badge).toHaveText('2', { timeout: 10_000 })
    await expect(badge).toBeVisible()
  })
})

// ═══════════════════ T4.4 评论定位 + 文档段落角标 ═══════════════════

test.describe('T4.4 评论定位与文档角标', () => {
  test('定位按钮：dom 评论 → 原型元素闪烁；page 评论 → 整页闪烁', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)

    // dom 评论（有锚点）
    await protoFrame.locator('[data-pa="login-account"]').click()
    const d1 = await submitAndWait(page, '定位测试 dom 评论')
    await page.getByTestId('comment-done').click()
    // page 评论
    await page.getByTestId('comment-page-btn').click()
    const d2 = await submitAndWait(page, '定位测试页面评论')
    await page.getByTestId('comment-done').click()
    await Promise.all([
      waitForGitLog(cloneDirOf(page), `comment: ${d1.comment_id} 创建`),
      waitForGitLog(cloneDirOf(page), `comment: ${d2.comment_id} 创建`),
    ])

    // 打开抽屉 → 点 dom 评论「定位」→ 目标元素闪烁（pp-anchor-flash，1.6s 窗口内断言）
    await page.getByTestId('drawer-toggle').click()
    await expect(page.getByTestId('comment-drawer')).toBeVisible()
    await page.getByTestId(`locate-${d1.comment_id}`).click()
    await expect(
      protoFrame.locator('[data-pa="login-account"]'),
    ).toHaveClass(/pp-anchor-flash/, { timeout: 3_000 })

    // 点 page 评论「定位」→ body 整页闪烁
    await page.getByTestId(`locate-${d2.comment_id}`).click()
    await expect(protoFrame.locator('body')).toHaveClass(/pp-anchor-flash/, { timeout: 3_000 })
  })

  test('定位按钮：doc 评论 → 文档段落高亮（无锚点段落按文本匹配）', async ({ page }) => {
    await openViewer(page)
    await enableCommentMode(page)

    // 无锚点段落（5.4 通用说明）doc 评论
    const p = page
      .getByTestId('prd-content')
      .locator('p:not([data-pa])', { hasText: '这段没有任何锚点标记' })
    await p.waitFor()
    const box = (await p.boundingBox())!
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.click(box.x + box.width - 40, box.y + 8)
    const d = await submitAndWait(page, '定位测试文档评论')
    await page.getByTestId('comment-done').click()
    await waitForGitLog(cloneDirOf(page), `comment: ${d.comment_id} 创建`)

    // 抽屉 → 定位 → 该段落高亮（anchor-highlight class）
    await page.getByTestId('drawer-toggle').click()
    await expect(page.getByTestId('comment-drawer')).toBeVisible()
    await page.getByTestId(`locate-${d.comment_id}`).click()
    await expect(p).toHaveClass(/anchor-highlight/, { timeout: 3_000 })
  })

  test('文档段落角标：hover 显示数量 → 点击开抽屉并定位到该组', async ({ page }) => {
    await openViewer(page)
    await enableCommentMode(page)

    // 有锚点段落（li login-account）doc 评论 ×2
    const li = page.getByTestId('prd-content').locator('li[data-pa="login-account"]')
    for (const i of [1, 2]) {
      const box = (await li.boundingBox())!
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
      await page.mouse.click(box.x + box.width - 40, box.y + 8)
      await submitAndWait(page, `文档角标测试 ${i}`)
      await page.getByTestId('comment-done').click()
    }
    await waitForGitLog(cloneDirOf(page), `comment: `)

    // hover 段落 → 角标显示 ×2
    const box = (await li.boundingBox())!
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    const badge = page.getByTestId('doc-comment-badge')
    await expect(badge).toBeVisible({ timeout: 5_000 })
    await expect(badge).toHaveText('2')

    // 点击角标 → 抽屉打开 + 定位组高亮（loc-focus）
    await badge.click()
    await expect(page.getByTestId('comment-drawer')).toBeVisible({ timeout: 5_000 })
    await expect(page.locator('.loc.loc-focus')).toBeVisible({ timeout: 5_000 })
    await expect(page.getByTestId('loc-count')).toHaveText('×2')
  })
})

// ═══════════════════ T4.5 项目级「可评论」开关 ═══════════════════

test.describe('T4.5 项目级可评论开关', () => {
  test('关闭后入口置灰、已有评论可查看；重新开启恢复', async ({ page }) => {
    const protoFrame = await openViewer(page)
    await enableCommentMode(page)

    // 关闭前提交一条评论（成为「已有评论」）
    await protoFrame.locator('[data-pa="login-account"]').click()
    const d = await submitAndWait(page, '开关关闭前提交的评论')
    await page.getByTestId('comment-done').click()
    await waitForGitLog(cloneDirOf(page), `comment: ${d.comment_id} 创建`)

    // 关闭「允许评论」→ 评论模式联动关闭 + 入口置灰（任务卡验收点）
    await page.getByTestId('commentable-toggle').click()
    await expect(
      page.frameLocator('[data-testid="viewer-proto-frame"]').locator('html'),
    ).not.toHaveClass(/pp-comment-mode/, { timeout: 5_000 })
    await expect(page.getByTestId('comment-mode')).toHaveClass(/is-disabled/)
    await expect(page.getByTestId('comment-mode')).not.toHaveClass(/is-checked/)
    await expect(page.getByTestId('comment-page-btn')).toBeDisabled()

    // 已有评论仍可查看：抽屉打开 + 条目可见（产品方案 §4.5）
    await page.getByTestId('drawer-toggle').click()
    await expect(page.getByTestId('comment-drawer')).toBeVisible()
    await expect(page.locator(`[data-cid="${d.comment_id}"]`)).toBeVisible()

    // T4.5 修订：写 reviews/ 的操作全部置灰/隐藏——
    // 勾选评论后批量按钮仍 disabled；编辑/删除按钮不出现
    await page.getByTestId(`ck-${d.comment_id}`).check()
    await expect(page.getByTestId('batch-confirm')).toBeDisabled()
    await expect(page.getByTestId('batch-ignore')).toBeDisabled()
    await expect(page.getByTestId('edit-comment')).toHaveCount(0)
    await expect(page.getByTestId('del-comment')).toHaveCount(0)

    // 重新开启 → 评论模式开关恢复可用 + 编辑按钮回归
    await page.getByTestId('commentable-toggle').click()
    await expect(page.getByTestId('comment-mode')).not.toHaveClass(/is-disabled/, {
      timeout: 5_000,
    })
    await expect(page.getByTestId('commentable-toggle')).toHaveClass(/is-checked/)
    await expect(page.getByTestId('edit-comment')).toHaveCount(1)
    await expect(page.getByTestId('batch-confirm')).not.toBeDisabled()
  })
})
