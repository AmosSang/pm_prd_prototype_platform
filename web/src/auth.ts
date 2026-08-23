/** 登录态管理：当前用户 + 登录/登出。 */
import { ref } from 'vue'
import { api } from './api'

export interface CurrentUser {
  id: number
  email: string
  name: string
}

export const currentUser = ref<CurrentUser | null>(null)
export const authChecked = ref(false)

/** 应用启动时调用：拉取当前登录用户（401 时视为未登录）。 */
export async function initAuth() {
  try {
    currentUser.value = await api.get('/api/auth/me')
  } catch {
    currentUser.value = null
  } finally {
    authChecked.value = true
  }
}

export async function requestCode(email: string) {
  return api.post('/api/auth/request-code', { email })
}

export async function verifyLogin(email: string, code: string) {
  const user = await api.post<{ user: CurrentUser }>('/api/auth/verify', { email, code })
  currentUser.value = user.user
  return user.user
}

export async function logout() {
  await api.post('/api/auth/logout')
  currentUser.value = null
}
