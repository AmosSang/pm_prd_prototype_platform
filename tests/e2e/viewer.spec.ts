import { expect, test } from '@playwright/test'
import { createProjectWithContent, type ProjectInfo } from './helpers'

/**
 * T2.4 分屏查看器 E2E。
 *
 * 验收点（任务卡）：打开项目分屏可见——E2E 断言两侧加载成功；PRD 标题正确渲染。
 * 流程（T8.1 去 Git 本地化）：API 建项目 + 上传 fixture → 列表点
 * 「打开分屏查看器」→ 断言 iframe READY（bridge 上报）+ PRD h1 渲染 + 分割条拖动。
 */

const PROTO = {
  'pages/login.html': '<html><body><main data-pa="page-login">登录页</main></body></html>',
}
const PRD = {
  name: '需求.md',
  content: '# 分屏测试 PRD\n\n## 3.1 登录页 <!-- pa: page-login -->\n\n- 账号输入\n',
}

test.describe('T2.4 分屏查看器', () => {
  let proj: ProjectInfo

  test.beforeEach(async ({ request }) => {
    // 每用例独立建项目（避免用例间状态耦合）
    proj = await createProjectWithContent(request, '分屏E2E项目', {
      protoFiles: PROTO,
      prdFile: PRD,
    })
  })

  async function openViewer(page: import('@playwright/test').Page) {
    await page.goto('/')
    await page
      .locator('.card')
      .filter({ hasText: proj.project_id })
      .getByTestId('open-project')
      .click()
    await expect(page).toHaveURL(new RegExp(`/project/${proj.project_id}`))
  }

  test('打开分屏：iframe 就绪 + PRD 标题渲染', async ({ page }) => {
    await openViewer(page)

    // 左：iframe 加载且 bridge READY 上报（.ready[data-ready=true]）
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })
    const frame = page.getByTestId('viewer-proto-frame')
    await expect(frame).toBeVisible()

    // 右：PRD h1 正确渲染（markdown-it）
    await expect(page.getByTestId('prd-content').locator('h1')).toHaveText('分屏测试 PRD')
    // 锚点注释行在纯渲染骨架下不报错（正文块级元素正常渲染）
    await expect(page.getByTestId('prd-content').locator('h2')).toContainText('3.1 登录页')
  })

  test('分割条拖动：左侧宽度变化', async ({ page }) => {
    await openViewer(page)
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })

    const container = page.locator('.v-body')
    const divider = page.getByTestId('divider')
    const protoPane = page.locator('.pane.proto')

    const before = await protoPane.boundingBox()
    const containerBox = await container.boundingBox()
    expect(before).toBeTruthy()
    expect(containerBox).toBeTruthy()

    // 从分割条当前位置拖到容器 30% 处
    const startX = (await divider.boundingBox())!.x + 3
    const targetX = containerBox!.x + containerBox!.width * 0.3
    await page.mouse.move(startX, containerBox!.y + 100)
    await page.mouse.down()
    await page.mouse.move(targetX, containerBox!.y + 100, { steps: 10 })
    await page.mouse.up()

    const after = await protoPane.boundingBox()
    expect(after!.width).toBeLessThan(before!.width - 50)
  })
})
