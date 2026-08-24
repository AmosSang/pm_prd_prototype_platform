/** 项目相关 API 与类型（T2.3 / T2.4）。 */
import { api } from './api'

export interface ProjectInfo {
  id: number
  project_id: string
  name: string
  repo_url: string
  branch: string
  commentable: boolean
  last_sync_at: string | null
  sync_error: string | null
  created_at: string
}

export interface ProjectOverview {
  project: ProjectInfo
  docs: string[]
  proto_entries: string[]
  page_map: unknown[]
  reconcile_summary: unknown | null
}

export function listProjects(): Promise<ProjectInfo[]> {
  return api.get<ProjectInfo[]>('/api/projects')
}

export function createProject(payload: {
  name: string
  repo_url: string
  token: string
  branch: string
}): Promise<ProjectInfo> {
  return api.post<ProjectInfo>('/api/projects', payload)
}

export function getOverview(id: number): Promise<ProjectOverview> {
  return api.get<ProjectOverview>(`/api/projects/${id}/overview`)
}

export function getPrd(id: number, file: string): Promise<{ file: string; content: string }> {
  return api.get<{ file: string; content: string }>(
    `/api/projects/${id}/prd?file=${encodeURIComponent(file)}`,
  )
}

/** 手动同步（临时按钮，T3.1）：fetch + ff-only 拉最新。T5.1 会升级为完整 SYNC_PULL。 */
export function syncProject(id: number): Promise<ProjectInfo> {
  return api.post<ProjectInfo>(`/api/projects/${id}/sync`)
}
