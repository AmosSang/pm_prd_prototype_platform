import { expect, test } from '@playwright/test'

/**
 * T1.3 POC 三场景固化
 *
 * 场景一：登录页（基础整页 + 红框）
 * 场景二：带弹窗（截图瞬间弹窗打开——交互状态保留）
 * 场景三：长页滚动（滚动到底部再截图——滚动补偿下的红框对齐）
 *
 * 三场景共用 ShotDemo 生产链路（不做 mock），
 * 「红框套住目标元素」断言方法与 T1.2 红框对齐用例一致：
 * 目标元素染绿 → 截图 → 后端画红框 PNG → 像素扫描绿块与红框包围盒间距。
 */
const GREEN = [0, 200, 0]
const RED = [220, 38, 38]

/** 走生产链路截图并返回后端 PNG 的扫描结果（绿块/红框包围盒） */
async function shotAndScan(page: import('@playwright/test').Page): Promise<{
  pngW: number; pngH: number
  green: { minX: number; minY: number; maxX: number; maxY: number; n: number }
  red: { minX: number; minY: number; maxX: number; maxY: number; n: number }
}> {
  await page.click('button.shot')
  const img = page.locator('[data-testid="shot-image"]')
  await expect(img).toBeVisible({ timeout: 30_000 })
  const src = (await img.getAttribute('src'))!
  const res = await page.request.get(src)
  expect(res.status()).toBe(200)
  const b64 = Buffer.from(await res.body()).toString('base64')
  return page.evaluate(
    async (b64str: string) => {
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
      const GREEN: number[] = [0, 200, 0], RED: number[] = [220, 38, 38]
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
    },
    b64,
  )
}

/** 断言红框四边套住绿块（允许 -5~8px 测量噪声，语义同 T1.2 红框对齐用例） */
function expectBoxAligned(
  scan: Awaited<ReturnType<typeof shotAndScan>>,
  name: string,
) {
  expect(scan.green.n, `[${name}] 绿色目标块应存在`).toBeGreaterThan(100)
  expect(scan.red.n, `[${name}] 红框像素应存在`).toBeGreaterThan(50)
  const padL = scan.red.minX - scan.green.minX
  const padT = scan.red.minY - scan.green.minY
  const padR = scan.green.maxX - scan.red.maxX
  const padB = scan.green.maxY - scan.red.maxY
  expect(padL, `[${name}] 红框左缘`).toBeGreaterThanOrEqual(-5)
  expect(padL, `[${name}] 红框左缘`).toBeLessThanOrEqual(8)
  expect(padT, `[${name}] 红框上缘`).toBeGreaterThanOrEqual(-5)
  expect(padT, `[${name}] 红框上缘`).toBeLessThanOrEqual(8)
  expect(padR, `[${name}] 红框右缘`).toBeGreaterThanOrEqual(-5)
  expect(padR, `[${name}] 红框右缘`).toBeLessThanOrEqual(8)
  expect(padB, `[${name}] 红框下缘`).toBeGreaterThanOrEqual(-5)
  expect(padB, `[${name}] 红框下缘`).toBeLessThanOrEqual(8)
}

async function waitReady(page: import('@playwright/test').Page) {
  await expect(page.getByText('READY（page=')).toBeVisible({ timeout: 15_000 })
}

test.describe('T1.3 POC 三场景', () => {
  test('场景一（登录页）：整页截图 + 红框套住验证码输入框', async ({ page }) => {
    await page.goto('/demo/shot?scene=login')
    await waitReady(page)
    const frame = page.frameLocator('[data-testid="proto-frame"]')

    // 目标元素染绿（染色法：绿块=截图引擎实际渲染位置）
    await frame.locator('#captcha').evaluate((el: HTMLElement) => {
      el.style.background = 'rgb(0,200,0)'
      el.style.borderColor = 'rgb(0,200,0)'
    })

    const scan = await shotAndScan(page)
    expectBoxAligned(scan, '登录页')

    // 还原染色，防污染后续用例
    await frame.locator('#captcha').evaluate((el: HTMLElement) => {
      el.style.background = ''
      el.style.borderColor = ''
    })
  })

  test('场景二（带弹窗）：弹窗打开状态下截图，弹窗与红框同时入图', async ({ page }) => {
    await page.goto('/demo/shot?scene=modal')
    await waitReady(page)
    const frame = page.frameLocator('[data-testid="proto-frame"]')

    // 打开弹窗（截图瞬间的交互状态必须保留）
    await frame.locator('#open-modal').click()
    await expect(frame.locator('#modal-mask.open')).toBeVisible()

    // 弹窗内确认按钮染绿 = 截图目标
    await frame.locator('#confirm-delete').evaluate((el: HTMLElement) => {
      el.style.background = 'rgb(0,200,0)'
      el.style.borderColor = 'rgb(0,200,0)'
    })

    const scan = await shotAndScan(page)
    expectBoxAligned(scan, '带弹窗')

    // 还原
    await frame.locator('#confirm-delete').evaluate((el: HTMLElement) => {
      el.style.background = ''
      el.style.borderColor = ''
    })
    await frame.locator('#cancel').click()
  })

  test('场景三（长页滚动）：滚动到底部截图，红框随滚动补偿对齐', async ({ page }) => {
    // 长页整页（~1400px+）截图渲染耗时显著高于常规页，放宽用例时限
    test.setTimeout(90_000)
    await page.goto('/demo/shot?scene=scroll')
    await waitReady(page)
    const frame = page.frameLocator('[data-testid="proto-frame"]')

    // 滚动到页底（目标元素 #remark 在首屏折叠线以下）
    await frame.locator('#remark').scrollIntoViewIfNeeded()
    await page.waitForTimeout(300)

    await frame.locator('#remark').evaluate((el: HTMLElement) => {
      el.style.background = 'rgb(0,200,0)'
      el.style.borderColor = 'rgb(0,200,0)'
    })

    // 断言 A：整页截图高度应显著大于 iframe 视口（480px）——证明滚动区域完整渲染
    const scan = await shotAndScan(page)
    expect(scan.pngH, '长页整页高度应 > 视口 480px').toBeGreaterThan(600)

    expectBoxAligned(scan, '长页滚动')

    // 还原
    await frame.locator('#remark').evaluate((el: HTMLElement) => {
      el.style.background = ''
      el.style.borderColor = ''
    })
  })
})
