<script setup lang="ts">
import { ref, watch } from 'vue'
import type { CommentPayload, CreateCommentResult } from '../projects'

/**
 * T4.2 评论框（左侧底部，替换 T4.1 的采集结果面板）。
 *
 * 三态：
 * - composing：目标摘要（T4.1 采集字段，testid 保留）+ 表单（文字必填 / 署名只读）
 * - submitting：由父组件控制（props.submitting），按钮 loading
 * - done：提交成功 + 截图预览（可查看不可编辑，产品方案 §4.5）
 *
 * 截图由父组件（Viewer）在提交时经 bridge 采集（提交时而非打开时——
 * 保证反映提交一刻状态）；本组件只管表单与展示。
 * T 增强：表单不再含「优先级」「修改范围」（评论只保留内容）。
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
  submit: [form: { content: string }]
  close: []
}>()

const content = ref('')

// 换目标（重新点元素）重置表单
watch(
  () => props.payload,
  () => {
    content.value = ''
  },
  { immediate: true },
)

function onSubmit() {
  if (!content.value.trim() || props.submitting) return
  emit('submit', {
    content: content.value.trim(),
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
          <span class="syncing" title="评论已写入项目目录（T8.1 本地化存储）">
            已存档
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

      <!-- 表单（T8.6 优化：去掉自动填充的 payload 属性摘要，只留内容/优先级/范围） -->
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
/* T8.3 修复：悬浮窗（fixed）——旧版内嵌在原型 pane 文档流里，出现时把
   iframe 挤窄导致原型视口变形（vh 类布局随之错位）；悬浮后不占布局，
   原型区尺寸恒定。定位右下：原型 pane 在左半屏，换目标（再点原型元素）
   不会被窗遮挡；PRD 侧入口点击均发生在窗出现之前，不受影响。 */
.comment-box {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: 480px;
  max-width: calc(100vw - 48px);
  z-index: 100;
  border: 1px solid var(--pp-border);
  border-radius: var(--pp-radius);
  box-shadow: var(--pp-shadow-lg);
  background: var(--pp-surface);
  max-height: min(60vh, 480px);
  overflow-y: auto;
  padding: 14px 16px;
  font-size: 12px;
}
.cp-title {
  color: var(--pp-primary);
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
}
.cp-grid {
  display: grid;
  grid-template-columns: 132px 1fr;
  gap: 3px 10px;
  align-items: baseline;
  padding-bottom: 6px;
  border-bottom: 1px dashed var(--pp-border);
  margin-bottom: 8px;
}
.k { color: var(--pp-text-3); }
b { font-weight: 500; color: var(--pp-text-1); word-break: break-all; }
.mono,
.cp-details code {
  font-family: var(--pp-mono);
  font-size: 11px;
}
.cp-details { margin: 0; }
.cp-details summary { cursor: pointer; color: var(--pp-primary); }
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
  border: 1px solid var(--pp-border-strong);
  border-radius: var(--pp-radius-sm);
  padding: 8px 10px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
  background: var(--pp-surface);
  color: var(--pp-text-1);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.cb-content:focus {
  outline: none;
  border-color: var(--pp-primary);
  box-shadow: 0 0 0 3px rgba(79, 99, 210, 0.12);
}
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
  color: var(--pp-text-2);
  white-space: nowrap;
}
.cb-row select,
.cb-row input {
  border: 1px solid var(--pp-border-strong);
  border-radius: var(--pp-radius-xs);
  padding: 2px 4px;
  font-size: 12px;
}
.cb-row input[readonly] { background: var(--pp-surface-2); color: var(--pp-text-2); }
.cb-author input { width: 90px; }
.cb-actions { margin-left: auto; display: inline-flex; gap: 6px; }
.cb-btn {
  border: 1px solid var(--pp-border-strong);
  border-radius: var(--pp-radius-xs);
  background: var(--pp-surface);
  padding: 5px 14px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.cb-btn:hover:not(:disabled) { border-color: var(--pp-primary); color: var(--pp-primary); }
.cb-btn.primary {
  background: var(--pp-primary);
  border-color: var(--pp-primary);
  color: #fff;
  box-shadow: 0 1px 4px rgba(79, 99, 210, 0.28);
}
.cb-btn.primary:hover:not(:disabled) {
  background: var(--pp-primary-hover);
  color: #fff;
}
.cb-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.cb-error { color: var(--pp-danger); margin: 0; }
.cb-hint { color: var(--pp-text-3); margin: 0; }

/* 提交成功态 */
.cb-done-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.cb-done-head .ok { color: var(--pp-success); font-size: 13px; font-weight: 600; }
.cb-done-head .cid {
  font-family: var(--pp-mono);
  background: var(--pp-primary-soft);
  border-radius: var(--pp-radius-xs);
  padding: 1px 6px;
  color: var(--pp-primary);
}
.cb-done-head .syncing {
  color: var(--pp-text-2);
  background: var(--pp-surface-2);
  border-radius: var(--pp-radius-xs);
  padding: 1px 6px;
  font-size: 11px;
}
.cb-done-head .cb-btn { margin-left: auto; }
.cb-shot {
  max-width: 100%;
  max-height: 170px;
  border: 1px solid var(--pp-border);
  border-radius: var(--pp-radius-sm);
}
.cb-noshot { color: var(--pp-text-3); margin: 4px 0 0; }
</style>
