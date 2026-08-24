import { expect, test } from '@playwright/test'
import { execSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import http from 'node:http'

/**
 * T2.3 项目绑定 E2E。
 *
 * 验收点（任务卡）：绑定真实测试仓库成功、列表出现卡片、错误 token 给出提示。
 * 「真实测试仓库」= globalSetup 造的本地裸仓库（真实 git clone 全链路，零外网）。
 * 错误 token 用本地 HTTP 服务模拟 GitLab 401。
 */

const REPO_DIR = path.join(os.tmpdir(), 'ppp-e2e-repo')

/** 造一个带 prototype/prd 的裸仓库（幂等：已存在则复用）。 */
function ensureBareRepo() {
  if (fs.existsSync(path.join(REPO_DIR, 'HEAD'))) return REPO_DIR
  fs.rmSync(REPO_DIR, { recursive: true, force: true })
  const work = path.join(os.tmpdir(), 'ppp-e2e-repo-work')
  fs.rmSync(work, { recursive: true, force: true })
  fs.mkdirSync(path.join(work, 'prototype'), { recursive: true })
  fs.mkdirSync(path.join(work, 'prd'), { recursive: true })
  fs.writeFileSync(path.join(work, 'prototype', 'index.html'), '<html><body>home</body></html>')
  fs.writeFileSync(path.join(work, 'prd', 'a.md'), '# PRD\n')
  execSync(`git init -b main -q "${work}"`)
  execSync(`git -C "${work}" config user.email t@t.local`)
  execSync(`git -C "${work}" config user.name t`)
  execSync(`git -C "${work}" add -A`)
  execSync(`git -C "${work}" commit -qm init`)
  execSync(`git clone -q --bare "${work}" "${REPO_DIR}"`)
  return REPO_DIR
}

test.beforeAll(() => {
  ensureBareRepo()
})

test.describe('T2.3 项目绑定', () => {
  test('绑定本地裸仓库成功 → 列表出现卡片', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('project-card-demo')).toBeVisible()

    await page.getByTestId('new-project').click()
    await page.getByTestId('form-name').fill('E2E绑定项目')
    await page.getByTestId('form-repo-url').fill(ensureBareRepo())
    await page.getByTestId('form-token').fill('glpat-e2e-token')
    await page.getByTestId('form-submit').click()

    // 成功提示 + 新卡片出现（project_id 随机，用 name 定位卡片）
    await expect(page.getByText('绑定成功')).toBeVisible({ timeout: 30_000 })
    const card = page.locator('.card', { hasText: 'E2E绑定项目' })
    await expect(card).toBeVisible()
    // slug 规则：中文名转 kebab 后只剩 'e2e' 前缀 + 随机后缀
    await expect(card.locator('.meta')).toContainText(/^e2e-[a-z0-9]+/i)
  })

  test('错误 token → 认证失败提示', async ({ page, request }) => {
    // 本地 HTTP 服务模拟 GitLab 401（对 GET /xxx.git/info/refs 一律 401）
    const srv = http.createServer((_req, res) => {
      res.statusCode = 401
      res.end()
    })
    await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r))
    const port = (srv.address() as { port: number }).port

    await page.goto('/')
    await page.getByTestId('new-project').click()
    await page.getByTestId('form-name').fill('错误token项目')
    await page.getByTestId('form-repo-url').fill(`http://127.0.0.1:${port}/grp/repo.git`)
    await page.getByTestId('form-token').fill('bad-token')
    await page.getByTestId('form-submit').click()

    // 后端分类后的中文提示
    await expect(page.getByText('认证失败')).toBeVisible({ timeout: 30_000 })

    // 失败项目不落库（列表无此卡片）
    await expect(page.locator('.card', { hasText: '错误token项目' })).toHaveCount(0)
    srv.close()
  })
})
