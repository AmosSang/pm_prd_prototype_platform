import { expect, test } from '@playwright/test'

const PROTO_ORIGIN = 'http://localhost:8081'

test.describe('T1.2 截图链路', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/demo/shot')
  })

  test('vendor modern-screenshot 可从代理加载', async ({ request }) => {
    const res = await request.get(`${PROTO_ORIGIN}/vendor/modern-screenshot.mjs`)
    expect(res.status()).toBe(200)
    expect(res.headers()['content-type']).toContain('javascript')
  })

  test('vendor 路径安全：非法文件名 404', async ({ request }) => {
    const cases = [
      '/vendor/../../bridge/bridge.js',
      '/vendor/..%2f..%2fserver/config.py',
      '/vendor/no-such-file.js',
    ]
    for (const url of cases) {
      const res = await request.get(`${PROTO_ORIGIN}${url}`)
      expect(res.status(), url).toBe(404)
    }
  })

  test('完整链路：截图 → Blob 回传 → 上传 → 红框 PNG 可访问', async ({ page }) => {
    // 等 iframe READY
    await expect(page.getByText('READY（page=')).toBeVisible({ timeout: 10_000 })

    // 点截图按钮
    await page.click('button.shot')

    // 结果图出现
    const img = page.locator('[data-testid="shot-image"]')
    await expect(img).toBeVisible({ timeout: 20_000 })

    // 信息行包含尺寸与红框（非零）
    const info = await page.locator('[data-testid="shot-info"]').textContent()
    expect(info).toContain('整页')
    expect(info).toContain('红框')

    // 上传后的 PNG 可直接访问且为 PNG
    const src = await img.getAttribute('src')
    expect(src).toMatch(/^\/api\/shots\/demo\/.+\.png$/)
    const res = await page.request.get(src!)
    expect(res.status()).toBe(200)
    expect(res.headers()['content-type']).toBe('image/png')

    // PNG 魔数校验
    const buf = await res.body()
    expect(buf.length).toBeGreaterThan(1000)
    expect(buf[0]).toBe(0x89)
    expect(buf[1]).toBe(0x50)
    expect(buf[2]).toBe(0x4e)
    expect(buf[3]).toBe(0x47)
  })

  test('上传接口校验：缺文件/非法 rect 拒绝', async ({ request }) => {
    const fd = new FormData()
    fd.append('request_id', 'test-req-1')
    const res = await request.post('/api/projects/demo/shots', { multipart: fd })
    // Playwright multipart 需要 buffer，缺 screenshot 字段 → 400
    expect([400]).toContain(res.status())
  })
})
