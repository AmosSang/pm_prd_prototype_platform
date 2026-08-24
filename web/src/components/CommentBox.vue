<script setup lang="ts">
import { ref, watch } from 'vue'
import type { CommentPayload, CreateCommentResult } from '../projects'

/**
 * T4.2 评论框（左侧底部，替换 T4.1 的采集结果面板）。
 *
 * 三态：
 * - composing：目标摘要（T4.1 采集字段，testid 保留）+ 表单
 *   （文字必填 / 优先级 P1-P3 默认 P2 / 范围默认按宿主推断 / 署名只读）
 * - submitting：由父组件控制（props.submitting），按钮 loading
 * - done：提交成功 + 截图预览（可查看不可编辑，产品方案 §4.5）
 *
 * 截图由父组件（Viewer）在提交时经 bridge 采集（提交时而非打开时——
 * 保证反映提交一刻状态）；本组件只管表单与展示。
 */
const props = defineProps<{
  payload: CommentPayload
  author: string
  submitting: boolean
  result: CreateCommentResult | null
  /** 截图预览 URL（临时区 /api/shots/...；doc_block 评论无） */
  shotPreviewUrl: string | null
  error: string
}>()

const emit = defineEmits<{
  submit: [form: { content: string; priority: string; scope: string }]
  close: []
}>()

const content = ref('')
const priority = ref('P2')
const scope = ref('prototype')

// 换目标（重新点元素）重置表单；scope 默认按宿主推断（产品方案 §4.5）：
// 文档评论 → doc，原型评论 → prototype
watch(
  () => props.payload,
  () => {
    content.value = ''
    priority.value = 'P2'
    scope.value = props.payload.target_type === 'doc_block' ? 'doc' : 'prototype'
  },
  { immediate: true },
)

function onSubmit() {
  if (!content.value.trim() || props.submitting) return
  emit('submit', {
    content: content.value.trim(),
    priority: priority.value,
    scope: scope.value,
  })
}
</script>

<template>
  <!-- 容器 testid 沿用 T4.1 的 payload-panel（点元素 → 本框出现） -->
  <div class="comment-box" data-testid="comment-box">
    <!-- 提交成功态 -->
    <template v-if="result">
      <div class="cb-done">
        <div class="cb-done-head">
          <span class="ok">✓ 已提交</span>
          <code class="cid" data-testid="submitted-cid">{{ result.comment_id }}</code>
          <span v-if="!result.git_pushed" class="push-fail" title="评论已保存到平台，稍后会重试同步">
            仓库同步失败
          </span>
          <button class="cb-btn" data-testid="comment-done" @click="emit('close')">完成</button>
        </div>
        <img
          v-if="shotPreviewUrl"
          class="cb-shot"
          :src="shotPreviewUrl"
          alt="评论截图（含目标红框）"
          data-testid="shot-preview"
        />
        <p v-else class="cb-noshot">（文档评论，无截图）</p>
      </div>
    </template>

    <!-- 填写态 -->
    <template v-else>
      <div class="cp-title">发表评论</div>

      <!-- 目标摘要（T4.1 采集字段，testid 保留） -->
      <div class="cp-grid">
        <span class="k">target_type</span>
        <b data-testid="payload-target-type">{{ payload.target_type }}</b>
        <template v-if="payload.target_type === 'doc_block'">
          <span class="k">doc_anchor_id</span>
          <b data-testid="payload-anchor">{{ payload.doc_anchor_id || '（无）' }}</b>
          <span class="k">doc_excerpt</span>
          <b data-testid="payload-text">{{ payload.doc_excerpt || '（无文本）' }}</b>
        </template>
        <template v-else>
          <span class="k">prototype_page</span>
          <b data-testid="payload-page">{{ payload.prototype_page }}</b>
          <span class="k">anchor_id</span>
          <b data-testid="payload-anchor">{{ payload.anchor_id || '（无）' }}</b>
          <span class="k">nearest_anchor_id</span>
          <b data-testid="payload-nearest">{{ payload.nearest_anchor_id || '（无）' }}</b>
          <span class="k">css_path</span>
          <b class="mono" data-testid="payload-css-path">{{ payload.css_path }}</b>
          <span class="k">text_excerpt</span>
          <b data-testid="payload-text">{{ payload.text_excerpt || '（无文本）' }}</b>
          <span class="k">modal_open</span>
          <b data-testid="payload-modal-open">{{ payload.interaction_state.modal_open }}</b>
          <span class="k">viewport</span>
          <b data-testid="payload-viewport">{{ payload.interaction_state.viewport }}</b>
          <span class="k">scroll_y</span>
          <b data-testid="payload-scroll-y">{{ payload.interaction_state.scroll_y }}</b>
          <span class="k">route</span>
          <b class="mono" data-testid="payload-route">{{ payload.interaction_state.route }}</b>
          <span class="k">outer_html</span>
          <details class="cp-details">
            <summary>展开（目标 + 祖先上下文）</summary>
            <code data-testid="payload-outer-html">{{ payload.outer_html }}</code>
          </details>
        </template>
      </div>

      <!-- 表单 -->
      <div class="cb-form">
        <textarea
          v-model="content"
          class="cb-content"
          rows="3"
          maxlength="2000"
          placeholder="评论内容（必填）：描述要修改什么、期望的效果……"
          data-testid="comment-content"
        />
        <div class="cb-row">
          <label>
            优先级
            <select v-model="priority" data-testid="comment-priority">
              <option value="P1">P1 高</option>
              <option value="P2">P2 中</option>
              <option value="P3">P3 低</option>
            </select>
          </label>
          <label>
            修改范围
            <select v-model="scope" data-testid="comment-scope">
              <option value="prototype">仅原型</option>
              <option value="doc">仅文档</option>
              <option value="both">两侧同改</option>
            </select>
          </label>
          <label class="cb-author">
            署名
            <input type="text" :value="author" readonly data-testid="comment-author" />
          </label>
          <span class="cb-actions">
            <button
              class="cb-btn primary"
              :disabled="!content.trim() || submitting"
              data-testid="comment-submit"
              @click="onSubmit"
            >
              {{ submitting ? '提交中…' : '提交（自动截图）' }}
            </button>
            <button class="cb-btn" :disabled="submitting" data-testid="comment-cancel" @click="emit('close')">
              取消
            </button>
          </span>
        </div>
        <p v-if="error" class="cb-error" data-testid="comment-error">{{ error }}</p>
        <p class="cb-hint">提交时自动生成整页截图并红框标注目标区域</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.comment-box {
  flex-shrink: 0;
  border-top: 1px solid #e6e8ec;
  background: #fbfcfe;
  max-height: 300px;
  overflow-y: auto;
  padding: 8px 12px;
  font-size: 12px;
}
.cp-title {
  color: #2b5cff;
  margin-bottom: 6px;
  font-size: 13px;
}
.cp-grid {
  display: grid;
  grid-template-columns: 132px 1fr;
  gap: 3px 10px;
  align-items: baseline;
  padding-bottom: 6px;
  border-bottom: 1px dashed #e6e8ec;
  margin-bottom: 8px;
}
.k { color: #999; }
b { font-weight: 500; color: #24292f; word-break: break-all; }
.mono,
.cp-details code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}
.cp-details { margin: 0; }
.cp-details summary { cursor: pointer; color: #2b5cff; }
.cp-details code {
  display: block;
  white-space: pre-wrap;
  word-break: break-all;
  margin-top: 4px;
}

.cb-form { display: flex; flex-direction: column; gap: 8px; }
.cb-content {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #d9dce1;
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
}
.cb-content:focus { outline: none; border-color: #2b5cff; }
.cb-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.cb-row label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #57606a;
  white-space: nowrap;
}
.cb-row select,
.cb-row input {
  border: 1px solid #d9dce1;
  border-radius: 4px;
  padding: 2px 4px;
  font-size: 12px;
}
.cb-row input[readonly] { background: #f6f8fa; color: #57606a; }
.cb-author input { width: 90px; }
.cb-actions { margin-left: auto; display: inline-flex; gap: 6px; }
.cb-btn {
  border: 1px solid #d9dce1;
  border-radius: 4px;
  background: #fff;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}
.cb-btn:hover:not(:disabled) { border-color: #2b5cff; color: #2b5cff; }
.cb-btn.primary {
  background: #2b5cff;
  border-color: #2b5cff;
  color: #fff;
}
.cb-btn.primary:hover:not(:disabled) { background: #1e4fd8; }
.cb-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.cb-error { color: #d33; margin: 0; }
.cb-hint { color: #999; margin: 0; }

/* 提交成功态 */
.cb-done-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.cb-done-head .ok { color: #2e9e44; font-size: 13px; }
.cb-done-head .cid {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: #f0f4ff;
  border-radius: 4px;
  padding: 1px 6px;
  color: #2b5cff;
}
.cb-done-head .push-fail {
  color: #b45200;
  background: #fdf6ec;
  border-radius: 4px;
  padding: 1px 6px;
}
.cb-done-head .cb-btn { margin-left: auto; }
.cb-shot {
  max-width: 100%;
  max-height: 170px;
  border: 1px solid #e6e8ec;
  border-radius: 4px;
}
.cb-noshot { color: #999; margin: 4px 0 0; }
</style>
