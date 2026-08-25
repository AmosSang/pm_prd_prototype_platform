import { expect, test } from '@playwright/test'

const PROTO_ORIGIN = 'http://localhost:8081'

test.describe('T1.1 沙箱 iframe + bridge.js 往返', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/demo/bridge')
  })

  test('iframe 以 sandbox 属性加载，且含 allow-same-origin（内部系统不收紧）', async ({ page }) => {
    const sandbox = await page.getAttribute('[data-testid="proto-frame"]', 'sandbox')
    expect(sandbox).toBeTruthy()
    // T 决策：内部原型系统不收紧，放开 allow-same-origin 让原型可用 localStorage
    expect(sandbox).toContain('allow-same-origin')
  })

  test('代理注入：原型 HTML 响应含 bridge.js 标签', async ({ request }) => {
    const res = await request.get(`${PROTO_ORIGIN}/proto/demo/prototype/pages/login.html`)
    expect(res.status()).toBe(200)
    const html = await res.text()
    expect(html).toContain('<script src="/bridge.js"></script>')
    expect(html).toContain('data-pa="page-login"')
  })

  test('代理安全：目录穿越与非法路径 404', async ({ request }) => {
    const cases = [
      '/proto/demo/prd/sample.md', // 非 prototype 子树
      '/proto/demo/prototype/../prd/sample.md', // 穿越
      '/proto/../server/config.py', // 项目级穿越
      '/proto/Demo/prototype/pages/login.html', // 大写项目 ID
    ]
    for (const url of cases) {
      const res = await request.get(`${PROTO_ORIGIN}${url}`)
      expect(res.status(), url).toBe(404)
    }
  })

  test('READY：bridge 上报页面就绪', async ({ page }) => {
    await expect(page.getByText('READY（page=/proto/demo/')).toBeVisible({
      timeout: 10_000,
    })
  })

  test('ECHO：PING/ECHO 双向往返', async ({ page }) => {
    // 等 READY 后再点 PING
    await expect(page.getByText('READY（page=')).toBeVisible({ timeout: 10_000 })
    await page.click('button.ping')
    await expect(page.getByText('ECHO（echo=pong-')).toBeVisible({ timeout: 5_000 })
  })

  test('bridge.js 资源可访问且为 JS', async ({ request }) => {
    const res = await request.get(`${PROTO_ORIGIN}/bridge.js`)
    expect(res.status()).toBe(200)
    expect(res.headers()['content-type']).toContain('javascript')
  })
})
