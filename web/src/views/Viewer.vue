<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { anchorPlugin } from '../anchor-plugin'
import { currentUser } from '../auth'
import CommentBox from '../components/CommentBox.vue'
import {
  createComment,
  getOverview,
  getPrd,
  getReconcile,
  listProjects,
  uploadShot,
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

const PROTO_ORIGIN = 'http://localhost:8081'

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

const sandboxAttr = 'allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox'
const ready = ref(false)
const anchorCount = ref(0) // 本页锚点数（ANCHOR_REPORT 更新，右上角显示）

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
    // T4.3：git 落仓走异步队列（git_task pending），提交不再被 git 结果
    // 阻塞；失败会置项目 sync_error（首页卡片「同步异常」提示）
    submittedResult.value = res
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
    // 跨页定位：切页后等 READY 再发 HIGHLIGHT_ANCHOR（技术方案 §2.5）
    if (pendingHighlight.value) {
      const anchorId = pendingHighlight.value
      pendingHighlight.value = ''
      postHighlight(anchorId)
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
}

// ───────────────────────── 锚点联动（T3.1 正向 / T3.2 反向）─────────────
// 正向：点原型锚点 icon → 右侧文档滚动到 [data-pa=id] 段落 + 高亮 2s。
// 反向：点文档「定位」→ 查页面地图 → 跨页先切 iframe（等 READY）→ 发
// HIGHLIGHT_ANCHOR（bridge 滚动+闪烁）。
let highlightTimer: ReturnType<typeof setTimeout> | null = null

/** 文档容器内查锚点元素（当前已渲染的文档）。 */
function findDocAnchor(anchorId: string): HTMLElement | null {
  const container = document.querySelector<HTMLElement>('[data-testid="prd-content"]')
  return container?.querySelector<HTMLElement>(`[data-pa="${cssEscape(anchorId)}"]`) || null
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
const pendingHighlight = ref('')

function postHighlight(anchorId: string) {
  // targetOrigin '*'：sandbox iframe（无 allow-same-origin）的 origin 是
  // 不透明 "null"，指定具体 origin 会被浏览器拒发。安全靠 bridge 侧
  // origin + nonce 双重校验（技术方案 §2.2，与反向消息同规约）
  iframeEl.value?.contentWindow?.postMessage(
    { type: 'HIGHLIGHT_ANCHOR', anchorId, nonce },
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
    ElMessage.info('仓库内未发现原型页面')
    return
  }
  if (targetEntry !== currentEntry.value) {
    // 跨页：切 src（iframe 重载 → bridge READY → onMessage 里补发定位）
    pendingHighlight.value = anchorId
    ready.value = false
    currentEntry.value = targetEntry
  } else {
    postHighlight(anchorId)
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
function onDocClick(e: MouseEvent) {
  const host = docHostOf(e.target)
  if (!host || !host.classList.contains('pa-locate-hover')) return
  // 顶部按钮区：段落顶部 28px 内（按钮在 top:-10px 高 20px）
  const rect = host.getBoundingClientRect()
  if (e.clientY > rect.top + 28) return
  e.preventDefault()
  const anchorId = host.getAttribute('data-pa')
  if (e.clientX <= rect.left + 60) {
    // 左上「定位」（仅锚点宿主；无锚点不显示该按钮，点了也不响应）
    if (anchorId) locateAnchor(anchorId)
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
  const anchorId = host.getAttribute('data-pa') || ''
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

// ───────────────────────── 分割条拖动 ─────────────────────────
// 左侧面板宽度百分比；pointer 捕获 + 全局 move/up，拖出条外也持续跟随。
const leftPct = ref(50)
const dragging = ref(false)
const containerEl = ref<HTMLElement | null>(null)

function onDividerDown(e: PointerEvent) {
  dragging.value = true
  ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
}
function onDividerMove(e: PointerEvent) {
  if (!dragging.value || !containerEl.value) return
  const rect = containerEl.value.getBoundingClientRect()
  const pct = ((e.clientX - rect.left) / rect.width) * 100
  leftPct.value = Math.min(80, Math.max(20, pct))
}
function onDividerUp() {
  dragging.value = false
}

onMounted(async () => {
  window.addEventListener('message', onMessage)
  try {
    // slug → 列表反查数字 id（主键不进 URL，防猜测遍历）
    const all = await listProjects()
    const hit = all.find((p) => p.project_id === slug)
    if (!hit) throw new Error('项目不存在')
    overview.value = await getOverview(hit.id)
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载失败'
  }
  if (overview.value) {
    currentEntry.value = overview.value.proto_entries[0] || ''
    currentDoc.value = overview.value.docs[0] || ''
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
      <span class="meta">{{ slug }} · {{ overview.project.branch }}</span>
      <!-- T4.1 评论模式开关：开启后点击原型元素采集评论定位 payload -->
      <span class="comment-toggle" title="开启后 hover 高亮、点击原型元素采集评论定位信息">
        评论模式
        <el-switch
          v-model="commentMode"
          size="small"
          data-testid="comment-mode"
          @change="onCommentModeChange"
        />
      </span>
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
      <section class="pane proto" :style="{ width: leftPct + '%' }">
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
            :disabled="!commentMode || !ready"
            :title="commentMode ? '对当前页面整体发表评论' : '开启评论模式后可用'"
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
        <p v-else class="empty">仓库内未发现 prototype/ 目录或 HTML 入口</p>
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

      <!-- 分割条 -->
      <div
        class="divider"
        :class="{ dragging }"
        data-testid="divider"
        @pointerdown="onDividerDown"
        @pointermove="onDividerMove"
        @pointerup="onDividerUp"
        @pointercancel="onDividerUp"
      />

      <!-- 右：PRD -->
      <section class="pane prd">
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
            仓库内未发现 markdown 文档（prd/ 目录或根目录 *.md）
          </p>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <article v-else class="markdown-body" data-testid="prd-content" v-html="prdHtml" />
        </div>
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
}
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
