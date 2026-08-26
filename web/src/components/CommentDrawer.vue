<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  batchStatus,
  deleteComment,
  editComment,
  type CommentItem,
  type CommentStatus,
} from '../projects'

/**
 * T4.4 评论列表抽屉（产品方案 §4.4 布局：查看器底部横条）。
 *
 * - 按页面分组：dom/page 评论按 prototype_page，doc_block 归「PRD 文档」组
 * - 组内同位置合并：dom 按 anchor_id||nearest||css_path 聚合显示 ×N 角标，
 *   点开折叠组（产品方案「同位置多条合并角标」）
 * - 筛选：宿主类型（全部/原型/文档）+ 状态（四态）——本地筛（数据量小即时切换）
 * - 批量操作：勾选 → 确认（待确认→已确认待修改）/ 忽略；每条一个落仓任务
 * - 编辑/删除规则（产品方案 §4.5）：作者本人 + 待确认/已确认待修改态才可操作
 * - 每条评论「定位」按钮（T4.4 修订）：emit locate → Viewer 定位文档段落/
 *   原型元素（跨页/跨文档切换 + 高亮闪烁）
 * - focusKey（文档角标点击传入）：展开对应合并组 + 滚动 + 高亮 2s
 * - T4.5：项目关闭可评论时批量按钮置灰、编辑/删除按钮隐藏（后端同步拦截，
 *   这些操作都写 reviews/，属开关要消除的双写窗口；查看不受影响）
 */
const props = defineProps<{
  projectId: number
  comments: CommentItem[]
  currentUserEmail: string
  focusKey?: string
  commentable?: boolean
  // T8.4：创建者（批量流转/跨状态编辑删除/标记已修改专属）
  isCreator?: boolean
}>()

const emit = defineEmits<{
  refresh: []
  locate: [comment: CommentItem]
}>()

const rootEl = ref<HTMLElement | null>(null)

const STATUS_OPTIONS: CommentStatus[] = ['待确认', '已确认待修改', '已修改', '忽略', '延后再改']
const EDITABLE: CommentStatus[] = ['待确认', '已确认待修改']

const hostFilter = ref<'all' | 'proto' | 'doc'>('all')
const statusFilter = ref<'all' | CommentStatus>('all')
const checked = ref<Set<string>>(new Set())
// T8.6：折叠状态反向存储（默认空集 = 全部展开）。页面级用 collapsedGroups，元素/段落级用 collapsedLocs。
const collapsedGroups = ref<Set<string>>(new Set())
const collapsedLocs = ref<Set<string>>(new Set())
const busy = ref(false)

/** 编辑弹层状态 */
const editing = ref<CommentItem | null>(null)
const editContent = ref('')

/** 位置键：同页面同锚点算同位置（合并展示口径）。
 * doc 分支与 Viewer.docLocKeyOf 同口径（payload.doc_anchor_id 优先，
 * 无锚点退 doc_path）——文档段落角标匹配与 focusKey 定位都靠它对齐。 */
function locKey(c: CommentItem): string {
  if (c.target_type === 'doc_block') {
    return (
      'doc|' +
      (c.payload.doc_file || '') +
      '|' +
      (c.payload.doc_anchor_id || c.payload.doc_path || '')
    )
  }
  if (c.target_type === 'page') return 'page|' + c.prototype_page
  return (
    'dom|' + c.prototype_page + '|' + (c.anchor_id || c.payload.nearest_anchor_id || '')
  )
}

const filtered = computed(() =>
  props.comments.filter((c) => {
    if (hostFilter.value === 'proto' && c.target_type === 'doc_block') return false
    if (hostFilter.value === 'doc' && c.target_type !== 'doc_block') return false
    if (statusFilter.value !== 'all' && c.status !== statusFilter.value) return false
    return true
  }),
)

interface Group {
  key: string
  title: string
  locs: { key: string; label: string; items: CommentItem[] }[]
}
/** 按页面分组 → 组内按位置合并 */
const groups = computed<Group[]>(() => {
  const byPage = new Map<string, CommentItem[]>()
  for (const c of filtered.value) {
    const g = c.target_type === 'doc_block' ? 'PRD 文档' : c.prototype_page || '（未知页面）'
    if (!byPage.has(g)) byPage.set(g, [])
    byPage.get(g)!.push(c)
  }
  const out: Group[] = []
  for (const [title, items] of byPage) {
    const byLoc = new Map<string, CommentItem[]>()
    for (const c of items) {
      const k = locKey(c)
      if (!byLoc.has(k)) byLoc.set(k, [])
      byLoc.get(k)!.push(c)
    }
    out.push({
      key: title,
      title,
      locs: [...byLoc.entries()].map(([k, list]) => ({
        key: k,
        label: locLabel(list[0]),
        items: list,
      })),
    })
  }
  return out
})

function locLabel(c: CommentItem): string {
  if (c.target_type === 'doc_block') return c.anchor_id || c.payload.doc_path || '（无锚点段落）'
  if (c.target_type === 'page') return '本页整体'
  return c.anchor_id || c.payload.nearest_anchor_id || '（非锚点元素）'
}

function toggleCheck(cid: string) {
  const s = new Set(checked.value)
  if (s.has(cid)) s.delete(cid)
  else s.add(cid)
  checked.value = s
}

// T8.6：页面级 / 元素段落级折叠切换（默认空集 = 全部展开）
function toggleGroup(key: string) {
  const s = new Set(collapsedGroups.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  collapsedGroups.value = s
}
function toggleLoc(key: string) {
  const s = new Set(collapsedLocs.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  collapsedLocs.value = s
}
function groupItemCount(g: Group): number {
  return g.locs.reduce((n, l) => n + l.items.length, 0)
}

const checkedList = computed(() =>
  props.comments.filter((c) => checked.value.has(c.comment_id)),
)

// T 增强：批量修改状态——任意状态 → 任意目标状态（无硬性状态机限制）；
// 仍仅创建者可操作（后端逐条校验），项目可评论关闭时禁用。
async function onBatchStatus(target: CommentStatus) {
  const items = checkedList.value
  if (!items.length || busy.value) return
  if (props.commentable === false) {
    ElMessage.warning('项目已关闭评论，无法修改状态')
    return
  }
  busy.value = true
  try {
    const res = await batchStatus(
      items.map((c) => c.comment_id),
      target,
    )
    if (res.updated.length) {
      ElMessage.success(`已修改 ${res.updated.length} 条为「${target}」`)
    }
    if (res.skipped.length) {
      const hasPermission = res.skipped.some((s) => s.reason.includes('创建者'))
      ElMessage.warning(
        hasPermission ? `${res.skipped.length} 条不可操作（权限/关闭评论）` : `${res.skipped.length} 条跳过`,
      )
    }
    checked.value = new Set()
    emit('refresh')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '操作失败')
  } finally {
    busy.value = false
  }
}

function startEdit(c: CommentItem) {
  editing.value = c
  editContent.value = c.payload.content
}

async function saveEdit() {
  const c = editing.value
  if (!c || busy.value) return
  if (!editContent.value.trim()) {
    ElMessage.warning('评论内容不能为空')
    return
  }
  busy.value = true
  try {
    await editComment(c.comment_id, {
      content: editContent.value.trim(),
    })
    ElMessage.success('已保存')
    editing.value = null
    emit('refresh')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    busy.value = false
  }
}

async function onDelete(c: CommentItem) {
  try {
    await ElMessageBox.confirm(
      `删除评论 ${c.comment_id}？将同时删除项目目录中的评论文件与截图。`,
      '删除评论',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deleteComment(c.comment_id)
    ElMessage.success('已删除')
    checked.value.delete(c.comment_id)
    emit('refresh')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败')
  }
}

/** T8.4 §6：创建者可编辑/删除任意状态；作者限自己的评论且仅
 * 待确认/已确认待修改态；「可评论」关闭时一律不允许（写操作冻结）。 */
function canEdit(c: CommentItem): boolean {
  if (props.commentable === false) return false
  if (props.isCreator) return true
  return (
    c.author_email === props.currentUserEmail &&
    EDITABLE.includes(c.status)
  )
}

// ───────────────── T8.5 抽屉增强：单条标记已修改 + 截图放大 ─────────────────

/** 单条「标记已修改」：创建者专属，仅「已确认待修改」态的快捷入口
 * （T 增强：底层已是任意→任意，这里只是快捷方式）。 */
function canMarkDone(c: CommentItem): boolean {
  return (
    props.isCreator &&
    props.commentable !== false &&
    c.status === '已确认待修改'
  )
}

async function markDoneOne(c: CommentItem) {
  if (busy.value) return
  busy.value = true
  try {
    const res = await batchStatus([c.comment_id], '已修改')
    if (res.updated.length) {
      ElMessage.success('已标记为已修改')
      emit('refresh')
    } else {
      ElMessage.warning(res.skipped[0]?.reason || '不可操作')
    }
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '操作失败')
  } finally {
    busy.value = false
  }
}

/** 截图缩略图放大：全屏遮罩预览当前评论截图（T8.5）。 */
const previewShot = ref<CommentItem | null>(null)
function openShot(c: CommentItem) {
  previewShot.value = c
}
function closeShot() {
  previewShot.value = null
}

/** 评论截图 URL（T8.5：项目目录截图服务接口）。 */
function shotUrl(c: CommentItem): string {
  return `/api/comments/${c.comment_id}/shot`
}

function statusTagType(s: CommentStatus): 'info' | 'warning' | 'success' | 'danger' {
  if (s === '待确认') return 'warning'
  if (s === '已确认待修改') return 'danger'
  return 'info'
}

// 筛选变化时清掉失效勾选（被筛掉的条目不应留在勾选集）
watch([hostFilter, statusFilter], () => {
  const visible = new Set(filtered.value.map((c) => c.comment_id))
  checked.value = new Set([...checked.value].filter((cid) => visible.has(cid)))
})

// focusKey（文档段落角标点击）：确保对应合并组展开 + 滚动到该位置 + 高亮 2s。
// key 值含 | 与中文（不做属性选择器查询，遍历比较 dataset 稳）。默认全展开，
// 这里只需把可能被折叠的组/位置取消折叠。
watch(
  () => props.focusKey,
  (k) => {
    if (!k) return
    const gs = new Set(collapsedGroups.value)
    gs.delete(k)
    collapsedGroups.value = gs
    const ls = new Set(collapsedLocs.value)
    ls.delete(k)
    collapsedLocs.value = ls
    nextTick(() => {
      const locs = rootEl.value?.querySelectorAll<HTMLElement>('[data-lock]') || []
      for (const loc of locs) {
        if (loc.dataset.lock === k) {
          loc.scrollIntoView({ behavior: 'smooth', block: 'center' })
          loc.classList.add('loc-focus')
          setTimeout(() => loc.classList.remove('loc-focus'), 2000)
          return
        }
      }
    })
  },
)
</script>

<template>
  <div ref="rootEl" class="drawer" data-testid="comment-drawer">
    <!-- 工具栏 -->
    <!-- 标题栏（与原型/PRD 顶栏同款 pane-head 样式）：评论数 + 已选数 -->
    <div class="drawer-head">
      <span class="head-title">评论（{{ filtered.length }}）</span>
      <span class="head-selected">已选 {{ checkedList.length }}</span>
    </div>

    <!-- 筛选行：宿主 + 状态 -->
    <div class="drawer-filter">
      <label class="f">
        宿主
        <select v-model="hostFilter" data-testid="filter-host">
          <option value="all">全部</option>
          <option value="proto">原型</option>
          <option value="doc">文档</option>
        </select>
      </label>
      <label class="f">
        状态
        <select v-model="statusFilter" data-testid="filter-status">
          <option value="all">全部</option>
          <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
    </div>

    <!-- T 增强：批量修改状态（单个按钮 + 目标状态菜单；任意→任意，仅创建者） -->
    <div v-if="isCreator" class="drawer-actions">
      <el-dropdown
        :disabled="!checkedList.length || busy || commentable === false"
        trigger="click"
        @command="onBatchStatus"
      >
        <button
          class="op confirm batch-status"
          :disabled="!checkedList.length || busy || commentable === false"
          data-testid="batch-status-btn"
          title="将勾选的评论统一改为目标状态（任意状态→任意状态）"
        >
          批量修改状态（{{ checkedList.length }}）▾
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item
              v-for="s in STATUS_OPTIONS"
              :key="s"
              :command="s"
              :data-testid="`batch-to-${s}`"
            >
              改为「{{ s }}」
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <!-- 列表 -->
    <div class="list">
      <p v-if="!filtered.length" class="empty">暂无评论——开启评论模式后在原型或文档上评论</p>
      <section v-for="g in groups" :key="g.key" class="group">
        <!-- 页面级标题（可折叠：上一级收起展开） -->
        <div class="group-head" :class="{ collapsed: collapsedGroups.has(g.key) }" @click="toggleGroup(g.key)">
          <span class="group-title" data-testid="comment-group-title">{{ g.title }}</span>
          <span class="group-cnt">{{ groupItemCount(g) }} 条</span>
          <span class="group-hint">{{ collapsedGroups.has(g.key) ? '展开' : '收起' }}</span>
        </div>
        <template v-if="!collapsedGroups.has(g.key)">
          <div v-for="loc in g.locs" :key="loc.key" class="loc" :data-lock="loc.key">
            <!-- 元素/段落位置行（多条合并角标，可折叠；默认展开） -->
            <div
              class="loc-head"
              :class="{ multi: loc.items.length > 1, collapsed: collapsedLocs.has(loc.key) }"
              data-testid="comment-loc"
              @click="loc.items.length > 1 && toggleLoc(loc.key)"
            >
              <code class="loc-label">{{ loc.label }}</code>
              <span v-if="loc.items.length > 1" class="loc-count" data-testid="loc-count">
                ×{{ loc.items.length }}
              </span>
              <span v-else class="loc-single">1 条</span>
              <span v-if="loc.items.length > 1" class="loc-hint">
                {{ collapsedLocs.has(loc.key) ? '展开' : '收起' }}
              </span>
            </div>
            <template v-if="loc.items.length === 1 || !collapsedLocs.has(loc.key)">
            <article
              v-for="c in loc.items"
              :key="c.comment_id"
              class="item"
              :data-cid="c.comment_id"
              :data-status="c.status"
            >
              <input
                type="checkbox"
                class="ck"
                :checked="checked.has(c.comment_id)"
                :data-testid="`ck-${c.comment_id}`"
                @change="toggleCheck(c.comment_id)"
              />
              <div class="item-main">
                <div class="item-meta">
                  <code class="cid">{{ c.comment_id }}</code>
                  <el-tag size="small" :type="statusTagType(c.status)" data-testid="comment-status">
                    {{ c.status }}
                  </el-tag>
                  <span class="author">{{ c.author_name }}</span>
                  <span class="time">{{ c.created_at.slice(0, 16).replace('T', ' ') }}</span>
                  <span class="ops">
                    <!-- T4.4 定位：文档评论→段落高亮；原型评论→切页+元素闪烁 -->
                    <button
                      class="op"
                      :data-testid="`locate-${c.comment_id}`"
                      title="定位到评论目标（文档段落 / 原型元素）"
                      @click="emit('locate', c)"
                    >
                      定位
                    </button>
                    <!-- T8.5 单条标记已修改（创建者，仅已确认待修改态） -->
                    <button
                      v-if="canMarkDone(c)"
                      class="op mark"
                      :disabled="busy"
                      :data-testid="`mark-done-${c.comment_id}`"
                      title="已确认待修改 → 已修改（状态闭环，创建者）"
                      @click="markDoneOne(c)"
                    >
                      标记已修改
                    </button>
                    <template v-if="canEdit(c)">
                      <button class="op" data-testid="edit-comment" @click="startEdit(c)">编辑</button>
                      <button class="op danger" data-testid="del-comment" @click="onDelete(c)">删除</button>
                    </template>
                  </span>
                </div>
                <p class="content">{{ c.payload.content }}</p>
                <!-- T8.5 文档段落摘录块：doc_block 评论或 DOM 评论派生 doc_excerpt 的展示锚定段落 -->
                <div v-if="c.payload.doc_excerpt" class="doc-excerpt" data-testid="doc-excerpt">
                  <span class="de-label">{{
                    c.payload.doc_anchor_id || c.payload.doc_path || '文档段落'
                  }}</span>
                  {{ c.payload.doc_excerpt }}
                </div>
                <!-- T8.5 截图缩略图：有截图的评论展示，点击放大 -->
                <img
                  v-if="c.payload.screenshot"
                  :src="shotUrl(c)"
                  class="shot-thumb"
                  :data-testid="`shot-thumb-${c.comment_id}`"
                  alt="评论截图缩略图"
                  title="点击放大查看截图"
                  loading="lazy"
                  @click="openShot(c)"
                />
              </div>
            </article>
          </template>
        </div>
        </template>
      </section>
    </div>

    <!-- 编辑弹层 -->
    <div v-if="editing" class="edit-mask" data-testid="edit-dialog">
      <div class="edit-box">
        <div class="edit-head">
          编辑评论 <code>{{ editing.comment_id }}</code>
          <button class="op" @click="editing = null">关闭</button>
        </div>
        <textarea v-model="editContent" rows="4" maxlength="2000" data-testid="edit-content" />
        <div class="edit-row">
          <button
            class="op confirm"
            :disabled="busy || !editContent.trim()"
            data-testid="edit-save"
            @click="saveEdit"
          >
            保存
          </button>
        </div>
      </div>
    </div>

    <!-- T8.5 截图放大遮罩（点击缩略图打开，遮罩关闭） -->
    <div v-if="previewShot" class="shot-mask" data-testid="shot-preview-mask" @click.self="closeShot">
      <div class="shot-box">
        <div class="shot-head">
          <code>{{ previewShot.comment_id }}</code>
          <button class="op" data-testid="shot-close" @click="closeShot">关闭</button>
        </div>
        <img :src="shotUrl(previewShot)" alt="评论截图放大" data-testid="shot-preview-img" />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* T8.6：抽屉从底部横条改为右侧栏（_pane.comments 内 flex:1 填满高度） */
.drawer {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--pp-border);
  background: var(--pp-surface);
}
/* 标题栏（与原型/PRD pane-head 同款样式） */
.drawer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 14px;
  background: var(--pp-surface);
  border-bottom: 1px solid var(--pp-border);
  font-size: 13px;
  color: var(--pp-text-2);
  flex-shrink: 0;
  font-weight: 500;
}
.drawer-head .head-title { font-weight: 600; color: var(--pp-text-1); }
.drawer-head .head-selected { color: var(--pp-primary); font-size: 12px; }

/* 筛选行：宿主 + 状态 */
.drawer-filter {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 7px 14px;
  border-bottom: 1px solid var(--pp-border);
  font-size: 12px;
  color: var(--pp-text-2);
  flex-shrink: 0;
}
.f { display: inline-flex; align-items: center; gap: 4px; }
.f select {
  border: 1px solid var(--pp-border-strong);
  border-radius: var(--pp-radius-xs);
  padding: 2px 6px;
  font-size: 12px;
  background: var(--pp-surface);
  color: var(--pp-text-1);
}
.f select:focus {
  outline: none;
  border-color: var(--pp-primary);
}

/* 按钮行：四个批量操作（创建者） */
.drawer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border-bottom: 1px solid var(--pp-border);
  flex-shrink: 0;
}
.op {
  border: 1px solid var(--pp-border-strong);
  border-radius: 999px;
  background: var(--pp-surface);
  padding: 3px 12px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  color: var(--pp-text-2);
  transition: all 0.15s ease;
}
.op:hover:not(:disabled) {
  border-color: var(--pp-primary);
  color: var(--pp-primary);
  background: var(--pp-primary-soft);
}
.op:disabled { opacity: 0.45; cursor: not-allowed; }
.op.confirm { background: var(--pp-primary); border-color: var(--pp-primary); color: #fff; }
.op.confirm:hover:not(:disabled) {
  background: var(--pp-primary-hover);
  color: #fff;
}
.op.ignore:hover:not(:disabled) { border-color: #9a6b00; color: #9a6b00; background: var(--pp-warning-bg); }
.op.mark:hover:not(:disabled) { border-color: var(--pp-success); color: var(--pp-success); background: var(--pp-success-bg); }
.op.rework:hover:not(:disabled) { border-color: var(--pp-warning); color: var(--pp-warning); background: var(--pp-warning-bg); }
.op.danger:hover { border-color: var(--pp-danger); color: var(--pp-danger); background: var(--pp-danger-bg); }

.list { flex: 1; overflow-y: auto; padding: 10px 14px; user-select: none; }
.empty { color: var(--pp-text-3); font-size: 12px; padding: 12px 0; }
.group { margin-bottom: 10px; }
/* 页面级标题（可折叠） */
.group-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--pp-primary);
  margin: 8px 0 4px;
  font-weight: 600;
  cursor: pointer;
  user-select: none;
}
.group-head:hover { color: var(--pp-primary-hover); }
.group-title { font-size: 12px; color: var(--pp-primary); font-weight: 600; }
.group-cnt { color: var(--pp-text-3); font-size: 11px; font-weight: 400; }
.group-hint { color: var(--pp-primary); font-size: 11px; font-weight: 400; margin-left: auto; }
.loc { margin-bottom: 4px; }
/* focusKey 定位高亮（文档角标点击进来时 2s） */
.loc.loc-focus {
  background: #fff3d6;
  box-shadow: 0 0 0 2px #ffd66e;
  border-radius: var(--pp-radius-sm);
  padding: 2px 4px;
}
.loc-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--pp-radius-xs);
  background: var(--pp-surface-2);
  margin-bottom: 2px;
}
.loc-head.multi { cursor: pointer; }
.loc-head.multi:hover { background: var(--pp-primary-soft); }
.loc-head.multi.collapsed { opacity: 0.75; }
.loc-label { color: var(--pp-text-2); font-size: 11px; font-family: var(--pp-mono); }
.loc-count {
  background: #e5484d;
  color: #fff;
  border-radius: 8px;
  padding: 0 5px;
  font-size: 10px;
  line-height: 15px;
  font-weight: 600;
}
.loc-single { color: var(--pp-text-3); font-size: 10px; }
.loc-hint { color: var(--pp-primary); font-size: 10px; }

.item {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 7px 10px;
  border-radius: var(--pp-radius-sm);
  transition: background 0.15s ease;
}
.item:hover { background: var(--pp-primary-soft); }
.ck { margin-top: 3px; accent-color: var(--pp-primary); }
.item-main { flex: 1; min-width: 0; }
.item-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--pp-text-3);
  flex-wrap: wrap;
}
.cid { font-family: var(--pp-mono); font-size: 10.5px; color: var(--pp-primary); }
.author { color: var(--pp-text-2); }
.time { color: var(--pp-text-4); }
.ops { display: inline-flex; gap: 4px; margin-left: 4px; }
.ops .op { padding: 0 10px; font-size: 11px; border-radius: 999px; }
.content {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--pp-text-1);
  line-height: 1.55;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* T8.5 文档段落摘录块 */
.doc-excerpt {
  margin-top: 6px;
  padding: 7px 10px;
  background: var(--pp-primary-soft);
  border-left: 3px solid var(--pp-primary);
  border-radius: 0 var(--pp-radius-xs) var(--pp-radius-xs) 0;
  font-size: 12px;
  color: var(--pp-text-2);
  line-height: 1.5;
}
.doc-excerpt .de-label {
  display: inline-block;
  margin-right: 6px;
  color: var(--pp-primary);
  font-weight: 600;
  font-size: 11px;
}

/* T8.5 截图缩略图 */
.shot-thumb {
  display: block;
  margin-top: 6px;
  max-width: 220px;
  max-height: 120px;
  border: 1px solid var(--pp-border);
  border-radius: var(--pp-radius-sm);
  cursor: zoom-in;
  background: var(--pp-surface-2);
}

/* T8.5 截图放大遮罩 */
.shot-mask {
  position: fixed;
  inset: 0;
  background: rgba(20, 24, 33, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 120;
}
.shot-box {
  background: var(--pp-surface);
  border-radius: var(--pp-radius);
  padding: 12px 16px;
  max-width: 92vw;
  max-height: 92vh;
  display: flex;
  flex-direction: column;
  gap: 8px;
  box-shadow: var(--pp-shadow-lg);
}
.shot-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--pp-text-1);
}
.shot-head .op { margin-left: auto; }
.shot-box img {
  max-width: 90vw;
  max-height: 80vh;
  border-radius: var(--pp-radius-xs);
  object-fit: contain;
  background: var(--pp-surface);
}

/* 编辑弹层 */
.edit-mask {
  position: fixed;
  inset: 0;
  background: rgba(20, 24, 33, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.edit-box {
  background: var(--pp-surface);
  border-radius: var(--pp-radius);
  padding: 18px;
  width: 480px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: var(--pp-shadow-lg);
}
.edit-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--pp-text-1);
}
.edit-head .op { margin-left: auto; }
.edit-box textarea {
  border: 1px solid var(--pp-border-strong);
  border-radius: var(--pp-radius-sm);
  padding: 8px 10px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
  color: var(--pp-text-1);
}
.edit-box textarea:focus {
  outline: none;
  border-color: var(--pp-primary);
  box-shadow: 0 0 0 3px rgba(79, 99, 210, 0.12);
}
.edit-row { display: flex; align-items: center; gap: 14px; font-size: 12px; color: var(--pp-text-2); }
.edit-row label { display: inline-flex; align-items: center; gap: 4px; }
.edit-row select {
  border: 1px solid var(--pp-border-strong);
  border-radius: var(--pp-radius-xs);
  padding: 2px 6px;
}
.edit-row .op { margin-left: auto; padding: 4px 18px; }
</style>
