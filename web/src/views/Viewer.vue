<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { anchorPlugin } from '../anchor-plugin'
import { currentUser } from '../auth'
import { PROTO_ORIGIN } from '../proto-origin'
import CommentBox from '../components/CommentBox.vue'
import CommentDrawer from '../components/CommentDrawer.vue'
import {
  createComment,
  getOverview,
  getPrd,
  getReconcile,
  listComments,
  listProjects,
  updateProject,
  uploadPrototype,
  uploadPrd,
  uploadShot,
  type CommentItem,
  type CommentPayload,
  type CreateCommentResult,
  type HighlightRect,
  type ProjectOverview,
  type ReconcileDetail,
} from '../projects'

/**
 * T2.4 分屏查看器骨架 + T3.1 锚点正向联动 + T3.2 反向联动 + T3.3 对账
 * + T4.1 评论模式采集 + T4.2 评论框与提交链路。
 * 左：原型 iframe（:8081 独立 origin + sandbox + URL nonce，bridge.js 由代理注入）
 * 右：PRD markdown 渲染（markdown-it + 锚点插件，多文档 el-select 切换）
 * 中：可拖动分割条（pointer 事件，父级相对宽度）
 *
 * T3.1 正向联动：bridge 上报 ANCHOR_CLICK（点原型锚点 icon ◈）→
 * 右侧 [data-pa] 元素 scrollIntoView + 高亮 2s（anchor-highlight class）。
 * T3.2 反向联动：文档段落 hover「定位」按钮 → 查页面地图（锚点 → 原型文件）
 * → 跨页先切 iframe src（等 READY）→ 发 HIGHLIGHT_ANCHOR（bridge 滚动+闪烁）。
 * 跨文档：锚点不在当前文档时切文档再定位（正向反向共用）。
 * T3.3 对账：顶栏提示条（匹配 · 缺失 · 未描述），点击拉明细弹窗
 * （三态 + 重复 ID + 页面地图坏引用，数据来自服务端静态解析）。
 * T4.1 评论模式：顶栏开关 → SET_COMMENT_MODE；bridge 采集 ELEMENT_SELECTED
 * payload。iframe 重载（切页）后 READY 时重发开关保持状态；ROUTE_CHANGE 更新
 * SPA 当前路由显示。
 * T4.2 评论框：三类入口（DOM 点击 / 「评论本页」COLLECT_PAGE / 文档段落
 * 「评论」按钮）→ CommentBox 填写 → 提交时经 bridge 截图（先临时关模式清
 * hover 蓝框）→ /shots 上传 → POST /comments（DB + reviews/ 落仓）。
 */

const route = useRoute()
const slug = route.params.slug as string

function makeNonce(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}
const nonce = makeNonce()

const overview = ref<ProjectOverview | null>(null)
const loadError = ref('')

const currentDoc = ref('')
const prdHtml = ref('')
const docLoading = ref(false)

// 原型入口（默认第一个 entry）
const currentEntry = ref('')
const iframeSrc = computed(() =>
  currentEntry.value
    ? `${PROTO_ORIGIN}/proto/${slug}/${currentEntry.value}#pp-nonce=${nonce}`
    : '',
)

const sandboxAttr = 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox'
const ready = ref(false)
const anchorCount = ref(0) // 本页锚点数（ANCHOR_REPORT 更新，右上角显示）

// ───────────────────────── T4.5 项目级「可评论」开关 ─────────────────────────
// 产品方案 §4.5：默认开启；关闭后全员评论入口置灰（已有评论仍可查看——
// 抽屉/角标不受影响）。PM 驱动 Agent 修改前关闭、同步刷新后再开启，
// 消除 reviews/ 双写窗口。POST /comments 服务端已兜底拦截（T4.2）。
const commentable = ref(true)

async function onCommentableChange(on: string | number | boolean) {
  if (!overview.value) return
  const val = !!on
  try {
    const p = await updateProject(overview.value.project.id, { commentable: val })
    overview.value.project = p
    commentable.value = val
    if (!val) {
      // 联动关评论模式（入口置灰由 :disabled 与模式关闭共同保证），
      // 收起可能开着的评论框
      if (commentMode.value) onCommentModeChange(false)
      capturedPayload.value = null
      resetCommentBox()
    }
    ElMessage.success(val ? '已开启评论' : '已关闭评论（已有评论仍可查看）')
  } catch (e) {
    commentable.value = !val // 失败回滚开关 UI
    ElMessage.error(e instanceof Error ? e.message : '操作失败')
  }
}

// ───────────────────── T8.3/T8.2 创建者工具区（导出 + 上传，仅创建者） ─────────────────────
// 产品方案 §4.7：导出全部评论（归档/周报）或所有已确认待修改（交付修改范围）；
// 上传原型 zip / PRD md 走 T8.2 创建者专属接口，成功后刷新 overview 与对账。
function onExport(scope: string) {
  const id = overview.value?.project.id
  if (!id || (scope !== 'all' && scope !== 'confirmed')) return
  window.open(`/api/projects/${id}/comments/export?scope=${scope}`)
}

/** 创建者工具区下拉命令：export_all / export_confirmed / up_proto / up_prd */
function onCreatorTool(cmd: string) {
  if (cmd.startsWith('export_')) {
    onExport(cmd === 'export_all' ? 'all' : 'confirmed')
    return
  }
  if (cmd === 'up_proto') protoInput.value?.click()
  else if (cmd === 'up_prd') prdInput.value?.click()
}

const protoInput = ref<HTMLInputElement | null>(null)
const prdInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)

async function onProtoPicked(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  const id = overview.value?.project.id
  if (!f || !id) return
  if (!f.name.toLowerCase().endsWith('.zip')) {
    ElMessage.error('原型包必须是 zip 格式')
    return
  }
  uploading.value = true
  try {
    await uploadPrototype(id, f)
    ElMessage.success('原型上传成功，查看器已刷新')
    await reloadOverview() // 刷新 overview（原型入口/对账/锚点）
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '上传失败')
  } finally {
    uploading.value = false
    if (protoInput.value) protoInput.value.value = ''
  }
}

async function onPrdPicked(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  const id = overview.value?.project.id
  if (!f || !id) return
  if (!f.name.toLowerCase().endsWith('.md')) {
    ElMessage.error('仅支持 markdown 文档')
    return
  }
  uploading.value = true
  try {
    await uploadPrd(id, f)
    ElMessage.success('PRD 上传成功，查看器已刷新')
    await reloadOverview()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '上传失败')
  } finally {
    uploading.value = false
    if (prdInput.value) prdInput.value.value = ''
  }
}

// ───────────────────────── T4.1/T4.2 评论模式与评论框 ─────────────────────────
const commentMode = ref(false)
const capturedPayload = ref<CommentPayload | null>(null)
const currentRoute = ref('') // ROUTE_CHANGE 上报的 SPA 当前路由（页面 + hash）

// 评论框状态（T4.2）
const submitting = ref(false)
const submittedResult = ref<CreateCommentResult | null>(null)
const submitError = ref('')
const shotPreviewUrl = ref('') // 截图预览（临时区 URL；doc_block 无）

/** 重置评论框到「填写中」（换目标 / 重新打开时）。 */
function resetCommentBox() {
  submittedResult.value = null
  submitError.value = ''
  shotPreviewUrl.value = ''
}

function postSetCommentMode(enabled: boolean) {
  // targetOrigin '*'：同 postHighlight 的沙箱约束（不透明 origin 为 "null"）
  iframeEl.value?.contentWindow?.postMessage(
    { type: 'SET_COMMENT_MODE', enabled, nonce },
    '*',
  )
}

/** 评论模式开关切换：同步 bridge + 关评论框（新旧会话不混淆）。 */
function onCommentModeChange(on: string | number | boolean) {
  commentMode.value = !!on
  capturedPayload.value = null
  resetCommentBox()
  postSetCommentMode(!!on)
}

/** 关闭评论框（取消 / 完成按钮）。 */
function closeCommentBox() {
  capturedPayload.value = null
  resetCommentBox()
}

/** 「评论本页」按钮（页面评论入口，产品方案 §4.5）：请求 bridge 采集
 * 页面根 payload（COLLECT_PAGE → ELEMENT_SELECTED 统一入口弹评论框）。 */
function commentPage() {
  if (!commentMode.value || !ready.value) return
  iframeEl.value?.contentWindow?.postMessage({ type: 'COLLECT_PAGE', nonce }, '*')
}

// ───────────────────────── 截图链路（T4.2，复用 T1.2）─────────────────
// 提交时截图（非打开评论框时）：保证反映提交一刻状态（技术方案 §2.3）。
// Promise 化：requestId 关联的 SCREENSHOT_RESULT / SCREENSHOT_ERROR；
// 超时或失败返回 null（降级为无截图提交）。
interface ScreenshotResult {
  blob: Blob
  highlight: HighlightRect | null
}
const screenshotWaiters = new Map<
  string,
  { resolve: (v: ScreenshotResult | null) => void }
>()

function takeScreenshot(
  requestId: string,
  cssPath: string | null,
  timeoutMs = 30000,
): Promise<ScreenshotResult | null> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      screenshotWaiters.delete(requestId)
      resolve(null)
    }, timeoutMs)
    screenshotWaiters.set(requestId, {
      resolve: (v) => {
        clearTimeout(timer)
        resolve(v)
      },
    })
    iframeEl.value?.contentWindow?.postMessage(
      { type: 'TAKE_SCREENSHOT', requestId, cssPath, nonce },
      '*',
    )
  })
}

/** 提交评论：截图 → 上传 → POST /comments（DB + reviews/ 落仓）。 */
async function submitComment(form: { content: string; priority: string; scope: string }) {
  const payload = capturedPayload.value
  if (!payload || !overview.value || submitting.value) return
  submitting.value = true
  submitError.value = ''
  const isDoc = payload.target_type === 'doc_block'
  let shotId = ''
  let rect: HighlightRect | null = null
  try {
    if (!isDoc && ready.value) {
      // 截图前临时关评论模式：清掉 hover 蓝框（否则被截进图），截完恢复。
      // 开关 UI 状态不变（commentMode ref 不动，只动 bridge 侧）
      postSetCommentMode(false)
      const reqId = 'shot-' + Math.random().toString(36).slice(2, 10)
      // page 评论红框=整页无意义，不传 cssPath（bridge 整页无框）；
      // doc_block 评论不截图（目标是 PRD 段落，非原型）
      const shot = await takeScreenshot(
        reqId,
        payload.target_type === 'page' ? null : payload.css_path,
      )
      if (shot) {
        rect = shot.highlight
        // 截图临时区目录口径 = project_id（slug，与 /data/repos 同名）——
        // 提交时后端按 slug 找文件，两处必须一致（曾用数字主键导致 400）
        await uploadShot(overview.value.project.project_id, shot.blob, reqId, shot.highlight)
        shotId = reqId
        shotPreviewUrl.value = `/api/shots/${overview.value.project.project_id}/${reqId}.png`
      } else {
        ElMessage.warning('截图生成失败，本条评论将不含截图')
      }
      if (commentMode.value) postSetCommentMode(true)
    }
    const res = await createComment(overview.value.project.id, {
      payload,
      content: form.content,
      priority: form.priority,
      scope: form.scope,
      shot_id: shotId || undefined,
      highlight_rect: rect || undefined,
    })
    // T8.1 去 Git 本地化：评论直写项目目录，提交即落文件（无异步队列）
    submittedResult.value = res
    refreshComments() // T4.4：更新抽屉数据 + 原型角标
  } catch (e) {
    submitError.value = e instanceof Error ? e.message : '提交失败，请重试'
    if (!isDoc && commentMode.value) postSetCommentMode(true)
  } finally {
    submitting.value = false
  }
}

function onMessage(event: MessageEvent) {
  const msg = event.data || {}
  if (event.origin !== PROTO_ORIGIN && event.origin !== 'null') return
  if (msg.nonce !== nonce) return
  if (msg.type === 'READY') {
    ready.value = true
    // 评论模式 sticky：iframe 重载（切页/重导航）后 bridge 内存状态归零，
    // 开关若开着须重发（否则新页点击不采集）
    if (commentMode.value) postSetCommentMode(true)
    // 角标 sticky：切页后重发当前页角标（评论数据已在宿主内存）
    syncBadges()
    // 跨页定位：切页后等 READY 再发 HIGHLIGHT_ANCHOR（技术方案 §2.5）
    if (pendingHighlight.value) {
      const target = pendingHighlight.value
      pendingHighlight.value = null
      postHighlight(target)
    }
  }
  if (msg.type === 'ANCHOR_REPORT' && Array.isArray(msg.anchors)) {
    anchorCount.value = msg.anchors.length
  }
  if (msg.type === 'ANCHOR_CLICK' && typeof msg.anchorId === 'string') {
    jumpToDocAnchor(msg.anchorId)
  }
  if (msg.type === 'HIGHLIGHT_ACK' && msg.hit === false) {
    ElMessage.info(`锚点「${msg.anchorId}」在原型中缺失`)
  }
  if (msg.type === 'ELEMENT_SELECTED' && msg.payload) {
    // 采集到目标（DOM 点击 / COLLECT_PAGE）：打开评论框。
    // 填写中换目标 → 重置表单（CommentBox watch payload）
    capturedPayload.value = msg.payload as CommentPayload
    resetCommentBox()
  }
  if (msg.type === 'SCREENSHOT_RESULT' && typeof msg.requestId === 'string') {
    const w = screenshotWaiters.get(msg.requestId)
    if (w) {
      screenshotWaiters.delete(msg.requestId)
      w.resolve(
        msg.blob instanceof Blob
          ? { blob: msg.blob, highlight: (msg.highlight as HighlightRect) || null }
          : null,
      )
    }
  }
  if (msg.type === 'SCREENSHOT_ERROR' && typeof msg.requestId === 'string') {
    const w = screenshotWaiters.get(msg.requestId)
    if (w) {
      screenshotWaiters.delete(msg.requestId)
      w.resolve(null)
    }
  }
  if (msg.type === 'ROUTE_CHANGE') {
    // route 含 hash（SPA 视角完整）；旧消息无 route 时退回 page
    currentRoute.value =
      (typeof msg.route === 'string' && msg.route) || String(msg.page || '')
  }
  if (msg.type === 'COMMENT_BADGE_CLICK' && typeof msg.anchorId === 'string') {
    // T4.4：点原型角标 → 打开抽屉看该位置评论
    drawerOpen.value = true
    refreshComments()
  }
}

// ───────────────────────── T4.4 评论抽屉 + 原型角标 ─────────────────────────
const drawerOpen = ref(false)
const comments = ref<CommentItem[]>([])
const drawerFocusKey = ref('') // 文档角标点击 → 抽屉定位到的位置 key

async function refreshComments() {
  if (!overview.value) return
  try {
    comments.value = await listComments(overview.value.project.id)
  } catch {
    /* 抽屉打开失败不打断主流程，下次再拉 */
  }
  syncBadges()
}

function toggleDrawer() {
  drawerOpen.value = !drawerOpen.value
  if (drawerOpen.value) refreshComments()
}

/** doc_block 评论的位置 key（与 CommentDrawer.locKey doc 分支同口径：
 * doc_file + 锚点或标题路径——文档段落角标匹配、抽屉定位共用）。 */
function docLocKeyOf(c: CommentItem): string {
  return (
    'doc|' +
    (c.payload.doc_file || '') +
    '|' +
    (c.payload.doc_anchor_id || c.payload.doc_path || '')
  )
}

/** ── 评论定位（T4.4，每条评论的「定位」按钮）──
 * doc_block → 切到评论所在文档，锚点/文本匹配段落高亮 2s；
 * dom/page → 切到评论所在原型页（等 READY），锚点或 css_path 闪烁
 * （page 评论 cssPath='body' 整页闪烁）。 */
async function locateComment(c: CommentItem) {
  if (c.target_type === 'doc_block') {
    const docFile = (c.payload.doc_file as string) || ''
    const flash = () => {
      const anchorId = (c.payload.doc_anchor_id as string) || ''
      let el = anchorId ? findDocAnchor(anchorId) : null
      if (!el) {
        // 无锚点段落：按 doc_excerpt 文本匹配块级元素（采集时同款文本）
        el = findDocBlockByExcerpt((c.payload.doc_excerpt as string) || '')
      }
      if (el) flashDocAnchor(el)
      else ElMessage.info('评论对应的文档段落未找到（内容可能已被修改）')
    }
    if (docFile && docFile !== currentDoc.value && overview.value?.docs.includes(docFile)) {
      currentDoc.value = docFile
      await nextTick()
      await new Promise((r) => setTimeout(r, 80))
    }
    flash()
    return
  }
  // dom/page：评论存的是剥掉 prototype/ 前缀的页面口径
  const entry = overview.value?.proto_entries.find(
    (e) => e === 'prototype/' + (c.prototype_page || ''),
  )
  const targetEntry = entry || currentEntry.value
  if (!targetEntry) return
  const target: HighlightTarget =
    c.target_type === 'page'
      ? { cssPath: 'body' }
      : c.anchor_id
        ? { anchorId: c.anchor_id }
        : { cssPath: (c.payload.css_path as string) || 'body' }
  if (targetEntry !== currentEntry.value) {
    pendingHighlight.value = target
    ready.value = false
    currentEntry.value = targetEntry
  } else {
    postHighlight(target)
  }
}

/** 按 doc_excerpt 匹配文档块级元素（无锚点 doc 评论定位）。 */
function findDocBlockByExcerpt(excerpt: string): HTMLElement | null {
  if (!excerpt) return null
  const needle = excerpt.replace(/…$/, '')
  const container = document.querySelector('[data-testid="prd-content"]')
  const blocks = container?.querySelectorAll<HTMLElement>(DOC_BLOCK_SELECTOR) || []
  for (const b of blocks) {
    const text = (b.textContent || '').replace(/\s+/g, ' ').trim()
    if (text && text.startsWith(needle)) return b
  }
  return null
}

/** ── 文档段落评论数量角标（T4.4，产品需求：hover 段落时「评论」按钮附近
 * 固定显示数量角标；点击打开抽屉并定位到该位置的评论组）── */
const docBadge = ref({ count: 0, locKey: '' })
const docBadgeEl = ref<HTMLElement | null>(null)

/** hover 段落时更新文档角标（在 onDocMouseover 里调用）。 */
function updateDocBadge(host: Element) {
  const anchorId = firstAnchorOf(host) // 多锚点区块取第一个锚点归属角标
  const key = 'doc|' + currentDoc.value + '|' + (anchorId || headingPathOf(host))
  const n = comments.value.filter(
    (c) => c.target_type === 'doc_block' && docLocKeyOf(c) === key,
  ).length
  docBadge.value = { count: n, locKey: key }
  if (!n) return
  // 角标定位：段落右上角（相对 prd-scroll 容器，随滚动正确）
  nextTick(() => {
    const scrollEl = host.closest('.prd-scroll')
    const badgeEl = docBadgeEl.value
    if (!scrollEl || !badgeEl) return
    const hr = host.getBoundingClientRect()
    const sr = scrollEl.getBoundingClientRect()
    badgeEl.style.left = hr.right - sr.left + scrollEl.scrollLeft - 30 + 'px'
    badgeEl.style.top = hr.top - sr.top + scrollEl.scrollTop - 8 + 'px'
  })
}

/** 点击文档角标：打开抽屉 + 定位到该位置评论组（展开+滚动+高亮）。 */
function clickDocBadge() {
  if (!docBadge.value.count) return
  drawerOpen.value = true
  refreshComments().then(() => {
    drawerFocusKey.value = docBadge.value.locKey
  })
}

/** 角标下发：当前页（entry 剥 prototype/ 前缀对齐评论 prototype_page 口径）
 * 的锚点 → 未删除评论数（含 doc_block 之外的原型评论；doc_block 无原型角标）。 */
function syncBadges() {
  if (!ready.value || !overview.value) return
  const page = currentEntry.value.replace(/^prototype\//, '')
  const counts: Record<string, number> = {}
  for (const c of comments.value) {
    if (c.target_type === 'doc_block') continue
    if (c.prototype_page !== page) continue
    const anchor = c.anchor_id || c.payload.nearest_anchor_id || ''
    if (anchor) counts[anchor] = (counts[anchor] || 0) + 1
  }
  iframeEl.value?.contentWindow?.postMessage(
    { type: 'SET_COMMENT_BADGES', badges: counts, nonce },
    '*',
  )
}

// ───────────────────────── 锚点联动（T3.1 正向 / T3.2 反向）─────────────
// 正向：点原型锚点 icon → 右侧文档滚动到 [data-pa=id] 段落 + 高亮 2s。
// 反向：点文档「定位」→ 查页面地图 → 跨页先切 iframe（等 READY）→ 发
// HIGHLIGHT_ANCHOR（bridge 滚动+闪烁）。
let highlightTimer: ReturnType<typeof setTimeout> | null = null

/** 文档容器内查锚点元素（当前已渲染的文档）。data-pa 可能为多锚点（空格分隔），
 * 用 `~=` 做单词匹配：单锚点与多锚点都能命中。 */
function findDocAnchor(anchorId: string): HTMLElement | null {
  const container = document.querySelector<HTMLElement>('[data-testid="prd-content"]')
  return container?.querySelector<HTMLElement>(`[data-pa~="${cssEscape(anchorId)}"]`) || null
}

/** 取块级宿主上的「第一个」锚点 ID（data-pa 空格分隔多个；单锚点即其本身）。 */
function firstAnchorOf(host: Element): string {
  return (host.getAttribute('data-pa') || '').split(/\s+/).filter(Boolean)[0] || ''
}

/** 正向联动：滚动 + 高亮当前文档的锚点元素。 */
function flashDocAnchor(el: HTMLElement) {
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  if (highlightTimer) clearTimeout(highlightTimer)
  document.querySelectorAll('.anchor-highlight').forEach((n) => n.classList.remove('anchor-highlight'))
  el.classList.add('anchor-highlight')
  highlightTimer = setTimeout(() => el.classList.remove('anchor-highlight'), 2000)
}

/** 正向联动入口（ANCHOR_CLICK）。当前文档未命中 → 跨文档切换后定位。 */
async function jumpToDocAnchor(anchorId: string) {
  const el = findDocAnchor(anchorId)
  if (el) {
    flashDocAnchor(el)
    return
  }
  // 跨文档：扫所有文档找含该锚点注释的（API 拉原文正则匹配）
  if (!overview.value) return
  for (const doc of overview.value.docs) {
    if (doc === currentDoc.value) continue
    try {
      const res = await getPrd(overview.value.project.id, doc)
      if (new RegExp(`<!--\\s*pa:\\s*${anchorId}\\s*-->`).test(res.content)) {
        currentDoc.value = doc
        // 等 watch 触发的 loadDoc 完成渲染后再定位（下一轮微任务+重绘）
        await nextTick()
        await new Promise((r) => setTimeout(r, 50))
        const target = findDocAnchor(anchorId)
        if (target) flashDocAnchor(target)
        return
      }
    } catch {
      /* 单个文档拉取失败继续找下一个 */
    }
  }
  ElMessage.info(`锚点「${anchorId}」在文档中未找到`)
}

/** CSS.escape 兜底（锚点 ID 契约是 kebab-case，正常不会需要转义） */
function cssEscape(s: string): string {
  return window.CSS && CSS.escape ? CSS.escape(s) : s
}

// ───────────────────────── T3.2 反向联动 ─────────────────────────
// 文档段落 hover「定位」按钮 → 原型侧滚动闪烁。跨页：查页面地图（页面锚点
// → 原型文件）先切 iframe src，等 READY 后发 HIGHLIGHT_ANCHOR。
// T4.4 评论定位共用：目标可为 {anchorId}（锚点）或 {cssPath}（无锚点
// dom 评论的 css_path / page 评论的 'body' 整页闪烁）。
interface HighlightTarget {
  anchorId?: string
  cssPath?: string
}
const pendingHighlight = ref<HighlightTarget | null>(null)

function postHighlight(target: HighlightTarget) {
  // targetOrigin '*'：sandbox iframe（无 allow-same-origin）的 origin 是
  // 不透明 "null"，指定具体 origin 会被浏览器拒发。安全靠 bridge 侧
  // origin + nonce 双重校验（技术方案 §2.2，与反向消息同规约）
  iframeEl.value?.contentWindow?.postMessage(
    { type: 'HIGHLIGHT_ANCHOR', anchorId: target.anchorId, cssPath: target.cssPath, nonce },
    '*',
  )
}

/** 反向联动入口：文档「定位」按钮点击。 */
function locateAnchor(anchorId: string) {
  if (!overview.value) return
  // 目标原型文件三级查找：页面地图（页面锚点）→ 锚点索引（组件锚点）→ 当前页
  const mapHit = overview.value.page_map.find((e) => e.anchor === anchorId)
  const indexHit = overview.value.proto_anchor_index[anchorId]
  const targetEntry = mapHit?.proto || indexHit || currentEntry.value
  if (!targetEntry) {
    ElMessage.info('项目内未发现原型页面')
    return
  }
  if (targetEntry !== currentEntry.value) {
    // 跨页：切 src（iframe 重载 → bridge READY → onMessage 里补发定位）
    pendingHighlight.value = { anchorId }
    ready.value = false
    currentEntry.value = targetEntry
  } else {
    postHighlight({ anchorId })
  }
}

/** 文档侧「定位/评论」按钮 hover 显示：事件委托 mouseover/mouseout。
 * T4.2 修订：宿主泛化到任意块级元素（p/li/h1-h6/table/blockquote/pre）——
 * 任意段落都可评论（无锚点段落用指纹定位，产品方案 §3.3）；
 * 「定位」按钮仍只对 [data-pa] 宿主显示（CSS 选择器限定，无锚点无处可跳）。 */
const iframeEl = ref<HTMLIFrameElement | null | undefined>(undefined)

const DOC_BLOCK_SELECTOR = 'h1, h2, h3, h4, h5, h6, p, li, table, blockquote, pre'

function docHostOf(target: EventTarget | null): Element | null {
  return (target as HTMLElement)?.closest?.(DOC_BLOCK_SELECTOR) || null
}

function onDocMouseover(e: MouseEvent) {
  const host = docHostOf(e.target)
  if (!host) return
  host.classList.add('pa-locate-hover')
  updateDocBadge(host) // T4.4 文档段落评论数量角标
}

function onDocMouseout(e: MouseEvent) {
  const host = docHostOf(e.target)
  if (!host) return
  host.classList.remove('pa-locate-hover')
}

/** 段落点击（委托）：点击落在顶部按钮区（伪元素区域，坐标判断）才触发——
 * 左上「定位」（T3.2，::before 在 left:8px，仅 [data-pa] 宿主）或右上
 * 「评论」（T4.2，::after 在 right:8px，任意块级宿主、评论模式开时）；
 * 点段落其他位置不误触。 */
/** T 增强：多锚点区块点击「定位」→ 弹出锚点 ID 列表供选择（按宿主位置固定浮层）。 */
const anchorMenu = ref({ visible: false, x: 0, y: 0, ids: [] as string[] })

function openAnchorMenu(host: Element, ids: string[]) {
  const rect = host.getBoundingClientRect()
  anchorMenu.value = { visible: true, x: rect.left, y: rect.top, ids }
}

function pickAnchor(id: string) {
  anchorMenu.value.visible = false
  locateAnchor(id)
}

function onDocClick(e: MouseEvent) {
  const host = docHostOf(e.target)
  if (!host || !host.classList.contains('pa-locate-hover')) return
  // 顶部按钮区：段落顶部 28px 内（按钮在 top:-10px 高 20px）
  const rect = host.getBoundingClientRect()
  if (e.clientY > rect.top + 28) return
  e.preventDefault()
  const anchorIds = (host.getAttribute('data-pa') || '').split(/\s+/).filter(Boolean)
  if (e.clientX <= rect.left + 60) {
    // 左上「定位」（仅锚点宿主；无锚点不显示该按钮，点了也不响应）
    if (anchorIds.length > 1) {
      openAnchorMenu(host, anchorIds)
      return
    }
    if (anchorIds.length === 1) locateAnchor(anchorIds[0])
    return
  }
  if (commentMode.value && e.clientX >= rect.right - 64) {
    // 右上「评论」：文档段落评论（doc_block，目标为该 PRD 块级元素）
    openDocComment(host)
  }
}

/** 段落的标题路径（与服务端 extract_prd_anchors 的 doc_path 同口径：
 * h2 起的标题链，"/" 连接）。markdown 渲染的 DOM 是扁平文档流——标题不是
 * 段落祖先，须向前遍历兄弟收集标题，再按文档序建标题栈（同级兄弟替换、
 * 更浅级别截断）。 */
function headingPathOf(host: Element): string {
  const found: { level: number; text: string }[] = []
  let node = host.previousElementSibling
  while (node) {
    const m = /^H([1-6])$/.exec(node.tagName)
    if (m) found.push({ level: Number(m[1]), text: (node.textContent || '').trim() })
    node = node.previousElementSibling
  }
  found.reverse() // 文档序（远 → 近）
  const stack: { level: number; text: string }[] = []
  for (const h of found) {
    while (stack.length && stack[stack.length - 1].level >= h.level) stack.pop()
    stack.push(h)
  }
  // h1 是文档题不进链（同服务端口径）
  return stack
    .filter((s) => s.level >= 2)
    .map((s) => s.text)
    .join('/')
}

/** 文档段落评论入口（T4.2）：构造 doc_block payload 打开评论框。
 * 原型侧字段全空（无原型定位，schema 允许）；有锚点段落带 doc_anchor_id
 * （服务端复核 PRD 锚点）；无锚点段落 doc_anchor_id 空、doc_excerpt +
 * doc_path（标题路径）现采——服务端据此算内容指纹定位段落。 */
function openDocComment(host: Element) {
  const anchorId = firstAnchorOf(host) // 多锚点区块取第一个锚点
  const text = (host.textContent || '').replace(/\s+/g, ' ').trim()
  const excerpt = text.length > 200 ? text.slice(0, 200) + '…' : text
  resetCommentBox()
  capturedPayload.value = {
    target_type: 'doc_block',
    prototype_page: '',
    anchor_id: '',
    nearest_anchor_id: '',
    css_path: '',
    outer_html: '',
    text_excerpt: excerpt,
    interaction_state: { modal_open: false, viewport: '0x0', scroll_y: 0, route: '' },
    doc_anchor_id: anchorId,
    doc_excerpt: excerpt,
    doc_path: headingPathOf(host),
    // 当前文档路径：无锚点评论的定位/文档角标匹配依据（服务端落 doc_file）
    doc_file: currentDoc.value,
  }
}

const md = new MarkdownIt({ html: true, linkify: true })
md.use(anchorPlugin)

async function loadDoc(file: string) {
  if (!file || !overview.value) return
  docLoading.value = true
  try {
    const res = await getPrd(overview.value.project.id, file)
    prdHtml.value = md.render(res.content)
  } finally {
    docLoading.value = false
  }
}

watch(currentDoc, (f) => loadDoc(f))

// ───────────────────────── T3.3 对账提示条 + 明细弹窗 ─────────────────────────
const reconDetail = ref<ReconcileDetail | null>(null)
const reconDialogVisible = ref(false)
const reconLoading = ref(false)
const reconTab = ref('missing')

/** 打开明细弹窗：拉 /reconcile 全量明细。 */
async function openReconcile() {
  if (!overview.value) return
  reconDialogVisible.value = true
  reconLoading.value = true
  try {
    reconDetail.value = await getReconcile(overview.value.project.id)
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '对账明细加载失败')
    reconDialogVisible.value = false
  } finally {
    reconLoading.value = false
  }
}

const reconSummary = computed(() => overview.value?.reconcile_summary || null)
/** 有失配（任一附加检查非零）时提示条才可点/高亮 */
const reconHasIssue = computed(() => {
  const s = reconSummary.value
  if (!s) return false
  return (
    s.missing_in_proto > 0 ||
    s.undescribed > 0 ||
    s.duplicate_prd > 0 ||
    s.duplicate_proto > 0 ||
    s.map_broken > 0
  )
})

// ───────────────────────── 分割条拖动（T8.6 三栏：原型|文档|评论） ─────────────────────────
// 两把分割条：proto（原型|文档）+ drawer（文档|评论）。三栏宽度百分比，
// pointer 捕获 + 全局 move/up。评论抽屉关闭时 drawerPct 不参与，文档占剩余。
const protoPct = ref(50)
const drawerPct = ref(28)
const dragging = ref<'proto' | 'drawer' | null>(null)
const containerEl = ref<HTMLElement | null>(null)

/** 文档（中间栏）宽度：100 - 原型 - 评论（抽屉关闭时评论占位 0，文档填满剩余）。 */
const prdPct = computed(() => Math.max(15, 100 - protoPct.value - (drawerOpen.value ? drawerPct.value : 0)))

function onDividerDown(e: PointerEvent, kind: 'proto' | 'drawer') {
  dragging.value = kind
  // pointer 捕获：拖出分割条后仍持续收到 pointermove（不丢拖拽）
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}
function onDividerMove(e: PointerEvent) {
  if (!dragging.value || !containerEl.value) return
  const rect = containerEl.value.getBoundingClientRect()
  if (dragging.value === 'proto') {
    protoPct.value = Math.min(70, Math.max(15, ((e.clientX - rect.left) / rect.width) * 100))
  } else if (dragging.value === 'drawer') {
    drawerPct.value = Math.min(45, Math.max(15, ((rect.right - e.clientX) / rect.width) * 100))
  }
}
function onDividerUp() {
  dragging.value = null
}

/** 按 slug 反查项目并加载 overview，同步重置入口/文档/开关/评论（T8.2 上传后复用）。 */
async function loadOverview() {
  const all = await listProjects()
  const hit = all.find((p) => p.project_id === slug)
  if (!hit) throw new Error('项目不存在')
  overview.value = await getOverview(hit.id)
  if (overview.value) {
    currentEntry.value = overview.value.proto_entries[0] || ''
    currentDoc.value = overview.value.docs[0] || ''
    commentable.value = overview.value.project.commentable // T4.5
    refreshComments() // T4.4：初始角标（含打开过的抽屉数据）
  }
  return overview.value
}

/** 上传成功后静默刷新（错误弹 toast，不阻塞用户继续操作）。 */
async function reloadOverview() {
  try {
    await loadOverview()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '刷新失败')
  }
}

onMounted(async () => {
  window.addEventListener('message', onMessage)
  try {
    await loadOverview()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载失败'
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('message', onMessage)
  if (highlightTimer) clearTimeout(highlightTimer)
})
</script>

<template>
  <main class="viewer" v-if="overview">
    <header class="v-head">
      <router-link to="/" class="back">← 项目列表</router-link>
      <strong>{{ overview.project.name }}</strong>
      <span class="meta">{{ slug }} · 创建者 {{ overview.project.creator.name }}</span>
      <!-- T4.5 项目级「可评论」开关（T8.4 收权：仅创建者可见可操作）：
           关闭后全员评论入口置灰（已有评论可查看）。PM 驱动 Agent 修改前关闭，
           同步刷新后再开启（消除 reviews/ 双写窗口） -->
      <span
        v-if="overview.project.is_creator"
        class="comment-toggle"
        title="项目级开关：关闭后全员无法新增评论（已有评论仍可查看）；驱动 Agent 修改前建议关闭"
      >
        允许评论
        <el-switch
          v-model="commentable"
          size="small"
          data-testid="commentable-toggle"
          @change="onCommentableChange"
        />
      </span>
      <!-- T4.1 评论模式开关：开启后点击原型元素采集评论定位 payload -->
      <span class="comment-toggle" title="开启后 hover 高亮、点击原型元素采集评论定位信息">
        评论模式
        <el-switch
          v-model="commentMode"
          size="small"
          data-testid="comment-mode"
          :disabled="!commentable"
          @change="onCommentModeChange"
        />
      </span>
      <!-- T4.4 评论抽屉开关：按页面分组 / 筛选 / 批量确认忽略 -->
      <button
        class="drawer-toggle"
        :class="{ open: drawerOpen }"
        data-testid="drawer-toggle"
        @click="toggleDrawer"
      >
        评论 {{ comments.length }}
      </button>
      <!-- T8.3/T8.2 创建者工具区（仅创建者）：上传原型/PRD + 导出评论（交付范围下拉） -->
      <el-dropdown v-if="overview.project.is_creator" @command="onCreatorTool">
        <button class="drawer-toggle" data-testid="creator-tools">
          {{ uploading ? '处理中…' : '创建者工具 ▾' }}
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="up_proto" data-testid="creator-upload-proto">
              上传原型（zip）
            </el-dropdown-item>
            <el-dropdown-item command="up_prd" data-testid="creator-upload-prd">
              上传 PRD（markdown）
            </el-dropdown-item>
            <el-dropdown-item command="export_all" data-testid="export-option-all" divided>
              导出全部评论
            </el-dropdown-item>
            <el-dropdown-item command="export_confirmed" data-testid="export-option-confirmed">
              导出所有已确认待修改
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <input ref="protoInput" type="file" accept=".zip" class="hidden-input" data-testid="creator-proto-file" @change="onProtoPicked" />
      <input ref="prdInput" type="file" accept=".md" class="hidden-input" data-testid="creator-prd-file" @change="onPrdPicked" />
      <!-- T3.3 对账提示条：匹配 · 缺失 · 未描述（点击看明细） -->
      <span
        v-if="reconSummary"
        class="recon-bar"
        :class="{ issue: reconHasIssue }"
        data-testid="recon-bar"
        role="button"
        tabindex="0"
        title="点击查看锚点对账明细"
        @click="openReconcile"
        @keydown.enter="openReconcile"
      >
        对账：{{ reconSummary.matched }} 匹配
        <template v-if="reconSummary.missing_in_proto"> · {{ reconSummary.missing_in_proto }} 原型缺失</template>
        <template v-if="reconSummary.undescribed"> · {{ reconSummary.undescribed }} 未描述</template>
        <template v-if="reconSummary.duplicate_prd"> · {{ reconSummary.duplicate_prd }} PRD重复</template>
        <template v-if="reconSummary.duplicate_proto"> · {{ reconSummary.duplicate_proto }} 原型重复</template>
        <template v-if="reconSummary.map_broken"> · {{ reconSummary.map_broken }} 地图坏引用</template>
      </span>
    </header>

    <div class="v-body" ref="containerEl">
      <!-- 左：原型 -->
      <section class="pane proto" :style="{ width: protoPct + '%' }">
        <div class="pane-head">
          <span>原型</span>
          <el-select
            v-if="overview.proto_entries.length > 1"
            v-model="currentEntry"
            size="small"
            style="width: 220px"
            data-testid="proto-select"
          >
            <el-option v-for="e in overview.proto_entries" :key="e" :label="e" :value="e" />
          </el-select>
          <!-- T4.1 SPA 路由显示（ROUTE_CHANGE 上报后才出现） -->
          <span
            v-if="currentRoute"
            class="route-tag"
            data-testid="current-page"
            :title="currentRoute"
          >
            {{ currentRoute }}
          </span>
          <!-- T4.2 页面评论入口（产品方案 §4.5「评论本页」） -->
          <button
            class="comment-page-btn"
            :disabled="!commentable || !commentMode || !ready"
            :title="!commentable ? '项目已关闭评论' : commentMode ? '对当前页面整体发表评论' : '开启评论模式后可用'"
            data-testid="comment-page-btn"
            @click="commentPage"
          >
            评论本页
          </button>
          <span class="ready" :data-ready="ready">{{ ready ? '已就绪' : '加载中…' }}</span>
        </div>
        <iframe
          v-if="iframeSrc"
          ref="iframeEl"
          :src="iframeSrc"
          :sandbox="sandboxAttr"
          data-testid="viewer-proto-frame"
        />
        <p v-else class="empty">项目内未发现 prototype/ 目录或 HTML 入口</p>
        <!-- T4.2 评论框（三类入口共用：DOM 点击 / 评论本页 / 文档段落） -->
        <CommentBox
          v-if="capturedPayload"
          :payload="capturedPayload"
          :author="currentUser?.name || ''"
          :submitting="submitting"
          :result="submittedResult"
          :shot-preview-url="shotPreviewUrl || null"
          :error="submitError"
          @submit="submitComment"
          @close="closeCommentBox"
        />
      </section>

      <!-- 分割条（原型 | 文档） -->
      <div
        class="divider"
        :class="{ dragging: dragging === 'proto' }"
        data-testid="divider"
        @pointerdown="onDividerDown($event, 'proto')"
        @pointermove="onDividerMove"
        @pointerup="onDividerUp"
        @pointercancel="onDividerUp"
      />

      <!-- 中：PRD -->
      <section class="pane prd" :style="{ width: prdPct + '%' }">
        <div class="pane-head">
          <span>PRD 文档</span>
          <el-select
            v-if="overview.docs.length > 1"
            v-model="currentDoc"
            size="small"
            style="width: 240px"
            data-testid="doc-select"
          >
            <el-option v-for="d in overview.docs" :key="d" :label="d" :value="d" />
          </el-select>
          <span v-else-if="overview.docs.length === 1" class="doc-name">{{ currentDoc }}</span>
          <span class="anchor-count" data-testid="anchor-count" title="bridge 上报的本页锚点数">
            锚点 {{ anchorCount }}
          </span>
        </div>
        <div
          class="prd-scroll"
          :class="{ 'comment-on': commentMode }"
          @mouseover="onDocMouseover"
          @mouseout="onDocMouseout"
          @click="onDocClick"
        >
          <p v-if="docLoading" class="empty">加载中…</p>
          <p v-else-if="!overview.docs.length" class="empty">
            项目内未发现 markdown 文档（prd/ 目录或根目录 *.md）
          </p>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <article v-else class="markdown-body" data-testid="prd-content" v-html="prdHtml" />
          <!-- T4.4 文档段落评论数量角标：hover 有评论的段落时出现于右上角 -->
          <div
            v-if="docBadge.count"
            ref="docBadgeEl"
            class="doc-comment-badge"
            data-testid="doc-comment-badge"
            title="查看该段落的评论"
            @click="clickDocBadge"
          >
            {{ docBadge.count > 99 ? '99+' : docBadge.count }}
          </div>
        </div>
        <!-- T 增强：多锚点区块点击「定位」→ 弹出锚点 ID 列表供选择 -->
        <Teleport to="body">
          <div
            v-if="anchorMenu.visible"
            class="anchor-menu-backdrop"
            data-testid="anchor-menu-backdrop"
            @click="anchorMenu.visible = false"
          />
          <div
            v-if="anchorMenu.visible"
            class="anchor-menu"
            :style="{ left: anchorMenu.x + 'px', top: anchorMenu.y + 'px' }"
            data-testid="anchor-menu"
          >
            <div class="anchor-menu-title">选择锚点</div>
            <button
              v-for="id in anchorMenu.ids"
              :key="id"
              type="button"
              class="anchor-menu-item"
              :data-testid="`anchor-pick-${id}`"
              @click="pickAnchor(id)"
            >
              {{ id }}
            </button>
          </div>
        </Teleport>
      </section>

      <!-- 分割条（文档 | 评论，抽屉打开才显示） -->
      <div
        v-if="drawerOpen"
        class="divider"
        :class="{ dragging: dragging === 'drawer' }"
        data-testid="divider-drawer"
        @pointerdown="onDividerDown($event, 'drawer')"
        @pointermove="onDividerMove"
        @pointerup="onDividerUp"
        @pointercancel="onDividerUp"
      />

      <!-- 右：评论抽屉（T8.6 从底部横条改为右侧栏，宽度可拖） -->
      <section v-if="drawerOpen" class="pane comments" :style="{ width: drawerPct + '%' }">
        <CommentDrawer
          :project-id="overview.project.id"
          :comments="comments"
          :current-user-email="currentUser?.email || ''"
          :focus-key="drawerFocusKey"
          :commentable="commentable"
          :is-creator="overview.project.is_creator"
          @refresh="refreshComments"
          @locate="locateComment"
        />
      </section>
    </div>
  </main>

  <main v-else class="viewer loading">
    <p v-if="loadError" class="empty">{{ loadError }}</p>
    <p v-else class="empty">加载中…</p>
  </main>

  <!-- T3.3 对账明细弹窗 -->
  <el-dialog
    v-model="reconDialogVisible"
    title="锚点对账明细"
    width="720px"
    data-testid="recon-dialog"
  >
    <p v-if="reconLoading" class="empty">对账计算中…</p>
    <template v-else-if="reconDetail">
      <p class="recon-note">
        共 {{ reconDetail.summary.matched }} 匹配 / {{ reconDetail.summary.missing_in_proto }} 原型缺失 /
        {{ reconDetail.summary.undescribed }} 未描述
        <template v-if="reconDetail.summary.duplicate_prd">
          ；PRD 重复 ID {{ reconDetail.summary.duplicate_prd }} 组
        </template>
        <template v-if="reconDetail.summary.duplicate_proto">
          ；原型重复 ID {{ reconDetail.summary.duplicate_proto }} 组
        </template>
        <template v-if="reconDetail.summary.map_broken">
          ；页面地图坏引用 {{ reconDetail.summary.map_broken }} 条
        </template>
      </p>
      <el-tabs v-model="reconTab">
        <el-tab-pane
          :label="`原型缺失（${reconDetail.missing_in_proto.length}）`"
          name="missing"
        >
          <el-table
            v-if="reconDetail.missing_in_proto.length"
            :data="reconDetail.missing_in_proto"
            size="small"
            data-testid="recon-missing-table"
          >
            <el-table-column prop="id" label="锚点 ID" width="200">
              <template #default="{ row }"><code>{{ row.id }}</code></template>
            </el-table-column>
            <el-table-column label="PRD 位置">
              <template #default="{ row }">
                {{ row.prd.doc_path || '（文档顶部）' }}
                <span class="dim"> · {{ row.prd.file }}:{{ row.prd.line }}</span>
              </template>
            </el-table-column>
          </el-table>
          <p v-else class="empty">无——PRD 里的锚点在原型中都有对应 data-pa</p>
        </el-tab-pane>

        <el-tab-pane
          :label="`未描述（${reconDetail.undescribed.length}）`"
          name="undescribed"
        >
          <el-table
            v-if="reconDetail.undescribed.length"
            :data="reconDetail.undescribed"
            size="small"
            data-testid="recon-undescribed-table"
          >
            <el-table-column prop="id" label="锚点 ID" width="200">
              <template #default="{ row }"><code>{{ row.id }}</code></template>
            </el-table-column>
            <el-table-column label="原型位置">
              <template #default="{ row }">
                {{ row.proto.file }}
                <span class="dim"> · {{ row.proto.css_path }}</span>
              </template>
            </el-table-column>
          </el-table>
          <p v-else class="empty">无——原型里的 data-pa 都有 PRD 锚点描述</p>
        </el-tab-pane>

        <el-tab-pane :label="`匹配（${reconDetail.matched.length}）`" name="matched">
          <el-table
            v-if="reconDetail.matched.length"
            :data="reconDetail.matched"
            size="small"
            data-testid="recon-matched-table"
          >
            <el-table-column prop="id" label="锚点 ID" width="200">
              <template #default="{ row }"><code>{{ row.id }}</code></template>
            </el-table-column>
            <el-table-column label="PRD 位置">
              <template #default="{ row }">
                {{ row.prd.doc_path || '（文档顶部）' }}
                <span class="dim"> · {{ row.prd.file }}</span>
              </template>
            </el-table-column>
            <el-table-column label="原型位置">
              <template #default="{ row }">
                {{ row.proto.file }}
                <span class="dim"> · {{ row.proto.css_path }}</span>
              </template>
            </el-table-column>
          </el-table>
          <p v-else class="empty">无匹配锚点</p>
        </el-tab-pane>

        <el-tab-pane
          v-if="reconDetail.duplicate_prd.length || reconDetail.duplicate_proto.length || reconDetail.map_broken.length"
          :label="`附加检查（${reconDetail.duplicate_prd.length + reconDetail.duplicate_proto.length + reconDetail.map_broken.length}）`"
          name="extra"
        >
          <template v-if="reconDetail.duplicate_prd.length">
            <h4 class="recon-sub">PRD 重复 ID（须全局唯一）</h4>
            <ul class="recon-list">
              <li v-for="d in reconDetail.duplicate_prd" :key="'p-' + d.id">
                <code>{{ d.id }}</code>
                <span v-for="(o, i) in d.occurrences" :key="i" class="dim">
                  · {{ o.file }}:{{ o.line }}（{{ o.doc_path || '文档顶部' }}）
                </span>
              </li>
            </ul>
          </template>
          <template v-if="reconDetail.duplicate_proto.length">
            <h4 class="recon-sub">原型重复 ID（跨页面/元素复用同一 ID）</h4>
            <ul class="recon-list">
              <li v-for="d in reconDetail.duplicate_proto" :key="'o-' + d.id">
                <code>{{ d.id }}</code>
                <span v-for="(o, i) in d.occurrences" :key="i" class="dim">
                  · {{ o.file }}（{{ o.css_path }}）
                </span>
              </li>
            </ul>
          </template>
          <template v-if="reconDetail.map_broken.length">
            <h4 class="recon-sub">页面地图坏引用（原型文件不存在）</h4>
            <ul class="recon-list">
              <li v-for="m in reconDetail.map_broken" :key="m.proto">
                <code>{{ m.proto }}</code>
                <span class="dim"> · 页面「{{ m.name }}」（锚点 {{ m.anchor }}）</span>
              </li>
            </ul>
          </template>
        </el-tab-pane>
      </el-tabs>
    </template>
  </el-dialog>
</template>

<style scoped>
.viewer {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 44px);
}
.v-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid #e6e8ec;
  font-size: 14px;
}
.v-head .back { color: #3b82f6; text-decoration: none; font-size: 13px; }
.v-head .meta { color: #999; font-size: 12px; }

/* T3.3 对账提示条 */
.v-head .recon-bar {
  margin-left: auto;
  font-size: 12px;
  color: #2e9e44;
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  border-radius: 4px;
  padding: 2px 10px;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.v-head .recon-bar:hover { border-color: #b3e19d; }
.v-head .recon-bar.issue {
  color: #b45200;
  background: #fdf6ec;
  border-color: #faecd8;
}
.v-head .recon-bar.issue:hover { border-color: #f3d19e; }

/* 对账明细弹窗 */
.recon-note { margin: 0 0 12px; font-size: 13px; color: #57606a; }
.recon-sub { margin: 14px 0 6px; font-size: 13px; color: #24292f; }
.recon-list { margin: 0; padding-left: 18px; font-size: 13px; line-height: 2; }
.recon-list .dim { color: #999; font-size: 12px; }
.dim { color: #999; font-size: 12px; }

.v-body {
  flex: 1;
  display: flex;
  min-height: 0;
  user-select: none;
}
.pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}
/* T8.6 右侧评论栏 */
.pane.comments { background: #fff; }
.pane-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  background: #fafbfc;
  border-bottom: 1px solid #e6e8ec;
  font-size: 13px;
  color: #666;
}
.pane-head .ready { margin-left: auto; color: #999; font-size: 12px; }
.pane-head .ready[data-ready="true"] { color: #2e9e44; }
.pane-head .doc-name { color: #999; font-size: 12px; margin-left: auto; }
.pane-head .anchor-count { color: #b8860b; font-size: 12px; margin-left: 8px; }

.proto iframe {
  flex: 1;
  width: 100%;
  border: 0;
  background: #f5f6f8;
}
.proto .empty { padding: 24px; color: #999; }

/* T4.1 评论模式 */
.v-head .comment-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #57606a;
  white-space: nowrap;
}

/* T4.4 评论抽屉开关 */
.v-head .drawer-toggle {
  border: 1px solid #d9dce1;
  border-radius: 4px;
  background: #fff;
  padding: 2px 10px;
  font-size: 12px;
  color: #57606a;
  cursor: pointer;
  white-space: nowrap;
}
.hidden-input { display: none; }
.v-head .drawer-toggle:hover,
.v-head .drawer-toggle.open {
  border-color: #2b5cff;
  color: #2b5cff;
}
.pane-head .route-tag {
  color: #999;
  font-size: 12px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* T4.2「评论本页」按钮（页面评论入口） */
.pane-head .comment-page-btn {
  border: 1px solid #d9dce1;
  border-radius: 4px;
  background: #fff;
  padding: 2px 10px;
  font-size: 12px;
  color: #57606a;
  cursor: pointer;
  white-space: nowrap;
}
.pane-head .comment-page-btn:hover:not(:disabled) {
  border-color: #2b5cff;
  color: #2b5cff;
}
.pane-head .comment-page-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.divider {
  width: 6px;
  cursor: col-resize;
  background: #eef0f3;
  flex-shrink: 0;
  touch-action: none;
}
.divider:hover, .divider.dragging { background: #c9d4e8; }

.prd .prd-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 20px 28px;
  position: relative; /* T4.4 文档段落评论角标的定位基座 */
}

/* T4.4 文档段落评论数量角标（hover 有评论的段落时右上角） */
.doc-comment-badge {
  position: absolute;
  z-index: 20;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 9px;
  background: #e5484d;
  color: #fff;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
  font-weight: 600;
  cursor: pointer;
  user-select: none;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
}
.doc-comment-badge:hover { background: #c93a3f; }
.prd .empty { color: #999; }

.markdown-body {
  user-select: text;
  font-size: 14px;
  line-height: 1.7;
  color: #24292f;
  max-width: 820px;
}
.markdown-body h1 { font-size: 22px; border-bottom: 1px solid #e6e8ec; padding-bottom: 8px; }
.markdown-body h2 { font-size: 18px; margin-top: 24px; }
.markdown-body h3 { font-size: 15px; margin-top: 20px; }
.markdown-body table { border-collapse: collapse; margin: 12px 0; }
.markdown-body th, .markdown-body td {
  border: 1px solid #dfe2e6;
  padding: 6px 12px;
  font-size: 13px;
}
.markdown-body th { background: #f6f8fa; }
.markdown-body code {
  background: #f6f8fa;
  padding: 2px 5px;
  border-radius: 4px;
  font-size: 12.5px;
}
.markdown-body pre {
  background: #f6f8fa;
  padding: 12px;
  border-radius: 8px;
  overflow-x: auto;
}
.markdown-body blockquote {
  border-left: 4px solid #dfe2e6;
  margin: 12px 0;
  padding: 4px 16px;
  color: #57606a;
}
/* T3.1 正向联动高亮（点原型锚点 icon 后 2s） */
.markdown-body :deep(.anchor-highlight) {
  background: #fff3d6;
  box-shadow: 0 0 0 3px #ffd66e;
  border-radius: 4px;
  transition: background 0.3s ease;
}

/* T 增强：多锚点列表弹窗（点击「定位」后浮出，选则定位到对应锚点） */
.anchor-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 3000;
}
.anchor-menu {
  position: fixed;
  z-index: 3001;
  min-width: 150px;
  max-width: 260px;
  padding: 6px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid #e2e5ea;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.16);
  font-size: 12px;
  transform: translateY(10px);
}
.anchor-menu-title {
  padding: 4px 8px;
  color: #909399;
  border-bottom: 1px solid #f0f2f5;
  margin-bottom: 4px;
}
.anchor-menu-item {
  display: block;
  width: 100%;
  padding: 6px 8px;
  margin: 2px 0;
  text-align: left;
  border: none;
  border-radius: 4px;
  background: none;
  color: #2b5cff;
  font-size: 12px;
  cursor: pointer;
  word-break: break-all;
}
.anchor-menu-item:hover {
  background: #f0f5ff;
}

/* T3.2 反向联动「定位」按钮：[data-pa] 元素 hover 时左上角浮现。
   伪元素实现（v-html 渲染的 markdown 不能插组件），点击走事件委托。
   T4.2 修订：仅锚点宿主显示（无锚点段落无处可跳）；position:relative
   基座泛化到任意可评论块级宿主（hover class 同名复用）。 */
.markdown-body :deep([data-pa]),
.markdown-body :deep(.pa-locate-hover) {
  position: relative;
}
.markdown-body :deep([data-pa].pa-locate-hover)::before {
  content: '定位';
  position: absolute;
  top: -10px;
  left: 8px;
  z-index: 10;
  padding: 1px 8px;
  border-radius: 4px;
  background: #2b5cff;
  color: #fff;
  font-size: 12px;
  line-height: 20px;
  cursor: pointer;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.2);
  user-select: none;
}
.markdown-body :deep([data-pa].pa-locate-hover:hover)::before {
  background: #1e4fd8;
}
/* 伪元素点击 → 宿主是 [data-pa]（e.target 是宿主元素本体，
   closest('.pa-locate-btn') 匹配不到，改由 closest('[data-pa]') 命中） */

/* T4.2 文档段落「评论」按钮：评论模式开时 hover 浮现于右上角（::after，
   与左上「定位」::before 对称）。任意块级宿主（无锚点段落也可评论）。
   点击区域判定见 onDocClick。 */
.prd-scroll.comment-on :deep(.pa-locate-hover)::after {
  content: '评论';
  position: absolute;
  top: -10px;
  right: 8px;
  z-index: 10;
  padding: 0 7px;
  border-radius: 4px;
  background: #fff;
  border: 1px solid #2b5cff;
  color: #2b5cff;
  font-size: 12px;
  line-height: 20px;
  cursor: pointer;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
  user-select: none;
}
.prd-scroll.comment-on :deep(.pa-locate-hover:hover)::after {
  background: #2b5cff;
  color: #fff;
}

.loading { align-items: center; justify-content: center; }
</style>
