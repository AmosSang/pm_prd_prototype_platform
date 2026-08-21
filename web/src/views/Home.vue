<script setup lang="ts">
import { onMounted, ref } from 'vue'

const backendStatus = ref<'checking' | 'ok' | 'fail'>('checking')

onMounted(async () => {
  try {
    const res = await fetch('/api/health')
    const body = await res.json()
    backendStatus.value = body.code === 0 ? 'ok' : 'fail'
  } catch {
    backendStatus.value = 'fail'
  }
})
</script>

<template>
  <main class="hello">
    <h1>产品方案展示平台</h1>
    <p><router-link to="/demo/bridge">T1.1 沙箱桥接 Demo →</router-link></p>
    <p><router-link to="/demo/shot">T1.2 截图链路 Demo →</router-link></p>
    <p>
      后端连接状态：
      <span :class="backendStatus">{{ backendStatus === 'ok' ? '正常' : backendStatus === 'checking' ? '检测中…' : '异常' }}</span>
    </p>
  </main>
</template>

<style scoped>
.hello {
  font-family: system-ui, -apple-system, 'PingFang SC', sans-serif;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100vh;
  gap: 8px;
}
.checking { color: #999; }
.ok { color: #2e9e44; font-weight: 600; }
.fail { color: #d33; font-weight: 600; }
</style>
