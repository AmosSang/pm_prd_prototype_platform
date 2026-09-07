import { expect, test } from '@playwright/test'
import fs from 'node:fs'
import { createProjectWithContent, type ProjectInfo } from './helpers'

/**
 * T9.1 协作者 E2E（三用户场景，PRD §5.1 权限矩阵 V2）：
 *
 * 创建者建项目 → 添加协作者 → 协作者可见管理者工具区并上传成功 →
 * 协作者不能管理协作者（入口不可见 + 接口 403）→ 移除后写操作立即 403。
 */
const BASE = `http://localhost:${process.env.WEB_PORT || '8080'}`
const MAILBOX = '/tmp/ppp-fake-mailbox'
const COLLAB = 'collab@test.local'

function readCode(email: string): string {
  const text = fs.readFileSync(`${MAILBOX}/${email}`, 'utf-8')
  const m = /\b(\d{6})\b/.exec(text)
  if (!m) throw new Error(`mailbox 里没找到 ${email} 验证码：${text}`)
  return m[1]
}

/** 以指定邮箱登录（非默认 session 的用户），返回其 context。 */
async function loginAs(browser: import('@playwright/test').Browser, email: string) {
  const ctx = await browser.newContext({ baseURL: BASE })
  const p = await ctx.newPage()
  await p.goto('/login')
  await p.fill('[data-testid="login-email"]', email)
  await p.click('[data-testid="login-send"]')
  await expect(p.getByText('验证码已发送')).toBeVisible({ timeout: 10_000 })
  await p.fill('[data-testid="login-code"]', readCode(email))
  await p.click('[data-testid="login-submit"]')
  await expect(p.getByTestId('current-user')).toBeVisible({ timeout: 10_000 })
  return ctx
}

test('T9.1 协作者全链路：添加 → 协作者可上传 → 管理协作者被拒 → 移除即时失效', async ({
  page,
  browser,
  request,
}) => {
  // ── 准备：创建者（默认 session）建项目 + 上传内容 ──
  const proj: ProjectInfo = await createProjectWithContent(request, '协作E2E项目', {
    protoFiles: {
      'index.html': '<html><body><main data-pa="page-login">登录页</main></body></html>',
    },
    prdFile: { name: '需求.md', content: '# 协作 PRD\n\n## 5.1 登录页 <!-- pa: page-login -->\n' },
  })

  // ── 创建者：打开查看器 → 协作者入口可见 → 添加协作者 ──
  await page.goto(`/project/${proj.project_id}`)
  await expect(page.getByTestId('creator-tools')).toBeVisible({ timeout: 10_000 })
  const collabBtn = page.getByTestId('collab-manage-btn')
  await expect(collabBtn).toBeVisible()
  await collabBtn.click()
  await expect(page.getByTestId('collab-dialog')).toBeVisible()
  await page.getByTestId('collab-email-input').fill(COLLAB)
  await page.getByTestId('collab-add-btn').click()
  const table = page.getByTestId('collab-table')
  await expect(table).toBeVisible({ timeout: 10_000 })
  await expect(table.getByText('协作者E2E')).toBeVisible()
  await expect(collabBtn).toContainText('协作者 1')

  // 重复添加 → 提示已是协作者
  await page.getByTestId('collab-email-input').fill(COLLAB)
  await page.getByTestId('collab-add-btn').click()
  await expect(page.getByText('已是本项目协作者')).toBeVisible({ timeout: 10_000 })

  // ── 协作者视角：登录 → Home 卡片有「上传内容」无「删除」+ 协作者角标 ──
  const ctxC = await loginAs(browser, COLLAB)
  const pc = await ctxC.newPage()
  await pc.goto('/')
  const cardC = pc.locator('.card').filter({ hasText: proj.project_id })
  await expect(cardC.getByTestId(`upload-${proj.project_id}`)).toBeVisible()
  await expect(cardC.getByTestId(`del-${proj.project_id}`)).toHaveCount(0)
  await expect(cardC.getByTestId(`member-badge-${proj.project_id}`)).toBeVisible()

  // ── 协作者视角：查看器可见管理者工具区 + 可评论开关；协作者管理入口不可见 ──
  await cardC.getByTestId('open-project').click()
  await expect(pc.getByTestId('creator-tools')).toBeVisible({ timeout: 10_000 })
  await expect(pc.getByTestId('commentable-toggle')).toBeVisible()
  await expect(pc.getByTestId('collab-manage-btn')).toHaveCount(0)

  // ── 协作者接口：上传 PRD 成功（管理者权限）──
  const upRes = await pc.request.post(`/api/projects/${proj.id}/prd`, {
    multipart: {
      file: {
        name: '需求-v2.md',
        mimeType: 'text/markdown',
        buffer: Buffer.from('# 协作 PRD v2\n\n## 5.1 登录页 <!-- pa: page-login -->\n'),
      },
    },
  })
  expect(upRes.status(), '协作者上传 PRD 应 200').toBe(200)

  // ── 协作者接口：管理协作者 403、删项目 403 ──
  const addM = await pc.request.post(`/api/projects/${proj.id}/members`, {
    data: { email: 'perm@test.local' },
  })
  expect(addM.status(), '协作者加协作者应 403').toBe(403)
  const delP = await pc.request.delete(`/api/projects/${proj.id}`)
  expect(delP.status(), '协作者删项目应 403').toBe(403)

  // ── 创建者移除协作者 → 协作者下一次写操作立即 403 ──
  const listResp = await request.get(`/api/projects/${proj.id}/members`)
  const rows = (await listResp.json()).data as { user_id: number; email: string }[]
  const collabUid = rows.find((r) => r.email === COLLAB)!.user_id
  const rm = await request.delete(`/api/projects/${proj.id}/members/${collabUid}`)
  expect(rm.status()).toBe(200)

  const upRes2 = await pc.request.post(`/api/projects/${proj.id}/prd`, {
    multipart: {
      file: {
        name: '需求-v3.md',
        mimeType: 'text/markdown',
        buffer: Buffer.from('# v3\n'),
      },
    },
  })
  expect(upRes2.status(), '移除后协作者上传应 403').toBe(403)

  await ctxC.close()
})
