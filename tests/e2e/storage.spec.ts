import { expect, test } from '@playwright/test'
import { createProjectWithContent } from './helpers'

/**
 * 沙箱原型 localStorage：
 * 查看器原型 iframe 用 `sandbox` 且**带 allow-same-origin**（T 决策：内部原型系统
 * 不收紧），iframe 拥有真实 origin，可直接访问 localStorage/sessionStorage。
 *
 * 本用例原型 <head> 里直接 `localStorage.setItem`（不 try/catch，复现用户报错场景），
 * 成功后在 DOMContentLoaded 追加 `#ppp-storage-result`（内容 'ok'）——若沙箱仍抛
 * SecurityError，该 script 中断、div 不会创建，断言即失败；同时收集页面 error 断言无 SecurityError。
 */
const PROTO = {
  'index.html': `<!DOCTYPE html><html><head><meta charset="UTF-8">
<script>
  localStorage.setItem('k', 'v');
  var ok = localStorage.getItem('k') === 'v';
  document.addEventListener('DOMContentLoaded', function () {
    var el = document.createElement('div');
    el.id = 'ppp-storage-result';
    el.textContent = ok ? 'ok' : 'fail';
    document.body.appendChild(el);
  });
</script>
</head><body><main data-pa="page-login">登录页</main></body></html>`,
}

test.describe('T 增强 沙箱原型 localStorage', () => {
  test('原型用 localStorage 不抛 SecurityError（allow-same-origin 生效）', async ({ page, request }) => {
    const proj = await createProjectWithContent(request, `存储E2E-${Date.now().toString(36)}`, {
      protoFiles: PROTO,
    })

    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })

    await page.goto('/')
    await page
      .locator('.card')
      .filter({ hasText: proj.project_id })
      .getByTestId('open-project')
      .click()
    await expect(page).toHaveURL(new RegExp(`/project/${proj.project_id}`))
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })

    // iframe 内脚本用 localStorage 成功：div 内容 'ok'
    const frame = page.frameLocator('[data-testid="viewer-proto-frame"]')
    await expect(frame.locator('#ppp-storage-result')).toHaveText('ok', { timeout: 5_000 })

    // 无 SecurityError / localStorage 相关报错
    const bad = errors.filter((m) => /sandboxed|SecurityError|localStorage|cannot read/i.test(m))
    expect(bad).toEqual([])
  })
})
