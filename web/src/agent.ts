/** Agent 接入（T12.2）：API Token 生成 / 列表 / 撤销。
 *
 * Token 明文只在生成响应返回一次（后端不落库、不进日志，库中只存 SHA-256）；
 * 权限 = 当前账号的网页端权限（含协作者判定），撤销即时生效。
 */
import { api } from './api'

export interface ApiTokenInfo {
  id: number
  name: string
  created_at: string
  last_used_at: string | null
  revoked: boolean
}

/** 生成 Token：响应含 plaintext（仅此一次）。 */
export function createToken(name: string): Promise<ApiTokenInfo & { plaintext: string }> {
  return api.post<ApiTokenInfo & { plaintext: string }>('/api/tokens', { name })
}

export function listTokens(): Promise<ApiTokenInfo[]> {
  return api.get<ApiTokenInfo[]>('/api/tokens')
}

/** 撤销 Token（幂等）：撤销后持有者下一次调用即 401。 */
export function revokeToken(id: number): Promise<ApiTokenInfo> {
  return api.delete<ApiTokenInfo>(`/api/tokens/${id}`)
}
