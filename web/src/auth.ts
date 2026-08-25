/** 登录态管理：当前用户 + 登录/登出。 */
import { ref } from 'vue'
import { api } from './api'

export interface CurrentUser {
  id: number
  email: string
  name: string
  is_admin?: boolean
}

export const currentUser = ref<CurrentUser | null>(null)
export const authChecked = ref(false)

/** 应用启动时调用：拉取当前登录用户（401 时视为未登录）。 */
export async function initAuth() {
  try {
    const data = await api.get<{ user: CurrentUser }>('/api/auth/me')
    currentUser.value = data.user
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

// ───────────────────── 用户管理（T2.1 增强，仅超管）─────────────────────

export interface ManagedUser {
  id: number
  email: string
  name: string
  is_admin: boolean
  disabled: boolean
  created_at: string
}

export function listUsers(): Promise<ManagedUser[]> {
  return api.get<ManagedUser[]>('/api/users')
}

/** 创建用户（白名单接入：邮箱 + 姓名）。 */
export function createUser(email: string, name: string): Promise<ManagedUser> {
  return api.post<ManagedUser>('/api/users', { email, name })
}

/** 修改姓名（包括超管本人；改本人时后端同步 session，顶栏即时更新）。 */
export function renameUser(id: number, name: string): Promise<ManagedUser> {
  return api.patch<ManagedUser>(`/api/users/${id}`, { name })
}

/** 停用/启用账号。 */
export function setUserStatus(id: number, disabled: boolean): Promise<ManagedUser> {
  return api.patch<ManagedUser>(`/api/users/${id}/status`, { disabled })
}
