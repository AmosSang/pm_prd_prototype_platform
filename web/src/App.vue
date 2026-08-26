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
      <span class="brand">
        <span class="brand-dot" aria-hidden="true" />
        产品方案展示平台
      </span>
      <span class="spacer" />
      <router-link v-if="currentUser.is_admin" class="admin-link" data-testid="user-manage" to="/users">
        用户管理
      </router-link>
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
  height: 48px;
  background: var(--pp-surface);
  border-bottom: 1px solid var(--pp-border);
  font-size: 13px;
  position: sticky;
  top: 0;
  z-index: 50;
}
.brand {
  font-weight: 600;
  letter-spacing: 0.3px;
  color: var(--pp-text-1);
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.brand-dot {
  width: 10px;
  height: 10px;
  border-radius: 4px;
  background: linear-gradient(135deg, #4f63d2 0%, #7d8de0 100%);
  box-shadow: 0 1px 3px rgba(79, 99, 210, 0.4);
}
.spacer { flex: 1; }
.user { color: var(--pp-text-3); }
.logout { color: var(--pp-primary); cursor: pointer; }
.logout:hover { color: var(--pp-primary-hover); }
.admin-link { color: var(--pp-text-2); text-decoration: none; }
.admin-link:hover { color: var(--pp-primary); }
</style>
