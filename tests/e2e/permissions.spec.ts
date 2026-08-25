import { expect, test } from '@playwright/test'
import fs from 'node:fs'
import { createProjectWithContent, type ProjectInfo } from './helpers'

/**
 * T8.4 权限矩阵 E2E（§6）：创建者专属按钮显隐 + 普通用户接口 403。
 *
 * 场景：创建者建项目+上传 → 查看器看到导出/「可评论」开关、Home 卡片看到
 * 上传/删除；第二个登录用户（非创建者）打开同一项目 → 这些按钮全部不可见，
 * 调用创建者专属接口返回 403。
 */
const BASE = `http://localhost:${process.env.WEB_PORT || '8080'}`
const MAILBOX = '/tmp/ppp-fake-mailbox'
const PERM_USER = 'perm@test.local'

function readCode(): string {
  const text = fs.readFileSync(`${MAILBOX}/${PERM_USER}`, 'utf-8')
  const m = /\b(\d{6})\b/.exec(text)
  if (!m) throw new Error(`mailbox 里没找到验证码：${text}`)
  return m[1]
}

/** 第二个用户登录（非创建者），返回带其 session 的 context。 */
async function loginAsPermUser(browser: import('@playwright/test').Browser) {
  const ctx = await browser.newContext({ baseURL: BASE })
  const p = await ctx.newPage()
  await p.goto('/login')
  await p.fill('[data-testid="login-email"]', PERM_USER)
  await p.click('[data-testid="login-send"]')
  await expect(p.getByText('验证码已发送')).toBeVisible({ timeout: 10_000 })
  await p.fill('[data-testid="login-code"]', readCode())
  await p.click('[data-testid="login-submit"]')
  await expect(p.getByTestId('current-user')).toBeVisible({ timeout: 10_000 })
  return ctx
}

test('T8.4 双用户权限：创建者可见专属按钮，普通用户不可见 + 接口 403', async ({
  page,
  browser,
  request,
}) => {
  // 创建者建项目 + 上传内容（默认 session = 创建者）
  const proj: ProjectInfo = await createProjectWithContent(request, '权限E2E项目', {
    protoFiles: {
      'index.html': '<html><body><main data-pa="page-login">登录页</main></body></html>',
    },
    prdFile: { name: '需求.md', content: '# 权限 PRD\n\n## 5.1 登录页 <!-- pa: page-login -->\n' },
  })

  // 创建者视角：Home 卡片有上传/删除按钮
  await page.goto('/')
  const card = page.locator('.card').filter({ hasText: proj.project_id })
  await expect(card.getByTestId(`del-${proj.project_id}`)).toBeVisible()
  await expect(card.getByTestId(`upload-${proj.project_id}`)).toBeVisible()

  // 创建者视角：查看器有创建者工具区（上传+导出）与「可评论」开关
  await card.getByTestId('open-project').click()
  await expect(page.getByTestId('creator-tools')).toBeVisible()
  await expect(page.getByTestId('commentable-toggle')).toBeVisible()

  // 第二个登录用户（非创建者）
  const ctx2 = await loginAsPermUser(browser)
  const p2 = await ctx2.newPage()

  // 普通用户视角：Home 卡片无上传/删除
  await p2.goto('/')
  const card2 = p2.locator('.card').filter({ hasText: proj.project_id })
  await expect(card2.getByTestId(`del-${proj.project_id}`)).toHaveCount(0)
  await expect(card2.getByTestId(`upload-${proj.project_id}`)).toHaveCount(0)

  // 普通用户视角：查看器无创建者工具区 /「可评论」开关
  await card2.getByTestId('open-project').click()
  await expect(p2.getByTestId('creator-tools')).toHaveCount(0)
  await expect(p2.getByTestId('commentable-toggle')).toHaveCount(0)

  // 接口 403：普通用户调创建者专属接口
  const exp = await p2.request.get(`/api/projects/${proj.id}/comments/export?scope=all`)
  expect(exp.status(), '普通用户导出应 403').toBe(403)

  const del = await p2.request.delete(`/api/projects/${proj.id}`)
  expect(del.status(), '普通用户删项目应 403').toBe(403)

  // 普通用户调状态流转：403（跳过并报告）。需真实项目内评论——先让创建者提一条
  const commentResp = await request.post(`/api/projects/${proj.id}/comments`, {
    data: {
      payload: {
        target_type: 'dom',
        prototype_page: 'index.html',
        anchor_id: 'page-login',
        nearest_anchor_id: '',
        css_path: '[data-pa="page-login"]',
        outer_html: '<main data-pa="page-login">登录页</main>',
        text_excerpt: '登录页',
        interaction_state: { modal_open: false, viewport: '1440x900', scroll_y: 0, route: 'index.html' },
      },
      content: '权限测试评论',
      priority: 'P2',
      scope: 'prototype',
    },
  })
  expect(commentResp.status()).toBe(200)
  const cid = (await commentResp.json()).data.comment_id as string

  const st = await p2.request.post('/api/comments/batch-status', {
    data: { cids: [cid], status: '已确认待修改' },
  })
  expect(st.status()).toBe(200)
  expect((await st.json()).data.skipped[0].reason).toBe('仅项目创建者可操作状态')

  await ctx2.close()
})
