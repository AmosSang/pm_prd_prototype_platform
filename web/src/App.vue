<script setup lang="ts">
import { useRouter } from 'vue-router'
import { currentUser, logout } from './auth'

const router = useRouter()

async function onLogout() {
  await logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="app-shell">
    <header v-if="currentUser" class="app-bar" data-testid="app-bar">
      <span class="brand">产品方案展示平台</span>
      <span class="spacer" />
      <span class="user" data-testid="current-user">{{ currentUser.name }}（{{ currentUser.email }}）</span>
      <a class="logout" data-testid="logout" @click.prevent="onLogout">退出</a>
    </header>
    <router-view />
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; }
.app-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 20px;
  height: 44px;
  background: #1f2430;
  color: #fff;
  font-size: 13px;
}
.brand { font-weight: 600; letter-spacing: 0.5px; }
.spacer { flex: 1; }
.user { color: #c8ccd4; }
.logout { color: #8ab4ff; cursor: pointer; }
</style>
