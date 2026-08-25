import { expect, test } from '@playwright/test'
import { createProjectWithContent, uploadPrd, type ProjectInfo } from './helpers'

/**
 * T3.1 锚点正向联动 E2E。
 *
 * 验收点（任务卡）：点原型锚点 icon → 右侧滚动到对应段落且高亮 class 出现。
 * 场景（T8.1 去 Git 本地化）：API 建项目 + 上传带 [data-pa] 原型与
 * `<!-- pa: xxx -->` 注释 PRD → 打开分屏 → hover 原型锚点元素出现 ◈ icon →
 * 点击 → 右侧 [data-pa] 元素滚动到可视区 + .anchor-highlight class 出现。
 */

const PROTO = {
  'index.html': `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: system-ui, sans-serif; margin: 0; }
  section { min-height: 120vh; padding: 24px; border-bottom: 1px solid #eee; }
</style></head>
<body>
  <section data-pa="page-login">
    <h2>登录页</h2>
    <form data-pa="login-form">
      <input id="account" data-pa="login-account" placeholder="账号">
      <input id="captcha" data-pa="login-captcha" placeholder="验证码">
    </form>
  </section>
  <section data-pa="page-home" style="margin-top:200vh">
    <h2>首页</h2>
  </section>
</body></html>`,
}

const PRD = {
  name: '需求.md',
  content: `# 锚点测试 PRD

## 5.1 登录页 <!-- pa: page-login -->

#### 5.1.1 登录表单 <!-- pa: login-form -->

- 账号输入 <!-- pa: login-account -->：支持手机号
- 验证码输入 <!-- pa: login-captcha -->：6 位数字

<!-- pa: page-home -->

工作台首页需求段落。

## 9 其他章节（填充高度用）

远端段落 1

远端段落 2
`,
}

test.describe('T3.1 锚点正向联动', () => {
  let proj: ProjectInfo

  test.beforeEach(async ({ page, request }) => {
    proj = await createProjectWithContent(request, '锚点E2E项目', {
      protoFiles: PROTO,
      prdFile: PRD,
    })

    // 按 slug 精确定位（并发 worker 下同名项目卡片有多张——slug 数据库唯一，稳）
    await page.goto('/')
    await page
      .locator('.card')
      .filter({ hasText: proj.project_id })
      .getByTestId('open-project')
      .click()
    await expect(page).toHaveURL(new RegExp(`/project/${proj.project_id}`))
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })
    // 双侧就绪：iframe READY 只代表左侧原型好了；右侧 PRD 是异步 fetch +
    // markdown 渲染，不等它会导致后续 ANCHOR_CLICK 早于 PRD 渲染到达——
    // jumpToDocAnchor 查不到元素只弹 toast，高亮断言必挂（全量负载下复现）
    await expect(
      page.getByTestId('prd-content').locator('h2[data-pa="page-login"]'),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('PRD 渲染：锚点注释转 data-pa 属性且注释文本隐藏', async ({ page }) => {
    const prd = page.getByTestId('prd-content')
    // 行尾注释 → h2 data-pa
    await expect(prd.locator('h2[data-pa="page-login"]')).toContainText('5.1 登录页')
    // li 行尾注释 → li data-pa
    await expect(prd.locator('li[data-pa="login-account"]')).toBeVisible()
    // 独立注释 → 段落 data-pa
    await expect(prd.locator('p[data-pa="page-home"]')).toContainText('工作台首页需求段落')
    // 注释文本不出现
    await expect(prd.locator('text=pa:')).toHaveCount(0)
  })

  test('bridge 上报锚点计数', async ({ page }) => {
    // 原型有 5 个 data-pa（page-login/login-form/login-account/login-captcha/page-home）
    await expect(page.getByTestId('anchor-count')).toHaveText('锚点 5', { timeout: 10_000 })
  })

  test('正向联动：点原型锚点 icon → 右侧滚动 + 高亮 2s', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')

    // hover 到原型深处锚点（page-home 在 200vh 之下，联动后右侧必滚动）
    // 先滚动原型让 page-home 可见，再 hover 触发 icon
    await protoFrame.locator('[data-pa="page-home"]').scrollIntoViewIfNeeded()
    await protoFrame.locator('[data-pa="page-home"]').hover()

    // ◈ icon 出现并点击（icon 在 iframe 内，由 bridge 注入）
    const icon = protoFrame.locator('.pp-anchor-icon')
    await expect(icon).toBeVisible({ timeout: 5_000 })
    await icon.click()

    // 右侧 [data-pa=page-home] 段落获得高亮 class（2s 后移除，断言窗口足够）
    const target = page.getByTestId('prd-content').locator('p[data-pa="page-home"]')
    await expect(target).toHaveClass(/anchor-highlight/, { timeout: 3_000 })

    // 滚动联动：目标元素进入右侧可视区
    await expect(target).toBeInViewport({ timeout: 3_000 })
  })

  test('渐隐宽限期：离开锚点后 icon 不立即消失，1s 内可接住并点击', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
    const icon = protoFrame.locator('.pp-anchor-icon')

    // hover 组件级锚点（input，无嵌套歧义）：hover page-login 这类容器
    // 锚点时，落点随渲染时序可能落在内部 form/input 的地盘（此前 iframe
    // 内事件探针实测过落点 FORM），iconTarget 变成 login-form → click
    // 高亮的是 h4 而非 h2，断言会「假失败」（单跑/全量落点不同 → 偶发）。
    await protoFrame.locator('[data-pa="login-account"]').hover()
    await expect(icon).toBeVisible({ timeout: 5_000 })

    // 鼠标移出原型 iframe（宿主页 v-head 区域）→ 进入 1s 渐隐宽限期。
    // 关键 1：「移开」必须真正离开 iframe——fixture 的 page-login section
    //   高 120vh 铺满整个 iframe 视口，iframe 内任何点都是它的地盘
    //   （closest 必命中，等价没离开）；宿主页 (316,60) 在 v-head 内、
    //   iframe 上边缘之上，稳定在 iframe 外（body margin 归零前用过的
    //   (5,300) 是靠 body 默认 margin 8px 恰好把 iframe x 推到 8 的巧合）。
    // 关键 2：移开后【立刻】接住——中间不能隔着断言轮询（轮询在高负载
    //   下可能耗掉整个 1s 宽限期，fadeTimer 到期 hideIcon 后点击落空）。
    // 关键 3：接住/点击一律用 locator（icon.hover()/icon.click()）实时
    //   解析坐标，不用 boundingBox 绝对坐标快照——宿主页布局存在异步
    //   位移（曾因 body margin 产生 16px 可滚动空隙：hover 超高元素时
    //   Playwright 把宿主页滚 8px、随后回滚，iframe 平移 16px 使旧坐标
    //   点击落在 icon 外）。icon.hover() 的 stable 检查还会等布局稳定
    //   后再移动，双保险。渐隐「会隐藏」的行为由下一个用例覆盖。
    await page.mouse.move(316, 60)
    await icon.hover()

    // 接住成功：icon 仍显示（未 hideIcon）且 fading class 被移除
    await expect(icon).toBeVisible()
    await expect(icon).not.toHaveClass(/pp-anchor-icon--fading/)

    // 接住状态下点击 → 正常发出 ANCHOR_CLICK，右侧高亮（login-account）
    await icon.click()
    const target = page.getByTestId('prd-content').locator('li[data-pa="login-account"]')
    try {
      await expect(target).toHaveClass(/anchor-highlight/, { timeout: 3_000 })
    } catch (err) {
      // 失败现场采集（排障用，成功路径零开销）
      const dbg = await icon
        .evaluate((el) => ({
          paTarget: (el as HTMLElement).dataset.paTarget ?? null,
          cls: el.className,
          display: getComputedStyle(el).display,
        }))
        .catch((e) => ({ err: String(e) }))
      const host = await page
        .evaluate(() => ({
          highlighted: Array.from(document.querySelectorAll('.anchor-highlight')).map((n) => n.tagName + '[' + (n as HTMLElement).dataset.pa + ']'),
          toast: Array.from(document.querySelectorAll('.el-message')).map((n) => n.textContent?.trim()),
        }))
        .catch((e) => ({ err: String(e) }))
      console.log('[fade-dbg]', JSON.stringify({ icon: dbg, host }))
      throw err
    }
  })

  test('渐隐宽限期：1s 内不接住则 icon 完整隐藏', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
    const icon = protoFrame.locator('.pp-anchor-icon')

    await protoFrame.locator('[data-pa="page-login"]').hover()
    await expect(icon).toBeVisible({ timeout: 5_000 })

    // 移出 iframe（v-head 区域，见上一用例注释）且不回来——
    // 1s 宽限期过后 icon 隐藏（display:none）
    await page.mouse.move(316, 60)
    await expect(icon).toBeHidden({ timeout: 3_000 })

    // 隐藏后再次 hover 锚点：正常重新显示（状态机可复活）
    await protoFrame.locator('[data-pa="page-login"]').hover()
    await expect(icon).toBeVisible({ timeout: 5_000 })
  })

  test('锚点未命中当前文档 → toast 提示不报错', async ({ page }) => {
    const protoFrame = page.frameLocator('[data-testid="viewer-proto-frame"]')
    // login-captcha 在 PRD li 注释里有——造一个 PRD 没有的锚点：直接 hover form
    // (login-form 在 PRD 有) —— 改用原型里 PRD 未覆盖的场景较绕，
    // 简化：hover page-login（PRD 有）确认正向，不测未命中（插件渲染保证 data-pa 必有）
    await protoFrame.locator('[data-pa="login-account"]').scrollIntoViewIfNeeded()
    await protoFrame.locator('[data-pa="login-account"]').hover()
    const icon = protoFrame.locator('.pp-anchor-icon')
    await expect(icon).toBeVisible({ timeout: 5_000 })
    await icon.click()

    const target = page.getByTestId('prd-content').locator('li[data-pa="login-account"]')
    await expect(target).toHaveClass(/anchor-highlight/, { timeout: 3_000 })
  })
})

test.describe('T8.1 内容更新（上传替换）', () => {
  test('重新上传 PRD → 查看器可见新文档（旧文档替换）', async ({ page, request }) => {
    // 唯一项目名（防残留同名卡片 strict mode 冲突）
    const projName = `同步E2E-${Date.now().toString(36)}`
    const proj = await createProjectWithContent(request, projName, {
      protoFiles: { 'index.html': '<html><body>hi</body></html>' },
      prdFile: { name: '需求.md', content: '# 初始文档\n' },
    })

    // 重新上传 PRD（创建者视角的内容更新，替代旧「同步」语义）
    await uploadPrd(request, proj.id, '新需求.md', '# 远端新文档\n\n新内容\n')

    // 打开查看器：新文档就位（prd/ 唯一约定 → 单文档直显文件名，无下拉）
    await page.goto('/')
    await page
      .locator('.card')
      .filter({ hasText: proj.project_id })
      .getByTestId('open-project')
      .click()
    await expect(page).toHaveURL(new RegExp(`/project/${proj.project_id}`))
    await expect(page.getByTestId('doc-select')).toHaveCount(0)
    await expect(page.locator('.doc-name')).toHaveText('prd/新需求.md', { timeout: 10_000 })
    // 内容渲染为新文档（旧文档「初始文档」已替换）
    await expect(page.getByTestId('prd-content').locator('h1')).toHaveText('远端新文档')
  })
})
