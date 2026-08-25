import { expect, test } from '@playwright/test'
import { buildZip, createProject, type ProjectInfo } from './helpers'

/**
 * T2.3 / T8.1 / T8.2 项目创建、内容上传与删除 E2E。
 *
 * T8.1：新建项目（只填名称）→ 目录骨架 + DB 记录 → 列表出现卡片（创建者标记）。
 * T8.2：上传对话框（zip 进度条 + PRD 替换）→ 分屏联动可用；dist 壳下钻；
 * 错误提示（非 zip / 无 html）；删除项目（确认 → 卡片与目录消失）。
 */

test.describe('T8.1 项目创建', () => {
  test('新建项目成功 → 列表出现卡片（创建者标记）', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('project-card-demo')).toBeVisible()

    await page.getByTestId('new-project').click()
    await page.getByTestId('form-name').fill('E2E绑定项目')
    await page.getByTestId('form-submit').click()

    // 成功提示 + 新卡片出现（project_id 随机，用 name 定位卡片）
    await expect(page.getByText('创建成功')).toBeVisible({ timeout: 15_000 })
    const card = page.locator('.card', { hasText: 'E2E绑定项目' })
    await expect(card).toBeVisible()
    // slug 规则：中文名转 kebab 后只剩 'e2e' 前缀 + 随机后缀
    await expect(card.locator('.meta')).toContainText(/^e2e-[a-z0-9]+/i)
    // 创建者标记（当前登录用户 E2E测试员）
    await expect(card.locator('.meta')).toContainText('创建者 E2E测试员')
  })

  test('空名提交被拦截（按钮禁用）', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('new-project').click()
    await expect(page.getByTestId('form-name')).toHaveValue('')
    await expect(page.getByTestId('form-submit')).toBeDisabled()
  })
})

test.describe('T8.2 内容上传（创建者专属）', () => {
  let proj: ProjectInfo

  test.beforeEach(async ({ request, page }) => {
    proj = await createProject(request, '上传E2E项目')
    await page.goto('/')
  })

  test('UI 全链路：上传 zip（进度条）+ PRD → 分屏联动可用（任务卡验收）', async ({ page }) => {
    // 创建者卡片出现「上传内容」入口
    const card = page.locator('.card').filter({ hasText: proj.project_id })
    await card.getByTestId(`upload-${proj.project_id}`).click()
    await expect(page.locator('.el-dialog').filter({ hasText: '上传内容' })).toBeVisible()

    // 上传原型 zip（顶层 index.html + pages/ 多页结构）
    await page.getByTestId('proto-file').setInputFiles({
      name: 'proto.zip',
      mimeType: 'application/zip',
      buffer: buildZip({
        'index.html':
          '<html><body><main data-pa="page-login"><form data-pa="login-form"><input data-pa="login-account" placeholder="账号"></form></main></body></html>',
        'pages/login.html':
          '<html><body><main data-pa="page-login">登录页</main></body></html>',
      }),
    })
    await expect(page.getByText('原型上传成功')).toBeVisible({ timeout: 15_000 })
    // 进度条到 100（success 态）
    await expect(page.getByTestId('upload-progress')).toBeVisible()

    // 上传 PRD markdown
    await page.getByTestId('prd-file').setInputFiles({
      name: '需求.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from(
        '# 上传E2E PRD\n\n## 3.1 登录页 <!-- pa: page-login -->\n\n- 账号输入 <!-- pa: login-account -->：支持手机号\n',
        'utf-8',
      ),
    })
    await expect(page.getByText('PRD 上传成功')).toBeVisible({ timeout: 15_000 })

    // 关闭上传对话框 → 打开查看器 → 分屏两侧就绪（iframe READY + PRD 渲染）
    await page.keyboard.press('Escape')
    await card.getByTestId('open-project').click()
    await expect(page).toHaveURL(new RegExp(`/project/${proj.project_id}`))
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('prd-content').locator('h1')).toHaveText('上传E2E PRD')

    // 卡片显示内容更新时间（content_updated_at 维护）
    await page.goto('/')
    await expect(card.locator('.meta').filter({ hasText: '内容更新于' })).toBeVisible()
  })

  test('dist 壳下钻：zip 根无 html、唯一子目录 dist/ → 原型可直接打开', async ({ page }) => {
    const card = page.locator('.card').filter({ hasText: proj.project_id })
    await card.getByTestId(`upload-${proj.project_id}`).click()
    await expect(page.locator('.el-dialog').filter({ hasText: '上传内容' })).toBeVisible()

    await page.getByTestId('proto-file').setInputFiles({
      name: 'dist.zip',
      mimeType: 'application/zip',
      buffer: buildZip({
        'dist/index.html':
          '<html><body><main data-pa="page-login">构建产物首页</main></body></html>',
      }),
    })
    await expect(page.getByText('原型上传成功')).toBeVisible({ timeout: 15_000 })
    await page.keyboard.press('Escape')

    // 查看器加载下钻后的 index.html（dist 壳被剥掉）
    await card.getByTestId('open-project').click()
    await expect(page).toHaveURL(new RegExp(`/project/${proj.project_id}`))
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })
  })

  test('macOS Finder 压缩包：__MACOSX/.DS_Store 垃圾不干扰下钻（用户报障场景）', async ({ page }) => {
    const card = page.locator('.card').filter({ hasText: proj.project_id })
    await card.getByTestId(`upload-${proj.project_id}`).click()

    // Finder「压缩」产物的真实形态：__MACOSX 资源目录 + .DS_Store + ._ 资源 fork
    await page.getByTestId('proto-file').setInputFiles({
      name: '原型.zip',
      mimeType: 'application/zip',
      buffer: buildZip({
        'prototype/': '',
        'prototype/index.html':
          '<html><body><main data-pa="page-login">Mac 压缩原型</main></body></html>',
        'prototype/.DS_Store': 'junk',
        '__MACOSX/': '',
        '__MACOSX/prototype/': '',
        '__MACOSX/prototype/._index.html': 'resource-fork junk',
        '.DS_Store': 'junk',
      }),
    })
    await expect(page.getByText('原型上传成功')).toBeVisible({ timeout: 15_000 })
    await page.keyboard.press('Escape')

    // 下钻 prototype/ 壳成功（垃圾被忽略，唯一内容子目录判定不受干扰）
    await card.getByTestId('open-project').click()
    await expect(page).toHaveURL(new RegExp(`/project/${proj.project_id}`))
    await expect(page.locator('.ready[data-ready="true"]')).toBeVisible({ timeout: 15_000 })
  })

  test('错误提示：非 zip 文件前端拦截；无 html 包后端拒绝（中文提示）', async ({ page }) => {
    const card = page.locator('.card').filter({ hasText: proj.project_id })
    await card.getByTestId(`upload-${proj.project_id}`).click()

    // 非 zip：前端格式校验（ElMessage 中文提示）
    await page.getByTestId('proto-file').setInputFiles({
      name: 'a.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('not a zip'),
    })
    await expect(page.getByText('原型包必须是 zip 格式')).toBeVisible()

    // zip 内无 html：后端 400 + 中文提示
    await page.getByTestId('proto-file').setInputFiles({
      name: 'empty.zip',
      mimeType: 'application/zip',
      buffer: buildZip({ 'style.css': 'body{}' }),
    })
    await expect(page.getByText(/未找到 HTML/)).toBeVisible({ timeout: 15_000 })

    // 失败后可重新选择上传（input 已清空——同名文件可重选）
    await page.getByTestId('proto-file').setInputFiles({
      name: 'empty.zip',
      mimeType: 'application/zip',
      buffer: buildZip({ 'index.html': '<html><body>ok</body></html>' }),
    })
    await expect(page.getByText('原型上传成功')).toBeVisible({ timeout: 15_000 })
  })
})

test.describe('T8.2 删除项目（创建者专属）', () => {
  test('确认删除 → 卡片消失 + 目录清除', async ({ page, request }) => {
    const proj = await createProject(request, '待删E2E项目')
    await page.goto('/')

    const card = page.locator('.card').filter({ hasText: proj.project_id })
    await expect(card).toBeVisible()
    await card.getByTestId(`del-${proj.project_id}`).click()

    // ElMessageBox 确认（box 内「删除」按钮，区别于卡片按钮）
    await page.locator('.el-message-box').getByRole('button', { name: '删除' }).click()
    await expect(page.getByText('已删除')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.card', { hasText: proj.project_id })).toHaveCount(0)
  })

  test('取消删除 → 项目保留', async ({ page, request }) => {
    const proj = await createProject(request, '取消删除E2E项目')
    await page.goto('/')

    const card = page.locator('.card').filter({ hasText: proj.project_id })
    await card.getByTestId(`del-${proj.project_id}`).click()
    await page.locator('.el-message-box').getByRole('button', { name: '取消' }).click()
    await expect(page.locator('.card', { hasText: proj.project_id })).toBeVisible()
  })
})
