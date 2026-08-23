<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

const PROTO_ORIGIN = 'http://localhost:8081'
// T1.3 三场景：登录页（基础）/ modal（带弹窗）/ scroll（长页滚动）
const SCENARIOS: Record<string, { page: string; target: string; label: string }> = {
  login: { page: '/proto/demo/prototype/pages/login.html', target: '#captcha', label: '登录页（基础）' },
  modal: { page: '/proto/demo/prototype/pages/modal.html', target: '#confirm-delete', label: '账号设置（带弹窗）' },
  scroll: { page: '/proto/demo/prototype/pages/scroll.html', target: '#remark', label: '个人资料（长页滚动）' },
}

const route = useRoute()
const sceneKey = computed(() => {
  const q = String(route.query.scene ?? 'login')
  return q in SCENARIOS ? q : 'login'
})
const scene = computed(() => SCENARIOS[sceneKey.value])

function makeNonce(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

const nonce = makeNonce()
const iframeSrc = computed(() => `${PROTO_ORIGIN}${scene.value.page}#pp-nonce=${nonce}`)

const ready = ref(false)
const busy = ref(false)
const shotUrl = ref('')
const shotInfo = ref('')
const errorMsg = ref('')
const logs = ref<string[]>([])

let requestId = 0
let pending: { id: number; resolve: (v: any) => void; reject: (e: Error) => void } | null = null

function log(msg: string) {
  logs.value.unshift(`[${new Date().toLocaleTimeString()}] ${msg}`)
}

function onMessage(event: MessageEvent) {
  const msg = event.data || {}
  if (event.origin !== PROTO_ORIGIN && event.origin !== 'null') return
  if (msg.nonce !== nonce) return
  if (msg.type === 'READY') {
    ready.value = true
    log(`✅ READY（page=${msg.page}）`)
  } else if (msg.type === 'SCREENSHOT_RESULT' && pending && msg.requestId === pending.id) {
    log(`✅ SCREENSHOT_RESULT（${msg.width}x${msg.height}，红框 ${JSON.stringify(msg.highlight)}）`)
    pending.resolve(msg)
    pending = null
  } else if (msg.type === 'SCREENSHOT_ERROR' && pending && msg.requestId === pending.id) {
    pending.reject(new Error(msg.error))
    pending = null
  }
}

async function takeScreenshot() {
  if (!ready.value || busy.value) return
  busy.value = true
  errorMsg.value = ''
  shotUrl.value = ''
  shotInfo.value = ''
  try {
    const id = ++requestId
    const frame = document.getElementById('proto-frame') as HTMLIFrameElement
    // 等 iframe 完成截图（Blob 经 postMessage 结构化克隆回传）
    const result = await new Promise<any>((resolve, reject) => {
      pending = { id, resolve, reject }
      frame.contentWindow?.postMessage(
        { type: 'TAKE_SCREENSHOT', nonce, requestId: id, cssPath: scene.value.target },
        '*',
      )
      setTimeout(() => {
        if (pending && pending.id === id) {
          pending.reject(new Error('截图超时（15s）'))
          pending = null
        }
      }, 15_000)
    })
    log('→ 收到 Blob，上传后端画红框…')
    // 上传：FormData（截图 + highlight_rect）
    const fd = new FormData()
    fd.append('screenshot', result.blob, 'screenshot.png')
    fd.append('request_id', `demo-${id}-${Date.now()}`)
    fd.append('highlight_rect', JSON.stringify(result.highlight || {}))
    const res = await fetch('/api/projects/demo/shots', { method: 'POST', body: fd })
    const body = await res.json()
    if (body.code !== 0) throw new Error(body.msg || '上传失败')
    shotUrl.value = body.data.shot_url
    shotInfo.value = `整页 ${result.width}x${result.height} · 红框 ${JSON.stringify(result.highlight)}`
    log(`✅ 截图已保存：${body.data.shot_url}`)
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
    log(`❌ ${errorMsg.value}`)
  } finally {
    busy.value = false
  }
}

onMounted(() => window.addEventListener('message', onMessage))
onBeforeUnmount(() => window.removeEventListener('message', onMessage))

// 场景切换：iframe src 变化 → 重置状态等新页面的 READY
watch(sceneKey, () => {
  ready.value = false
  busy.value = false
  shotUrl.value = ''
  shotInfo.value = ''
  errorMsg.value = ''
})
</script>

<template>
  <main class="shot-demo">
    <h1>T1.2 截图链路 Demo</h1>
    <p class="meta">
      场景：{{ scene.label }} · 目标元素：{{ scene.target }} ·
      READY：{{ ready ? '✅' : '⏳' }}
    </p>
    <p class="scene-switch">
      <router-link
        v-for="(s, k) in SCENARIOS" :key="k"
        :to="{ path: '/demo/shot', query: { scene: k } }"
        class="scene-link" :class="{ active: k === sceneKey }">{{ s.label }}</router-link>
    </p>

    <div class="stage">
      <div class="pane">
        <div class="pane-head">
          原型 iframe
          <button class="shot" :disabled="!ready || busy" @click="takeScreenshot">
            {{ busy ? '截图中…' : '截图（整页+红框）' }}
          </button>
        </div>
        <iframe id="proto-frame" :key="sceneKey" :src="iframeSrc"
          sandbox="allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox"
          data-testid="proto-frame" />
      </div>

      <div class="pane">
        <div class="pane-head">截图结果（后端画红框后）</div>
        <div class="result">
          <p v-if="errorMsg" class="err" data-testid="shot-error">{{ errorMsg }}</p>
          <img v-if="shotUrl" :src="shotUrl" data-testid="shot-image" :alt="shotInfo" />
          <p v-if="shotInfo" class="info" data-testid="shot-info">{{ shotInfo }}</p>
          <p v-if="!shotUrl && !errorMsg" class="empty">点击左侧「截图」按钮体验完整链路</p>
        </div>
      </div>
    </div>

    <ul class="logs" data-testid="msg-logs">
      <li v-for="(l, i) in logs" :key="i">{{ l }}</li>
    </ul>
  </main>
</template>

<style scoped>
.shot-demo { font-family: system-ui, sans-serif; padding: 24px; max-width: 1100px; margin: 0 auto; }
.meta { color: #555; }
.stage { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
.pane { border: 1px solid #e2e2e2; border-radius: 8px; overflow: hidden; display: flex; flex-direction: column; height: 480px; }
.pane-head { padding: 8px 12px; background: #fafafa; border-bottom: 1px solid #e2e2e2; font-size: 13px; color: #666; display: flex; justify-content: space-between; align-items: center; }
iframe { flex: 1; width: 100%; border: 0; background: #f5f6f8; }
.shot { padding: 4px 14px; font-size: 12px; border: 1px solid #2b5cff; color: #2b5cff; background: #fff; border-radius: 4px; cursor: pointer; }
.shot:disabled { opacity: .4; cursor: not-allowed; }
.result { flex: 1; overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.result img { max-width: 100%; border: 1px solid #eee; }
.err { color: #d33; font-size: 13px; }
.info { color: #666; font-size: 12px; }
.empty { color: #bbb; font-size: 13px; }
.logs { list-style: none; padding: 8px 12px; font-size: 12px; font-family: monospace; border: 1px solid #eee; border-radius: 6px; margin-top: 16px; }
.scene-switch { display: flex; gap: 8px; margin: 8px 0 0; }
.scene-link { padding: 4px 12px; border: 1px solid #d9d9d9; border-radius: 14px; font-size: 13px; color: #555; text-decoration: none; }
.scene-link.active { border-color: #2b5cff; color: #2b5cff; background: #f0f4ff; }
</style>
