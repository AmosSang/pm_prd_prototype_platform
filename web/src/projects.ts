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

/** 页面地图条目（T3.2）：PRD 第 4 章表格解析结果 */
export interface PageMapEntry {
  name: string
  proto: string
  anchor: string
}

/** 对账摘要（T3.3）：overview.reconcile_summary */
export interface ReconcileSummary {
  matched: number
  missing_in_proto: number
  undescribed: number
  duplicate_prd: number
  duplicate_proto: number
  map_broken: number
}

/** 对账明细条目（T3.3）：/reconcile 接口 */
export interface PrdAnchorRef {
  id: string
  file: string
  doc_path: string
  line: number
}

export interface ProtoAnchorRef {
  id: string
  file: string
  css_path: string
}

export interface ReconcileDetail {
  summary: ReconcileSummary
  matched: { id: string; prd: PrdAnchorRef; proto: ProtoAnchorRef }[]
  missing_in_proto: { id: string; prd: PrdAnchorRef }[]
  undescribed: { id: string; proto: ProtoAnchorRef }[]
  duplicate_prd: { id: string; occurrences: PrdAnchorRef[] }[]
  duplicate_proto: { id: string; occurrences: ProtoAnchorRef[] }[]
  map_broken: PageMapEntry[]
}

export interface ProjectOverview {
  project: ProjectInfo
  docs: string[]
  proto_entries: string[]
  page_map: PageMapEntry[]
  /** 锚点 ID → 原型文件（T3.2）：组件锚点不在页面地图里，靠本索引找文件 */
  proto_anchor_index: Record<string, string>
  reconcile_summary: ReconcileSummary | null
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

/** 对账明细（T3.3）：三态清单 + 重复 ID + 页面地图坏引用 */
export function getReconcile(id: number): Promise<ReconcileDetail> {
  return api.get<ReconcileDetail>(`/api/projects/${id}/reconcile`)
}

/** 手动同步（临时按钮，T3.1）：fetch + ff-only 拉最新。T5.1 会升级为完整 SYNC_PULL。 */
export function syncProject(id: number): Promise<ProjectInfo> {
  return api.post<ProjectInfo>(`/api/projects/${id}/sync`)
}

// ───────────────────────── 评论（T4.2）─────────────────────────

/** 评论 DOM 定位 payload（bridge 采集，技术方案 §2.3；schema 见 server/reviews.py） */
export interface InteractionState {
  modal_open: boolean
  viewport: string
  scroll_y: number
  route: string
}

export interface CommentPayload {
  target_type: 'dom' | 'page' | 'doc_block'
  prototype_page: string
  anchor_id: string
  nearest_anchor_id: string
  css_path: string
  outer_html: string
  text_excerpt: string
  interaction_state: InteractionState
  /** doc_block 评论专有（前端构造，服务端复核 + 补指纹）。
   * 无锚点段落：doc_anchor_id 空、doc_path（标题链）+ doc_excerpt 供指纹定位 */
  doc_anchor_id?: string
  doc_excerpt?: string
  doc_path?: string
}

export interface HighlightRect {
  x: number
  y: number
  w: number
  h: number
}

/** 评论提交结果（POST /comments 响应：评论 JSON 全量 + 落仓任务状态） */
export interface CreateCommentResult {
  comment_id: string
  author: string
  status: string
  priority: string
  scope: string
  content: string
  created_at: string
  target_type: 'dom' | 'page' | 'doc_block'
  prototype_page?: string
  anchor_id?: string
  screenshot?: string
  highlight_rect?: HighlightRect
  doc_anchor_id?: string
  doc_excerpt?: string
  /** T4.3 落仓任务（异步队列：请求返回时 pending，git 结果不阻塞提交） */
  git_task: { id: number; status: string }
}

export function createComment(
  id: number,
  body: {
    payload: CommentPayload
    content: string
    priority: string
    scope: string
    shot_id?: string
    highlight_rect?: HighlightRect
  },
): Promise<CreateCommentResult> {
  return api.post<CreateCommentResult>(`/api/projects/${id}/comments`, body)
}

/** 截图上传（T1.2 链路）：Blob → /shots 临时区，返回访问 URL（预览用）。
 * slug 口径：临时区目录名与 /data/repos/{slug} 一致，评论提交时后端按 slug 取文件。 */
export function uploadShot(
  slug: string,
  blob: Blob,
  requestId: string,
  highlightRect: HighlightRect | null,
): Promise<{ shot_url: string }> {
  const fd = new FormData()
  fd.append('screenshot', blob, 'screenshot.png')
  fd.append('request_id', requestId)
  if (highlightRect) fd.append('highlight_rect', JSON.stringify(highlightRect))
  return api.upload<{ shot_url: string }>(`/api/projects/${slug}/shots`, fd)
}
