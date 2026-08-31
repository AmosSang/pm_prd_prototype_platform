import { expect, test } from '@playwright/test'
import { createProjectWithContent, type ProjectInfo } from './helpers'

/**
 * GOTO_ANCHOR 揭示流水线 E2E（G3）。
 *
 * 验收点（技术方案 §5）：文档「定位」→ 目标在未激活 Tab / 未触发弹窗 /
 * 未展开折叠区时，bridge 自动激活开合控件（aria-controls / data-pp-trigger /
 * details）再滚动闪烁；无标准控件时降级 toast（no_trigger）。
 *
 * 四场景：
 * 1. 跨页 + 弹窗内元素（aria-controls 触发）
 * 2. 原型内 Tab（未激活 tab 面板内元素，aria-controls 切页签）
 * 3. <details> 折叠区（details-open 原生分支）
 * 4. 降级：隐藏容器无任何触发器 → GOTO_ACK reason=no_trigger → toast
 */

const PROTO = {
  // 顶层占位（上传要求根顶层有 html；不带 data-pa 防重复）
  'index.html': '<html><body><h2>首页占位</h2></body></html>',
  // 弹窗页：modal 内容常驻 DOM，hidden 属性隐藏；触发按钮 aria-controls 关联
  'pages/modal.html': `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: system-ui, sans-serif; margin: 0; }
  .filler { height: 120vh; }
</style></head>
<body>
  <main data-pa="page-modal">
    <h2>弹窗演示页</h2>
    <button id="open-dlg" aria-controls="confirm-dlg" aria-expanded="false" data-pa="modal-open-btn">打开确认框</button>
    <div class="filler">填充高度</div>
  </main>
  <div id="confirm-dlg" class="modal" hidden data-pa="modal-dlg">
    <p>确认执行该操作？</p>
    <button data-pa="modal-confirm">确认</button>
  </div>
  <script>
    document.getElementById('open-dlg').addEventListener('click', function () {
      var dlg = document.getElementById('confirm-dlg')
      dlg.hidden = false
      this.setAttribute('aria-expanded', 'true')
    })
  </script>
</body></html>`,
  // Tab 页：panel 常驻 DOM，未激活 display:none；两个 tab 按钮 aria-controls
  'pages/tab.html': `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: system-ui, sans-serif; margin: 0; }
  .panel { display: none; padding: 16px; }
  .panel.active { display: block; }
</style></head>
<body>
  <main data-pa="page-tab">
    <h2>Tab 演示页</h2>
    <div role="tablist">
      <button role="tab" id="tab-a" aria-controls="panel-a" aria-selected="true" data-pa="tab-a-btn">Tab A</button>
      <button role="tab" id="tab-b" aria-controls="panel-b" aria-selected="false" data-pa="tab-b-btn">Tab B</button>
    </div>
    <div id="panel-a" class="panel active" role="tabpanel" data-pa="tab-panel-a"><p>面板 A 内容</p></div>
    <div id="panel-b" class="panel" role="tabpanel" data-pa="tab-panel-b">
      <p>面板 B 内容</p>
      <p data-pa="tab-b-target">面板 B 深处目标元素</p>
    </div>
  </main>
  <script>
    var tabs = document.querySelectorAll('[role="tab"]')
    tabs.forEach(function (t) {
      t.addEventListener('click', function () {
        tabs.forEach(function (o) {
          o.setAttribute('aria-selected', 'false')
          document.getElementById(o.getAttribute('aria-controls')).classList.remove('active')
        })
        t.setAttribute('aria-selected', 'true')
        document.getElementById(t.getAttribute('aria-controls')).classList.add('active')
      })
    })
  </script>
</body></html>`,
  // details 页：折叠区未展开 + 无触发器隐藏容器（降级场景）同页
  'pages/fold.html': `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: system-ui, sans-serif; margin: 0; }
</style></head>
<body>
  <main data-pa="page-fold">
    <h2>折叠演示页</h2>
    <details data-pa="fold-advanced">
      <summary>高级设置</summary>
      <p data-pa="fold-secret">折叠区深处的秘密参数</p>
    </details>
    <!-- 降级场景：hidden 容器无 aria-controls / data-pp-trigger / dialog / details -->
    <div id="orphan-box" hidden><span data-pa="fold-orphan">无触发器的隐藏元素</span></div>
    <!-- 自身隐藏浮层：元素自己 display:none、祖先全可见，触发器在外部
         （bridge v2 修复的盲区——nearestHiddenContainer 从 parentElement
          起找会漏掉这种形态；用户实测「技能面板/上传浮层」暴露）-->
    <button id="open-sheet" aria-controls="bottom-sheet" data-pa="fold-sheet-btn">打开底部面板</button>
    <div id="bottom-sheet" class="sheet" data-pa="fold-sheet"><span data-pa="fold-sheet-target">面板内目标元素</span></div>
  </main>
  <style>.sheet { display: none; padding: 12px; background: #eef; }
         .sheet.open { display: block; }</style>
  <script>document.getElementById('open-sheet').addEventListener('click', function () {
    document.getElementById('bottom-sheet').classList.toggle('open')
  })</script>
</body></html>`,
}

const PRD = {
  name: '需求.md',
  content: `# 揭示流水线测试 PRD

## 4 页面地图

| 页面 | 原型文件 | 页面锚点 |
|------|---------|---------|
| 弹窗页 | prototype/pages/modal.html | page-modal |
| Tab页 | prototype/pages/tab.html | page-tab |
| 折叠页 | prototype/pages/fold.html | page-fold |

## 5 功能需求

### 5.1 弹窗页 <!-- pa: page-modal -->

打开按钮 <!-- pa: modal-open-btn -->：打开确认框。

确认框 <!-- pa: modal-dlg -->：二次确认。

确认按钮 <!-- pa: modal-confirm -->：执行操作。

### 5.2 Tab页 <!-- pa: page-tab -->

Tab A <!-- pa: tab-a-btn -->。Tab B <!-- pa: tab-b-btn -->。

面板 A <!-- pa: tab-panel-a -->。面板 B <!-- pa: tab-panel-b -->。

面板 B 目标 <!-- pa: tab-b-target -->：深处元素。

### 5.3 折叠页 <!-- pa: page-fold -->

高级设置 <!-- pa: fold-advanced -->：折叠区。

秘密参数 <!-- pa: fold-secret -->：深处参数。

无触发器元素 <!-- pa: fold-orphan -->：降级场景。

面板按钮 <!-- pa: fold-sheet-btn -->：打开底部面板。

底部面板 <!-- pa: fold-sheet -->：自身隐藏浮层。

面板目标 <!-- pa: fold-sheet-target -->：浮层内元素。
`,
}

test.describe('GOTO 揭示流水线', () => {
  let proj: ProjectInfo

  test.beforeEach(async ({ page, request }) => {
    proj = await createProjectWithContent(request, '揭示流水线E2E', {
      protoFiles: PROTO,
      prdFile: PRD,
    })
    await page.goto('/')
    await page
      .locator('.card')
      .filter({ hasText: proj.project_id })
      .getByTestId('open-project')
      .click()
    await expect(page).toHaveURL(new RegExp(`/project/${proj.project_id}`))
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })
    await expect(
      page.getByTestId('prd-content').locator('h3[data-pa="page-modal"]'),
    ).toBeVisible({ timeout: 10_000 })
  })

  /** 点击文档段落（锚点宿主）左上角「定位」按钮。 */
  async function clickLocate(page: import('@playwright/test').Page, anchorId: string) {
    const para = page.getByTestId('prd-content').locator(`[data-pa~="${anchorId}"]`).first()
    await para.scrollIntoViewIfNeeded()
    await para.hover()
    const box = await para.boundingBox()
    expect(box).not.toBeNull()
    await page.mouse.click(box!.x + 20, box!.y + 8)
  }

  test('跨页 + 弹窗：定位弹窗内元素 → 自动切页 + 打开弹窗 + 闪烁', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
    const dlg = protoFrame.locator('[data-pa="modal-dlg"]')
    const confirmBtn = protoFrame.locator('[data-pa="modal-confirm"]')

    // 初始：弹窗 hidden（iframe 默认入口是 index.html）
    await expect(protoFrame.locator('[data-pa="page-modal"]')).toHaveCount(0, { timeout: 3_000 })

    await clickLocate(page, 'modal-confirm')

    // 自动切到 modal.html，弹窗被揭示（hidden 移除）→ 确认按钮可见
    await expect(dlg).toBeVisible({ timeout: 10_000 })
    await expect(confirmBtn).toBeInViewport({ timeout: 5_000 })
    // 揭示后闪烁
    await expect(confirmBtn).toHaveClass(/pp-anchor-flash/, { timeout: 5_000 })
    // 弹窗打开是 aria-controls 触发的（揭示记录：容器 + 方法）
    await expect(protoFrame.locator('#open-dlg')).toHaveAttribute('aria-expanded', 'true')
  })

  test('原型内 Tab：定位未激活面板元素 → 自动切 Tab + 闪烁', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')

    await page.getByTestId('proto-select').click()
    await page.getByRole('option', { name: 'prototype/pages/tab.html' }).click()
    // 等页面就绪（默认激活 panel-a；panel-b 设计上隐藏，不能等它可见）
    await expect(protoFrame.locator('[data-pa="tab-panel-a"]')).toBeVisible({ timeout: 10_000 })
    // 初始：panel-b 未激活，目标不可见
    await expect(protoFrame.locator('[data-pa="tab-b-target"]')).toBeHidden()

    await clickLocate(page, 'tab-b-target')

    // 自动切到 Tab B（panel-b 激活）→ 目标可见 + 闪烁
    await expect(protoFrame.locator('[data-pa="tab-b-target"]')).toBeVisible({ timeout: 10_000 })
    await expect(protoFrame.locator('#tab-b')).toHaveAttribute('aria-selected', 'true')
    await expect(protoFrame.locator('[data-pa="tab-b-target"]')).toHaveClass(/pp-anchor-flash/, {
      timeout: 5_000,
    })
  })

  test('details 折叠区：定位未展开内容 → 自动展开 + 闪烁', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')

    await page.getByTestId('proto-select').click()
    await page.getByRole('option', { name: 'prototype/pages/fold.html' }).click()
    await expect(protoFrame.locator('[data-pa="fold-advanced"]')).toBeVisible({ timeout: 10_000 })
    // 初始：details 未展开
    await expect(protoFrame.locator('[data-pa="fold-secret"]')).toBeHidden()

    await clickLocate(page, 'fold-secret')

    // 自动展开 + 闪烁
    await expect(protoFrame.locator('[data-pa="fold-secret"]')).toBeVisible({ timeout: 10_000 })
    await expect(protoFrame.locator('[data-pa="fold-advanced"]')).toHaveJSProperty('open', true)
    await expect(protoFrame.locator('[data-pa="fold-secret"]')).toHaveClass(/pp-anchor-flash/, {
      timeout: 5_000,
    })
  })

  test('降级：隐藏容器无标准触发器 → toast 提示（reason=no_trigger）', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')

    await page.getByTestId('proto-select').click()
    await page.getByRole('option', { name: 'prototype/pages/fold.html' }).click()
    await expect(protoFrame.locator('[data-pa="fold-advanced"]')).toBeVisible({ timeout: 10_000 })

    await clickLocate(page, 'fold-orphan')

    // GOTO_ACK reason=no_trigger → 宿主 toast（ElMessage）
    await expect(
      page.locator('.el-message').filter({ hasText: '未声明标准开合控件' }),
    ).toBeVisible({ timeout: 5_000 })
    // 元素保持隐藏（未被错误地强制显示）
    await expect(protoFrame.locator('[data-pa="fold-orphan"]')).toBeHidden()
  })

  test('自身隐藏浮层：祖先全可见、隐藏的是元素自己 → 触发器在外部也能揭示', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')

    await page.getByTestId('proto-select').click()
    await page.getByRole('option', { name: 'prototype/pages/fold.html' }).click()
    await expect(protoFrame.locator('[data-pa="fold-advanced"]')).toBeVisible({ timeout: 10_000 })
    // 初始：浮层自身 display:none（祖先链全可见——回归 bridge「自身隐藏」盲区）
    await expect(protoFrame.locator('[data-pa="fold-sheet-target"]')).toBeHidden()

    await clickLocate(page, 'fold-sheet-target')

    // 触发器（fold-sheet-btn）被自动点击 → 浮层打开 → 目标可见 + 闪烁
    await expect(protoFrame.locator('[data-pa="fold-sheet"]')).toHaveClass(/open/, {
      timeout: 10_000,
    })
    await expect(protoFrame.locator('[data-pa="fold-sheet-target"]')).toBeVisible({
      timeout: 5_000,
    })
    await expect(protoFrame.locator('[data-pa="fold-sheet-target"]')).toHaveClass(
      /pp-anchor-flash/,
      { timeout: 5_000 },
    )
  })
})
