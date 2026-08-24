/** 项目相关 API 与类型（T2.3）。 */
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
