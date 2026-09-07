<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

/**
 * T10.2 文档目录大纲（PRD §5.4.3）：文档栏内侧右缘固定窄栏。
 *
 * - 数据：渲染完成后从 DOM 提取 .markdown-body 内 h1–h4（文本 + 锚点 id），
 *   不引入第二份解析逻辑（PRD 硬约束）
 * - 点击：平滑滚动到对应标题（补偿 pane-head/容器 padding 偏移）+ 目标短暂高亮
 * - scrollspy：IntersectionObserver 监听标题可见性，实时高亮当前章节
 * - 展开收起规则（PRD §5.4.3 用户确认版）：
 *   split 模式默认展开、可手动收起；三栏（评论抽屉打开）强制收起。
 *   手动收起偏好记忆本次会话（v-show 不销毁，状态由父级 props 驱动）
 */
export interface TocItem {
  id: string
  text: string
  level: number // 1–4
}

const props = defineProps<{
  /** 大纲数据（父级从渲染后 DOM 提取，保证单一代码路径） */
  items: TocItem[]
  /** 滚动容器元素（.prd-scroll），scrollspy 与跳转的目标 */
  scrollEl: HTMLElement | null
  /** 评论抽屉打开（三栏）→ 强制收起 */
  collapsed: boolean
}>()

const listEl = ref<HTMLElement | null>(null)
const activeId = ref('')
let observer: IntersectionObserver | null = null
/** 点击跳转后的滚动期间暂停 spy 更新（避免中途经过的章节抢高亮） */
let scrollLock = 0

/** 层级缩进（CSS 计算）：h1 0 级、h2 1 级… */
function indentOf(item: TocItem): number {
  return Math.min(item.level - 1, 3)
}

function setupSpy(): void {
  observer?.disconnect()
  observer = null
  if (!props.scrollEl || !props.items.length) return
  // 根据文档 id 找到全部标题元素
  const heads = props.items
    .map((it) => props.scrollEl!.querySelector(`#${cssEscape(it.id)}`))
    .filter((el): el is Element => !!el)
  if (!heads.length) return
  // 根区 = 滚动容器本身；顶部余量让「刚滚过」的标题保持 active 一段距离
  observer = new IntersectionObserver(
    (entries) => {
      if (Date.now() < scrollLock) return
      for (const en of entries) {
        if (en.isIntersecting) {
          activeId.value = en.target.id
        }
      }
    },
    {
      root: props.scrollEl,
      // 视口上沿 15% 处的窄带作为「当前阅读线」，标题穿过即视为所在章节
      rootMargin: '-15% 0px -75% 0px',
      threshold: 0,
    },
  )
  heads.forEach((h) => observer!.observe(h))
}

/** 标题 id 是中文 slug，querySelector 需转义特殊字符（CSS.escape 不可用时手工） */
function cssEscape(id: string): string {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(id)
  return id.replace(/(["\\])/g, '\\$1')
}

/** 点击跳转：绝对偏移（getBoundingClientRect 差值 + 当前 scrollTop），
 * 平滑滚动；期间锁 spy；到位后目标标题短暂高亮。 */
function jump(item: TocItem): void {
  const target = props.scrollEl?.querySelector(`#${cssEscape(item.id)}`) as HTMLElement | null
  if (!target || !props.scrollEl) return
  const top =
    target.getBoundingClientRect().top -
    props.scrollEl.getBoundingClientRect().top +
    props.scrollEl.scrollTop -
    12 // 视觉呼吸空间
  scrollLock = Date.now() + 900 // 平滑滚动约 500ms + 缓冲
  activeId.value = item.id
  props.scrollEl.scrollTo({ top, behavior: 'smooth' })
  // 目标标题短暂高亮（scrollLock 解除后由 spy 接管）
  target.classList.add('toc-jumped')
  setTimeout(() => target.classList.remove('toc-jumped'), 1400)
}

// items 变化（文档切换/重建）→ 重挂 observer + 回到首个标题
watch(
  () => props.items,
  () => {
    activeId.value = props.items[0]?.id || ''
    nextTick(setupSpy)
  },
  { immediate: false },
)

// scrollEl 变化（首次挂载/容器替换）→ 重挂
watch(
  () => props.scrollEl,
  () => nextTick(setupSpy),
  { immediate: true },
)

onBeforeUnmount(() => observer?.disconnect())

const hasToc = computed(() => props.items.length > 0)
defineExpose({ hasToc })
</script>

<template>
  <aside v-if="hasToc" class="toc-sidebar" data-testid="toc-sidebar">
    <div class="toc-title">目录</div>
    <nav v-show="!collapsed" ref="listEl" class="toc-list" data-testid="toc-list">
      <button
        v-for="it in items"
        :key="it.id"
        type="button"
        class="toc-item"
        :class="{ active: it.id === activeId }"
        :style="{ paddingLeft: 8 + indentOf(it) * 12 + 'px' }"
        :data-testid="`toc-item-${it.id}`"
        :title="it.text"
        @click="jump(it)"
      >
        {{ it.text }}
      </button>
    </nav>
  </aside>
</template>

<style scoped>
.toc-sidebar {
  display: flex;
  flex-direction: column;
  width: 210px;
  flex-shrink: 0;
  border-left: 1px solid var(--pp-border);
  background: var(--pp-surface);
  overflow: hidden;
}
.toc-title {
  padding: 10px 12px 6px;
  font-size: 12px;
  color: var(--pp-text-3);
  font-weight: 600;
  flex-shrink: 0;
}
.toc-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 6px 10px;
}
.toc-item {
  display: block;
  width: 100%;
  padding: 4px 8px;
  border: none;
  border-radius: var(--pp-radius-xs);
  background: none;
  color: var(--pp-text-2);
  font-size: 12px;
  line-height: 1.5;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.toc-item:hover { background: var(--pp-surface-2); color: var(--pp-text-1); }
.toc-item.active {
  background: var(--pp-primary-soft);
  color: var(--pp-primary);
  font-weight: 600;
}
</style>
