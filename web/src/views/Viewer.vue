<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { anchorPlugin } from '../anchor-plugin'
import { getOverview, getPrd, listProjects, type ProjectOverview } from '../projects'

/**
 * T2.4 分屏查看器骨架 + T3.1 锚点正向联动 + T3.2 反向联动。
 * 左：原型 iframe（:8081 独立 origin + sandbox + URL nonce，bridge.js 由代理注入）
 * 右：PRD markdown 渲染（markdown-it + 锚点插件，多文档 el-select 切换）
 * 中：可拖动分割条（pointer 事件，父级相对宽度）
 *
 * T3.1 正向联动：bridge 上报 ANCHOR_CLICK（点原型锚点 icon ◈）→
 * 右侧 [data-pa] 元素 scrollIntoView + 高亮 2s（anchor-highlight class）。
 * T3.2 反向联动：文档段落 hover「定位」按钮 → 查页面地图（锚点 → 原型文件）
 * → 跨页先切 iframe src（等 READY）→ 发 HIGHLIGHT_ANCHOR（bridge 滚动+闪烁）。
 * 跨文档：锚点不在当前文档时切文档再定位（正向反向共用）。
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

function onMessage(event: MessageEvent) {
  const msg = event.data || {}
  if (event.origin !== PROTO_ORIGIN && event.origin !== 'null') return
  if (msg.nonce !== nonce) return
  if (msg.type === 'READY') {
    ready.value = true
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

/** 文档侧「定位」按钮 hover 显示：事件委托 mouseover/mouseout。 */
const iframeEl = ref<HTMLIFrameElement | null | undefined>(undefined)

function onDocMouseover(e: MouseEvent) {
  const host = (e.target as HTMLElement)?.closest?.('[data-pa]')
  if (!host) return
  host.classList.add('pa-locate-hover')
}

function onDocMouseout(e: MouseEvent) {
  const host = (e.target as HTMLElement)?.closest?.('[data-pa]')
  if (!host) return
  host.classList.remove('pa-locate-hover')
}

/** 段落点击（委托）：点击落在「定位」按钮区域内（伪元素区域，坐标判断）
 * 才触发反向定位，点段落其他位置不误触。 */
function onDocClick(e: MouseEvent) {
  const host = (e.target as HTMLElement)?.closest?.('[data-pa]')
  if (!host || !host.classList.contains('pa-locate-hover')) return
  // 「定位」按钮区域：段落顶部 28px 内（按钮在 top:-10px 高 20px）
  const rect = host.getBoundingClientRect()
  if (e.clientY <= rect.top + 28) {
    e.preventDefault()
    const anchorId = host.getAttribute('data-pa')
    if (anchorId) locateAnchor(anchorId)
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

/* T3.2 反向联动「定位」按钮：[data-pa] 元素 hover 时右上角浮现。
   伪元素实现（v-html 渲染的 markdown 不能插组件），点击走事件委托。 */
.markdown-body :deep([data-pa]) {
  position: relative;
}
.markdown-body :deep(.pa-locate-hover)::before {
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
.markdown-body :deep(.pa-locate-hover:hover)::before {
  background: #1e4fd8;
}
/* 伪元素点击 → 宿主是 [data-pa]（e.target 是宿主元素本体，
   closest('.pa-locate-btn') 匹配不到，改由 closest('[data-pa]') 命中） */

.loading { align-items: center; justify-content: center; }
</style>
