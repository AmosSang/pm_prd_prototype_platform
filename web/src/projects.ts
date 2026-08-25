/** 项目相关 API 与类型（T2.3 / T2.4；T8.1 去 Git 本地化修订）。 */
import { api } from './api'

/** 项目创建者（创建者权限体系：上传/导出/开关/管理） */
export interface Creator {
  id: number
  name: string
  email: string
}

export interface ProjectInfo {
  id: number
  project_id: string
  name: string
  creator: Creator
  is_creator: boolean
  commentable: boolean
  content_updated_at: string | null
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

/** 创建项目（T8.1）：只填名称；创建后为空项目，内容由上传接口补充（T8.2）。 */
export function createProject(payload: { name: string }): Promise<ProjectInfo> {
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

/** 项目级「可评论」开关（T4.5）：关闭后全员评论入口置灰（已有评论可查看）。 */
export function updateProject(id: number, body: { commentable: boolean }): Promise<ProjectInfo> {
  return api.patch<ProjectInfo>(`/api/projects/${id}`, body)
}

// ───────────────────── 内容上传与删除（T8.2，创建者专属）─────────────────────

/** 上传原型 zip（带进度回调；≤100MB，安全校验与原子替换在后端）。 */
export function uploadPrototype(
  id: number,
  file: File,
  onProgress: (percent: number) => void = () => {},
): Promise<ProjectInfo> {
  const fd = new FormData()
  fd.append('zip', file, file.name)
  return api.uploadWithProgress<ProjectInfo>(`/api/projects/${id}/prototype`, fd, onProgress)
}

/** 上传 PRD markdown（≤5MB，替换 prd/ 旧文档）。 */
export function uploadPrd(id: number, file: File): Promise<ProjectInfo> {
  const fd = new FormData()
  fd.append('file', file, file.name)
  return api.upload<ProjectInfo>(`/api/projects/${id}/prd`, fd)
}

/** 删除项目（目录 + 评论 + DB；仅创建者）。 */
export function deleteProject(id: number): Promise<{ deleted: boolean; project_id: string }> {
  return api.delete(`/api/projects/${id}`)
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
   * 无锚点段落：doc_anchor_id 空、doc_path（标题链）+ doc_excerpt 供指纹定位；
   * doc_file（当前文档路径）供定位/文档角标匹配 */
  doc_anchor_id?: string
  doc_excerpt?: string
  doc_path?: string
  doc_file?: string
}

export interface HighlightRect {
  x: number
  y: number
  w: number
  h: number
}

/** 评论提交结果（POST /comments 响应：评论 JSON 全量；T8.1 起直写文件，无落仓任务） */
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

// ───────────────────────── 评论列表/编辑/删除/批量状态（T4.4）──────────────────

export type CommentStatus = '待确认' | '已确认待修改' | '已修改' | '忽略'

/** 评论列表条目（GET /comments 响应；payload 为评论 JSON 全量） */
export interface CommentItem {
  comment_id: string
  author_name: string
  author_email: string
  status: CommentStatus
  priority: string
  scope: string
  target_type: 'dom' | 'page' | 'doc_block'
  prototype_page: string
  anchor_id: string
  created_at: string
  payload: {
    content: string
    text_excerpt?: string
    doc_excerpt?: string
    nearest_anchor_id?: string
    doc_path?: string
    doc_file?: string
    screenshot?: string
    [k: string]: unknown
  }
}

export function listComments(id: number): Promise<CommentItem[]> {
  return api.get<CommentItem[]>(`/api/projects/${id}/comments`)
}

export function editComment(
  cid: string,
  body: { content?: string; priority?: string; scope?: string },
): Promise<{ comment_id: string; updated: string[] }> {
  return api.patch(`/api/comments/${cid}`, body)
}

export function deleteComment(cid: string): Promise<{ comment_id: string; deleted: boolean }> {
  return api.delete(`/api/comments/${cid}`)
}

export function batchStatus(
  cids: string[],
  action: 'confirm' | 'ignore' | 'mark_done' | 'rework',
): Promise<{ action: string; to: string; updated: string[]; skipped: { comment_id: string; reason: string }[] }> {
  return api.post('/api/comments/batch-status', { cids, action })
}

/** 截图上传（T1.2 链路）：Blob → /shots 临时区，返回访问 URL（预览用）。
 * slug 口径：临时区目录名与项目目录 /data/projects/{slug} 一致，
 * 评论提交时后端按 slug 取文件并复制进项目 reviews/shots/（T8.1）。 */
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
