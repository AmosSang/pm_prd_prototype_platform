<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

const PROTO_ORIGIN = 'http://localhost:8081'
const DEMO_PAGE = '/proto/demo/prototype/pages/login.html'

function makeNonce(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

// URL nonce：sandbox 不透明 origin 下的消息认证（见 bridge.js 头注）
const nonce = makeNonce()
const iframeSrc = ref(`${PROTO_ORIGIN}${DEMO_PAGE}#pp-nonce=${nonce}`)

const ready = ref(false)
const echoed = ref(false)
const sandboxAttr = 'allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox'
const logs = ref<string[]>([])

function log(msg: string) {
  logs.value.unshift(`[${new Date().toLocaleTimeString()}] ${msg}`)
}

function onMessage(event: MessageEvent) {
  const msg = event.data || {}
  // 1) 来源必须是原型 origin（sandbox 下为 "null"）
  if (event.origin !== PROTO_ORIGIN && event.origin !== 'null') {
    log(`❌ 拒绝来源 ${event.origin}`)
    return
  }
  // 2) nonce 必须匹配本次会话
  if (msg.nonce !== nonce) {
    log(`❌ 拒绝消息：nonce 不符`)
    return
  }
  if (msg.type === 'ECHO') {
    echoed.value = true
    log(`✅ ECHO（echo=${msg.echo}）`)
  } else if (msg.type === 'READY') {
    ready.value = true
    log(`✅ READY（page=${msg.page}）`)
  }
}

let frame: HTMLIFrameElement | null = null

function ping() {
  // 兼容两类环境：沙箱 iframe（不透明 origin）与开发期 Vite 代理（普通 origin）。
  // sandbox 无 allow-same-origin 时必须用 '*'/'null'；普通 iframe 用具体 origin 更严格。
  frame?.contentWindow?.postMessage({ type: 'PING', nonce }, '*')
  log('→ PING 已发送')
}

onMounted(() => {
  window.addEventListener('message', onMessage)
  frame = document.getElementById('proto-frame') as HTMLIFrameElement
})
onBeforeUnmount(() => window.removeEventListener('message', onMessage))
</script>

<template>
  <main class="bridge-demo">
    <h1>T1.1 沙箱桥接 Demo</h1>
    <p class="meta">
      READY：{{ ready ? '✅ 已收到' : '⏳ 等待中' }} ·
      ECHO：{{ echoed ? '✅ 往返成功' : '⏳ 点 PING 测试' }}
    </p>

    <div class="stage">
      <div class="pane">
        <div class="pane-head">
          原型 iframe（sandbox，独立 origin :8081）
          <button class="ping" :disabled="!ready" @click="ping">PING</button>
        </div>
        <iframe
          id="proto-frame"
          :src="iframeSrc"
          :sandbox="sandboxAttr"
          data-testid="proto-frame"
        />
      </div>

      <div class="pane">
        <div class="pane-head">消息日志（postMessage）</div>
        <ul class="logs" data-testid="msg-logs">
          <li v-if="!logs.length" class="empty">（暂无消息）</li>
          <li v-for="(l, i) in logs" :key="i">{{ l }}</li>
        </ul>
      </div>
    </div>
  </main>
</template>

<style scoped>
.bridge-demo { font-family: system-ui, sans-serif; padding: 24px; max-width: 1100px; margin: 0 auto; }
.meta { color: #555; }
.stage { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
.pane { border: 1px solid #e2e2e2; border-radius: 8px; overflow: hidden; display: flex; flex-direction: column; height: 480px; }
.pane-head { padding: 8px 12px; background: #fafafa; border-bottom: 1px solid #e2e2e2; font-size: 13px; color: #666; display: flex; justify-content: space-between; align-items: center; }
iframe { flex: 1; width: 100%; border: 0; background: #f5f6f8; }
.ping { padding: 4px 14px; font-size: 12px; border: 1px solid #2b5cff; color: #2b5cff; background: #fff; border-radius: 4px; cursor: pointer; }
.ping:disabled { opacity: .4; cursor: not-allowed; }
.logs { list-style: none; margin: 0; padding: 8px 12px; font-size: 12px; font-family: monospace; overflow-y: auto; flex: 1; }
.empty { color: #bbb; }
</style>
