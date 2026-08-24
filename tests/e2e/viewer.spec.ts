import { expect, test } from '@playwright/test'
import { execSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/**
 * T2.4 分屏查看器 E2E。
 *
 * 验收点（任务卡）：打开项目分屏可见——E2E 断言两侧加载成功；PRD 标题正确渲染。
 * 流程：绑定本地裸仓库（复用 T2.3 方式）→ 列表点「打开分屏查看器」→
 * 断言 iframe READY（bridge 上报）+ PRD h1 渲染 + 分割条拖动改变左侧宽度。
 */

const REPO_DIR = path.join(os.tmpdir(), 'ppp-e2e-viewer-repo')

function ensureBareRepo() {
  if (fs.existsSync(path.join(REPO_DIR, 'HEAD'))) return REPO_DIR
  fs.rmSync(REPO_DIR, { recursive: true, force: true })
  const work = path.join(os.tmpdir(), 'ppp-e2e-viewer-work')
  fs.rmSync(work, { recursive: true, force: true })
  fs.mkdirSync(path.join(work, 'prototype', 'pages'), { recursive: true })
  fs.mkdirSync(path.join(work, 'prd'), { recursive: true })
  fs.writeFileSync(
    path.join(work, 'prototype', 'pages', 'login.html'),
    '<html><body><main data-pa="page-login">登录页</main></body></html>',
  )
  fs.writeFileSync(
    path.join(work, 'prd', '需求.md'),
    '# 分屏测试 PRD\n\n## 3.1 登录页 <!-- pa: page-login -->\n\n- 账号输入\n',
  )
  execSync(`git init -b main -q "${work}"`)
  execSync(`git -C "${work}" config user.email t@t.local`)
  execSync(`git -C "${work}" config user.name t`)
  execSync(`git -C "${work}" add -A`)
  execSync(`git -C "${work}" commit -qm init`)
  execSync(`git clone -q --bare "${work}" "${REPO_DIR}"`)
  return REPO_DIR
}

test.beforeAll(() => ensureBareRepo())

test.describe('T2.4 分屏查看器', () => {
  test.beforeEach(async ({ page }) => {
    // 绑定项目（每用例独立绑定，避免用例间状态耦合）
    await page.goto('/')
    await page.getByTestId('new-project').click()
    await page.getByTestId('form-name').fill('分屏E2E项目')
    await page.getByTestId('form-repo-url').fill(ensureBareRepo())
    await page.getByTestId('form-token').fill('glpat-e2e')
    await page.getByTestId('form-submit').click()
    await expect(page.getByText('绑定成功')).toBeVisible({ timeout: 30_000 })
  })

  test('打开分屏：iframe 就绪 + PRD 标题渲染', async ({ page }) => {
    // 按 slug 精确定位（并发 worker 下同名卡片多张；列表 id 倒序最新在前，
    // 读首卡 slug 再按 slug 点——即便是别的 worker 刚绑的同仓库项目也无妨，
    // 内容相同，断言照样成立）
    const mySlug = (await page
      .locator('.card', { hasText: '分屏E2E项目' })
      .first()
      .locator('.meta')
      .textContent())!.split(' ')[0]
    await page
      .locator('.card', { hasText: '分屏E2E项目' })
      .filter({ hasText: mySlug })
      .getByTestId('open-project')
      .click()
    await expect(page).toHaveURL(new RegExp(`/project/${mySlug}`))

    // 左：iframe 加载且 bridge READY 上报（.ready[data-ready=true]）
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })
    const frame = page.getByTestId('viewer-proto-frame')
    await expect(frame).toBeVisible()

    // 右：PRD h1 正确渲染（markdown-it）
    await expect(page.getByTestId('prd-content').locator('h1')).toHaveText('分屏测试 PRD')
    // 锚点注释行在纯渲染骨架下不报错（正文块级元素正常渲染）
    await expect(page.getByTestId('prd-content').locator('h2')).toContainText('3.1 登录页')
  })

  test('分割条拖动：左侧宽度变化', async ({ page }) => {
    const mySlug = (await page
      .locator('.card', { hasText: '分屏E2E项目' })
      .first()
      .locator('.meta')
      .textContent())!.split(' ')[0]
    await page
      .locator('.card', { hasText: '分屏E2E项目' })
      .filter({ hasText: mySlug })
      .getByTestId('open-project')
      .click()
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })

    const container = page.locator('.v-body')
    const divider = page.getByTestId('divider')
    const protoPane = page.locator('.pane.proto')

    const before = await protoPane.boundingBox()
    const containerBox = await container.boundingBox()
    expect(before).toBeTruthy()
    expect(containerBox).toBeTruthy()

    // 从分割条当前位置拖到容器 30% 处
    const startX = (await divider.boundingBox())!.x + 3
    const targetX = containerBox!.x + containerBox!.width * 0.3
    await page.mouse.move(startX, containerBox!.y + 100)
    await page.mouse.down()
    await page.mouse.move(targetX, containerBox!.y + 100, { steps: 10 })
    await page.mouse.up()

    const after = await protoPane.boundingBox()
    expect(after!.width).toBeLessThan(before!.width - 50)
  })
})
