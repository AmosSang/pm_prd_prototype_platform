# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: tests/e2e/debug-fade.spec.ts >> 调试：渐隐接住后点击链路
- Location: tests/e2e/debug-fade.spec.ts:52:1

# Error details

```
Error: page.goto: Protocol error (Page.navigate): Cannot navigate to invalid URL
Call log:
  - navigating to "/", waiting until "load"

```

# Test source

```ts
  1   | import { expect, test } from '@playwright/test'
  2   | import { execSync } from 'node:child_process'
  3   | import fs from 'node:fs'
  4   | import os from 'node:os'
  5   | import path from 'node:path'
  6   | 
  7   | /** 复用 anchor.spec.ts 的仓库构造逻辑（简化版）。 */
  8   | const REPO_DIR = path.join(os.tmpdir(), 'ppp-e2e-anchor-repo')
  9   | 
  10  | function ensureAnchorRepo() {
  11  |   fs.rmSync(REPO_DIR, { recursive: true, force: true })
  12  |   const work = path.join(os.tmpdir(), 'ppp-e2e-anchor-work')
  13  |   fs.rmSync(work, { recursive: true, force: true })
  14  |   fs.mkdirSync(path.join(work, 'prototype'), { recursive: true })
  15  |   fs.mkdirSync(path.join(work, 'prd'), { recursive: true })
  16  | 
  17  |   fs.writeFileSync(
  18  |     path.join(work, 'prototype', 'index.html'),
  19  |     `<!DOCTYPE html>
  20  | <html><head><meta charset="UTF-8"><style>
  21  |   body { font-family: system-ui, sans-serif; margin: 0; }
  22  |   section { min-height: 120vh; padding: 24px; border-bottom: 1px solid #eee; }
  23  | </style></head>
  24  | <body>
  25  |   <section data-pa="page-login">
  26  |     <h2>登录页</h2>
  27  |     <form data-pa="login-form">
  28  |       <input id="account" data-pa="login-account" placeholder="账号">
  29  |     </form>
  30  |   </section>
  31  | </body></html>`,
  32  |   )
  33  |   fs.writeFileSync(
  34  |     path.join(work, 'prd', '需求.md'),
  35  |     `# 锚点测试 PRD
  36  | 
  37  | ## 5.1 登录页 <!-- pa: page-login -->
  38  | 
  39  | 段落。
  40  | `,
  41  |   )
  42  |   execSync(`git init -b main -q "${work}"`)
  43  |   execSync(`git -C "${work}" config user.email t@t.local`)
  44  |   execSync(`git -C "${work}" config user.name t`)
  45  |   execSync(`git -C "${work}" add -A`)
  46  |   execSync(`git -C "${work}" commit -qm init`)
  47  |   execSync(`git clone -q --bare "${work}" "${REPO_DIR}"`)
  48  |   fs.rmSync(work, { recursive: true, force: true })
  49  |   return REPO_DIR
  50  | }
  51  | 
  52  | test('调试：渐隐接住后点击链路', async ({ page }) => {
  53  |   test.setTimeout(60_000)
  54  | 
  55  |   // 监听宿主收到的所有 message（含 ANCHOR_CLICK）
  56  |   const received: string[] = []
  57  |   page.on('console', (msg) => console.log('[console]', msg.text().slice(0, 200)))
  58  | 
> 59  |   await page.goto('/')
      |              ^ Error: page.goto: Protocol error (Page.navigate): Cannot navigate to invalid URL
  60  |   await page.getByTestId('new-project').click()
  61  |   await page.getByTestId('form-name').fill(`调试渐隐-${Date.now().toString(36)}`)
  62  |   await page.getByTestId('form-repo-url').fill(ensureAnchorRepo())
  63  |   await page.getByTestId('form-token').fill('glpat-e2e')
  64  |   await page.getByTestId('form-submit').click()
  65  |   await expect(page.getByText('绑定成功')).toBeVisible({ timeout: 30_000 })
  66  | 
  67  |   await page.getByTestId('open-project').first().click()
  68  |   await expect(page).toHaveURL(/\/project\//)
  69  |   await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })
  70  | 
  71  |   const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
  72  |   const icon = protoFrame.locator('.pp-anchor-icon')
  73  | 
  74  |   // hook：宿主 window 上记录收到的 postMessage
  75  |   await page.evaluate(() => {
  76  |     ;(window as any).__msgs = []
  77  |     window.addEventListener('message', (e: MessageEvent) => {
  78  |       ;(window as any).__msgs.push((e.data || {}).type + ':' + (e.data || {}).anchorId)
  79  |     })
  80  |   })
  81  | 
  82  |   await protoFrame.locator('[data-pa="page-login"]').hover()
  83  |   await expect(icon).toBeVisible({ timeout: 5_000 })
  84  | 
  85  |   // icon 位置与大小
  86  |   const iconBox = await icon.boundingBox()
  87  |   console.log('iconBox =', JSON.stringify(iconBox))
  88  | 
  89  |   // 移开鼠标 → 渐隐开始
  90  |   await page.mouse.move(5, 300)
  91  |   await page.waitForTimeout(200) // 渐隐进行中（200ms < 1000ms）
  92  | 
  93  |   // 渐隐中 icon 的类名与 opacity
  94  |   const state1 = await icon.getAttribute('class')
  95  |   console.log('fading state class =', state1)
  96  | 
  97  |   // hover 回 icon：Playwright hover 会先移动鼠标到元素中心
  98  |   await icon.hover({ timeout: 2_000 })
  99  |   await page.waitForTimeout(100)
  100 |   const state2 = await icon.getAttribute('class')
  101 |   const opacity = await icon.evaluate((el) => getComputedStyle(el).opacity)
  102 |   console.log('after hover class =', state2, 'opacity =', opacity)
  103 | 
  104 |   // 点击
  105 |   await icon.click()
  106 |   await page.waitForTimeout(500)
  107 | 
  108 |   const msgs = await page.evaluate(() => (window as any).__msgs)
  109 |   console.log('宿主收到的消息 =', JSON.stringify(msgs))
  110 | 
  111 |   const target = page.getByTestId('prd-content').locator('h2[data-pa="page-login"]')
  112 |   const cls = await target.getAttribute('class')
  113 |   console.log('PRD 目标 class =', cls)
  114 | })
  115 | 
```