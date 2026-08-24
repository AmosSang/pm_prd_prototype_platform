<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getOverview, getPrd, listProjects, type ProjectOverview } from '../projects'

/**
 * T2.4 分屏查看器骨架。
 * 左：原型 iframe（:8081 独立 origin + sandbox + URL nonce，bridge.js 由代理注入）
 * 右：PRD markdown 渲染（markdown-it，多文档 el-select 切换）
 * 中：可拖动分割条（pointer 事件，父级相对宽度）
 *
 * 锚点体系（icon/联动/高亮）是 T3.x 范围，本卡只做纯渲染骨架。
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

function onMessage(event: MessageEvent) {
  const msg = event.data || {}
  if (event.origin !== PROTO_ORIGIN && event.origin !== 'null') return
  if (msg.nonce !== nonce) return
  if (msg.type === 'READY') ready.value = true
}

const md = new MarkdownIt({ html: true, linkify: true })

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
onBeforeUnmount(() => window.removeEventListener('message', onMessage))
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
        </div>
        <div class="prd-scroll">
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

.loading { align-items: center; justify-content: center; }
</style>
