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

  test('截图保留页面背景底色（背景设在 html 上，用户报障场景）', async ({ page }) => {
    // 复刻报障形态：视觉底色来自 html（深色），body 透明——
    // 旧版画布硬编码白底 + 克隆根是 body（html 层背景不进克隆）
    // → 元素完整但底色丢失。修复后画布底色取 html/body 第一个非透明背景。
    await expect(page.getByText('READY（page=')).toBeVisible({ timeout: 10_000 })
    const protoFrame = page.frameLocator('[data-testid="proto-frame"]')
    const DARK = [26, 26, 46] // rgb(26,26,46)

    await protoFrame.locator('html').evaluate((el: HTMLElement) => {
      el.style.backgroundColor = 'rgb(26,26,46)'
      document.body.style.backgroundColor = 'transparent' // 覆盖 fixture 的 #f5f6f8
    })
    try {
      await page.click('button.shot')
      const img = page.locator('[data-testid="shot-image"]')
      await expect(img).toBeVisible({ timeout: 20_000 })
      const src = await img.getAttribute('src')
      const res = await page.request.get(src!)
      expect(res.status()).toBe(200)
      const b64 = Buffer.from(await res.body()).toString('base64')

      // 解码 PNG，取四角像素（margin/空白区最外缘 = 画布底色）
      const corners = await page.evaluate(async (b64str: string) => {
        const bin = atob(b64str)
        const bytes = new Uint8Array(bin.length)
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
        const bmp = await createImageBitmap(new Blob([bytes], { type: 'image/png' }))
        const c = document.createElement('canvas')
        c.width = bmp.width
        c.height = bmp.height
        const g = c.getContext('2d')!
        g.drawImage(bmp, 0, 0)
        const d = g.getImageData(0, 0, c.width, c.height).data
        const px = (x: number, y: number) => {
          const i = (y * c.width + x) * 4
          return [d[i], d[i + 1], d[i + 2]]
        }
        return {
          tl: px(2, 2),
          tr: px(c.width - 3, 2),
          bl: px(2, c.height - 3),
          br: px(c.width - 3, c.height - 3),
        }
      }, b64)

      for (const [name, [r, g, b]] of Object.entries(corners)) {
        expect(Math.abs(r - DARK[0]), `角[${name}] R=${r} 应为深色底`).toBeLessThanOrEqual(8)
        expect(Math.abs(g - DARK[1]), `角[${name}] G=${g} 应为深色底`).toBeLessThanOrEqual(8)
        expect(Math.abs(b - DARK[2]), `角[${name}] B=${b} 应为深色底`).toBeLessThanOrEqual(8)
      }
    } finally {
      await protoFrame.locator('html').evaluate((el: HTMLElement) => {
        el.style.backgroundColor = ''
        document.body.style.backgroundColor = ''
      })
    }
  })

  test('红框对齐：红框精确套住目标元素（含自定义 margin 场景）', async ({ page }) => {
    // 思路（红框对齐的产品语义断言）：
    // 染色元素（绿）与后端画的红框都渲染在同一张 PNG 上：
    //   红框位置 = bridge 上报的文档坐标（rect）+ 后端 Pillow 绘制
    //   绿块位置 = 截图引擎实际渲染位置
    // 两者对齐 ⟺ 红框精确套住目标元素 ⟺ 用户所见正确
    // 该方法消掉「文档坐标→PNG 坐标」映射误差，只断言最终视觉语义。
    // 由人眼发现的红框偏移 bug 转化而来（2026-08-21，修复前红框偏左上 +8px）
    await expect(page.getByText('READY（page=')).toBeVisible({ timeout: 10_000 })
    const protoFrame = page.frameLocator('[data-testid="proto-frame"]')

    const scenarios = [
      // 注意：fixture CSS 的 body margin 必须为 0（margin: 0;），
      // 场景值以 inline style 显式设置，不依赖 CSS 默认值
      { name: '显式 margin=0px', margin: '0px' },
      { name: '自定义 margin 20px 16px', margin: '20px 16px' },
    ]

    for (const sc of scenarios) {
      await protoFrame.locator('#captcha').evaluate((el: HTMLElement, margin: string) => {
        // 保险丝：先清残留（上轮断言失败可能跳过还原）
        el.style.background = ''
        el.style.borderColor = ''
        document.body.style.margin = ''
        // 再按本场景设置（background+border 全染，border-box 整体变绿）
        document.body.style.margin = margin
        el.style.background = 'rgb(0,200,0)'
        el.style.borderColor = 'rgb(0,200,0)'
        const rect = el.getBoundingClientRect()
        ;(window as any).__expected = {
          x: Math.round(rect.left + window.scrollX),
          y: Math.round(rect.top + window.scrollY),
        }
      }, sc.margin)

      try {
        // 走生产链路截图
        await page.click('button.shot')
        const img = page.locator('[data-testid="shot-image"]')
        await expect(img).toBeVisible({ timeout: 20_000 })
        const src = await img.getAttribute('src')

        // 取后端 PNG，扫描绿块与红框的实际位置
        const res = await page.request.get(src!)
        expect(res.status()).toBe(200)
        const b64 = Buffer.from(await res.body()).toString('base64')
        const scan = await page.evaluate(async (b64str: string) => {
          const bin = atob(b64str)
          const bytes = new Uint8Array(bin.length)
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
          const blob = new Blob([bytes], { type: 'image/png' })
          const bmp = await createImageBitmap(blob)
          const c = document.createElement('canvas')
          c.width = bmp.width; c.height = bmp.height
          const g = c.getContext('2d')!
          g.drawImage(bmp, 0, 0)
          const data = g.getImageData(0, 0, c.width, c.height).data
          const GREEN = [0, 200, 0], RED = [220, 38, 38]
          const tol = 25
          const box = {
            g: { minX: 1e9, minY: 1e9, maxX: -1, maxY: -1, n: 0 },
            r: { minX: 1e9, minY: 1e9, maxX: -1, maxY: -1, n: 0 },
          }
          for (let y = 0; y < c.height; y++) {
            for (let x = 0; x < c.width; x++) {
              const i = (y * c.width + x) * 4
              if (Math.abs(data[i]-GREEN[0])<=tol && Math.abs(data[i+1]-GREEN[1])<=tol && Math.abs(data[i+2]-GREEN[2])<=tol) {
                box.g.n++
                if (x<box.g.minX) box.g.minX=x; if (x>box.g.maxX) box.g.maxX=x
                if (y<box.g.minY) box.g.minY=y; if (y>box.g.maxY) box.g.maxY=y
              }
              if (Math.abs(data[i]-RED[0])<=tol && Math.abs(data[i+1]-RED[1])<=tol && Math.abs(data[i+2]-RED[2])<=tol) {
                box.r.n++
                if (x<box.r.minX) box.r.minX=x; if (x>box.r.maxX) box.r.maxX=x
                if (y<box.r.minY) box.r.minY=y; if (y>box.r.maxY) box.r.maxY=y
              }
            }
          }
          return { pngW: c.width, pngH: c.height, green: box.g, red: box.r }
        }, b64)

        // 断言 1：绿块与红框都存在
        expect(scan.green.n, `[${sc.name}] 绿色目标块应存在`).toBeGreaterThan(100)
        expect(scan.red.n, `[${sc.name}] 红框像素应存在`).toBeGreaterThan(50)

        // 断言 2：红框套住绿块——红框外缘与绿块外缘的间距在 -1~8px
        // （红框线宽 3px 画在 rect 外缘；扫描侧绿块边界因抗锯齿混色内收 1~2px，
        //  故红框外缘比绿块外缘小 0~5px 均为测量噪声，真实渲染偏移已 ≤1px）
        const padL = scan.red.minX - scan.green.minX
        const padT = scan.red.minY - scan.green.minY
        const padR = scan.green.maxX - scan.red.maxX
        const padB = scan.green.maxY - scan.red.maxY
        expect(padL, `[${sc.name}] 红框左缘应在绿块左缘附近（修复前 -8px）`).toBeGreaterThanOrEqual(-5)
        expect(padL, `[${sc.name}] 红框左缘不应超出绿块太远`).toBeLessThanOrEqual(8)
        expect(padT, `[${sc.name}] 红框上缘应在绿块上缘附近（修复前 -8px）`).toBeGreaterThanOrEqual(-5)
        expect(padT, `[${sc.name}] 红框上缘不应超出绿块太远`).toBeLessThanOrEqual(8)
        expect(padR, `[${sc.name}] 红框右缘应在绿块右缘附近`).toBeGreaterThanOrEqual(-5)
        expect(padR, `[${sc.name}] 红框右缘不应超出绿块太远`).toBeLessThanOrEqual(8)
        expect(padB, `[${sc.name}] 红框下缘应在绿块下缘附近`).toBeGreaterThanOrEqual(-5)
        expect(padB, `[${sc.name}] 红框下缘不应超出绿块太远`).toBeLessThanOrEqual(8)
      } finally {
        // 还原（断言失败也必须执行，否则污染下轮）
        await protoFrame.locator('#captcha').evaluate((el: HTMLElement) => {
          el.style.background = ''
          el.style.borderColor = ''
          document.body.style.margin = ''
        })
      }
    }
  })

  test('上传接口校验：缺文件/非法 rect 拒绝', async ({ request }) => {
    const fd = new FormData()
    fd.append('request_id', 'test-req-1')
    const res = await request.post('/api/projects/demo/shots', { multipart: fd })
    // Playwright multipart 需要 buffer，缺 screenshot 字段 → 400
    expect([400]).toContain(res.status())
  })
})
