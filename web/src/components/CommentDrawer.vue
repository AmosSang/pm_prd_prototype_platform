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
 */
const props = defineProps<{
  projectId: number
  comments: CommentItem[]
  currentUserEmail: string
  focusKey?: string
}>()

const emit = defineEmits<{
  refresh: []
  locate: [comment: CommentItem]
}>()

const rootEl = ref<HTMLElement | null>(null)

const STATUS_OPTIONS: CommentStatus[] = ['待确认', '已确认待修改', '已修改', '忽略']
const EDITABLE: CommentStatus[] = ['待确认', '已确认待修改']

const hostFilter = ref<'all' | 'proto' | 'doc'>('all')
const statusFilter = ref<'all' | CommentStatus>('all')
const checked = ref<Set<string>>(new Set())
const expanded = ref<Set<string>>(new Set()) // 展开的合并组 key
const busy = ref(false)

/** 编辑弹层状态 */
const editing = ref<CommentItem | null>(null)
const editContent = ref('')
const editPriority = ref('P2')
const editScope = ref('prototype')

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

function toggleExpand(key: string) {
  const s = new Set(expanded.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  expanded.value = s
}

const checkedList = computed(() =>
  props.comments.filter((c) => checked.value.has(c.comment_id)),
)

/** 可被当前动作处理的勾选项（状态机合法的才提交，其余后端也会跳过） */
function actionable(action: 'confirm' | 'ignore'): CommentItem[] {
  const from = action === 'confirm' ? ['待确认'] : ['待确认', '已确认待修改']
  return checkedList.value.filter((c) => from.includes(c.status))
}

async function onBatch(action: 'confirm' | 'ignore') {
  const items = actionable(action)
  if (!items.length || busy.value) return
  busy.value = true
  try {
    const res = await batchStatus(
      items.map((c) => c.comment_id),
      action,
    )
    if (res.updated.length) {
      ElMessage.success(`已${action === 'confirm' ? '确认' : '忽略'} ${res.updated.length} 条`)
    }
    if (res.skipped.length) {
      ElMessage.warning(`${res.skipped.length} 条不可操作（状态不符）`)
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
  editPriority.value = c.priority
  editScope.value = c.scope
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
      priority: editPriority.value,
      scope: editScope.value,
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
      `删除评论 ${c.comment_id}？将同时删除仓库中的评论文件与截图。`,
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

function canEdit(c: CommentItem): boolean {
  return c.author_email === props.currentUserEmail && EDITABLE.includes(c.status)
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

// focusKey（文档段落角标点击）：展开对应合并组 + 滚动到该位置 + 高亮 2s。
// key 值含 | 与中文（不做属性选择器查询，遍历比较 dataset 稳）
watch(
  () => props.focusKey,
  (k) => {
    if (!k) return
    const s = new Set(expanded.value)
    s.add(k)
    expanded.value = s
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
    <div class="bar">
      <span class="bar-title">评论（{{ filtered.length }}）</span>
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
      <span class="batch">
        已选 {{ checkedList.length }}
        <button
          class="op confirm"
          :disabled="!actionable('confirm').length || busy"
          data-testid="batch-confirm"
          title="待确认 → 已确认待修改（落仓）"
          @click="onBatch('confirm')"
        >
          批量确认
        </button>
        <button
          class="op ignore"
          :disabled="!actionable('ignore').length || busy"
          data-testid="batch-ignore"
          title="标记不处理（落仓）"
          @click="onBatch('ignore')"
        >
          批量忽略
        </button>
      </span>
    </div>

    <!-- 列表 -->
    <div class="list">
      <p v-if="!filtered.length" class="empty">暂无评论——开启评论模式后在原型或文档上评论</p>
      <section v-for="g in groups" :key="g.key" class="group">
        <h4 class="group-title" data-testid="comment-group-title">{{ g.title }}</h4>
        <div v-for="loc in g.locs" :key="loc.key" class="loc" :data-lock="loc.key">
          <!-- 位置行（多条时显示 ×N 合并角标，点开折叠） -->
          <div
            class="loc-head"
            :class="{ multi: loc.items.length > 1, open: expanded.has(loc.key) }"
            data-testid="comment-loc"
            @click="loc.items.length > 1 && toggleExpand(loc.key)"
          >
            <code class="loc-label">{{ loc.label }}</code>
            <span v-if="loc.items.length > 1" class="loc-count" data-testid="loc-count">
              ×{{ loc.items.length }}
            </span>
            <span v-else class="loc-single">1 条</span>
            <span v-if="loc.items.length > 1" class="loc-hint">
              {{ expanded.has(loc.key) ? '收起' : '展开' }}
            </span>
          </div>
          <template v-if="loc.items.length === 1 || expanded.has(loc.key)">
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
                  <span class="pri">{{ c.priority }}</span>
                  <span class="scope">{{ c.scope === 'prototype' ? '原型' : c.scope === 'doc' ? '文档' : '两侧' }}</span>
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
                    <template v-if="canEdit(c)">
                      <button class="op" data-testid="edit-comment" @click="startEdit(c)">编辑</button>
                      <button class="op danger" data-testid="del-comment" @click="onDelete(c)">删除</button>
                    </template>
                  </span>
                </div>
                <p class="content">{{ c.payload.content }}</p>
              </div>
            </article>
          </template>
        </div>
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
          <label>
            优先级
            <select v-model="editPriority" data-testid="edit-priority">
              <option value="P1">P1 高</option>
              <option value="P2">P2 中</option>
              <option value="P3">P3 低</option>
            </select>
          </label>
          <label>
            修改范围
            <select v-model="editScope" data-testid="edit-scope">
              <option value="prototype">仅原型</option>
              <option value="doc">仅文档</option>
              <option value="both">两侧同改</option>
            </select>
          </label>
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
  </div>
</template>

<style scoped>
.drawer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  height: 240px;
  border-top: 1px solid #d8dde4;
  background: #fff;
}
.bar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 6px 14px;
  border-bottom: 1px solid #eef0f3;
  font-size: 12px;
  color: #57606a;
  flex-shrink: 0;
}
.bar-title { font-size: 13px; font-weight: 600; color: #24292f; }
.f { display: inline-flex; align-items: center; gap: 4px; }
.f select { border: 1px solid #d9dce1; border-radius: 4px; padding: 1px 4px; font-size: 12px; }
.batch { margin-left: auto; display: inline-flex; align-items: center; gap: 8px; }
.op {
  border: 1px solid #d9dce1;
  border-radius: 4px;
  background: #fff;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.op:hover:not(:disabled) { border-color: #2b5cff; color: #2b5cff; }
.op:disabled { opacity: 0.45; cursor: not-allowed; }
.op.confirm { background: #2b5cff; border-color: #2b5cff; color: #fff; }
.op.confirm:hover:not(:disabled) { background: #1e4fd8; color: #fff; }
.op.ignore:hover:not(:disabled) { border-color: #b45200; color: #b45200; }
.op.danger:hover { border-color: #d33; color: #d33; }

.list { flex: 1; overflow-y: auto; padding: 8px 14px; user-select: none; }
.empty { color: #999; font-size: 12px; padding: 12px 0; }
.group { margin-bottom: 10px; }
.group-title {
  font-size: 12px;
  color: #2b5cff;
  margin: 6px 0 4px;
  font-weight: 600;
}
.loc { margin-bottom: 4px; }
/* focusKey 定位高亮（文档角标点击进来时 2s） */
.loc.loc-focus {
  background: #fff3d6;
  box-shadow: 0 0 0 2px #ffd66e;
  border-radius: 6px;
  padding: 2px 4px;
}
.loc-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #f6f8fa;
  margin-bottom: 2px;
}
.loc-head.multi { cursor: pointer; }
.loc-head.multi:hover { background: #eef2f8; }
.loc-label { color: #57606a; font-size: 11px; }
.loc-count {
  background: #e5484d;
  color: #fff;
  border-radius: 8px;
  padding: 0 5px;
  font-size: 10px;
  line-height: 15px;
  font-weight: 600;
}
.loc-single { color: #999; font-size: 10px; }
.loc-hint { color: #2b5cff; font-size: 10px; }

.item {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 5px 8px;
  border-radius: 6px;
}
.item:hover { background: #f9fbfd; }
.ck { margin-top: 3px; }
.item-main { flex: 1; min-width: 0; }
.item-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: #8a919b;
  flex-wrap: wrap;
}
.cid { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10.5px; color: #2b5cff; }
.pri { color: #b8860b; }
.author { color: #57606a; }
.time { color: #b0b7c0; }
.ops { display: inline-flex; gap: 4px; margin-left: 4px; }
.ops .op { padding: 0 8px; font-size: 11px; }
.content {
  margin: 3px 0 0;
  font-size: 12.5px;
  color: #24292f;
  line-height: 1.5;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* 编辑弹层 */
.edit-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.edit-box {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  width: 480px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.edit-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
}
.edit-head .op { margin-left: auto; }
.edit-box textarea {
  border: 1px solid #d9dce1;
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
}
.edit-box textarea:focus { outline: none; border-color: #2b5cff; }
.edit-row { display: flex; align-items: center; gap: 14px; font-size: 12px; color: #57606a; }
.edit-row label { display: inline-flex; align-items: center; gap: 4px; }
.edit-row select { border: 1px solid #d9dce1; border-radius: 4px; padding: 2px 4px; }
.edit-row .op { margin-left: auto; padding: 4px 16px; }
</style>
