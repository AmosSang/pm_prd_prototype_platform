/**
 * markdown-it 锚点插件（T3.1）
 *
 * 语法：块级 HTML 注释 `<!-- pa: xxx -->`，锚点 ID 挂到「注释之后第一个
 * 块级元素」上（技术方案 §2.4）：
 *   ## 3.1 登录页 <!-- pa: page-login -->   → 同 heading 行尾的注释，挂该 heading
 *   <!-- pa: login-form -->\n表单说明         → 独立注释行，挂下一个段落/表格
 *
 * 实现分两种情况（markdown-it token 流层面）：
 * 1. 行尾注释：`<!-- pa -->` 是 heading 段落内的 html_inline token，
 *    归属其所在块级 open token → 直接给 open token 加 attrs
 * 2. 独立注释行：html_block token 单独成块，渲染输出为空 → 锚点进「待附加」
 *    队列，等下一个块级 open token（heading/paragraph/table/ol/ul）出现时消费
 *
 * 渲染后 v-html 里的 DOM 形如 <h2 data-pa="page-login">3.1 登录页</h2>，
 * 宿主用 [data-pa] 选择器做滚动联动（scrollIntoView + 高亮 class）。
 * 服务端对账用的是另一份纯文本正则解析（T3.3），两侧解耦。
 */
import type MarkdownIt from 'markdown-it'

/** 锚点 ID 规则：kebab-case（与 PRD 模板契约一致） */
const ANCHOR_RE = /<!--\s*pa:\s*([a-z0-9-]+)\s*-->/
const BLOCK_OPEN_TYPES = new Set([
  'heading_open',
  'paragraph_open',
  'table_open',
  'ordered_list_open',
  'bullet_list_open',
  'blockquote_open',
])

export function anchorPlugin(md: InstanceType<typeof MarkdownIt>): void {
  // 注意：必须在 'inline' 规则之后跑——heading/paragraph 里的注释在
  // inline tokenize 阶段才生成 html_inline child token（after('block') 时
  // children 还不存在，实测注释会漏识别）。
  md.core.ruler.after('inline', 'pa_anchor', (state) => {
    const pending: string[] = [] // 独立注释的待附加锚点（FIFO）

    for (const tok of state.tokens) {
      // 情况 1：块级注释（独立成行）→ 记入待附加队列，渲染时置空（不输出注释文本）
      if (tok.type === 'html_block' && tok.content) {
        const m = ANCHOR_RE.exec(tok.content)
        if (m) {
          pending.push(m[1])
          tok.content = '' // html_block 渲染直接拼 content，置空即隐藏
          tok.hidden = true
          continue
        }
      }

      // 情况 2：行尾注释（html_inline）→ 归属其所在块级 open token 的 attrs
      // markdown-it 的 token 流：heading_open, inline, heading_close —— html_inline
      // 是 inline 的 children；需要回溯到本块的 open token（同层级最近一个）。
      if (tok.type === 'inline' && tok.children) {
        for (const child of tok.children) {
          if (child.type === 'html_inline' && child.content) {
            const m = ANCHOR_RE.exec(child.content)
            if (m) {
              const open = findOpenOf(state.tokens, tok)
              if (open && !open.attrGet('data-pa')) {
                open.attrSet('data-pa', m[1])
              }
              child.content = ''
              child.hidden = true
            }
          }
        }
      }

      // 消费待附加队列：下一个块级 open token 挂上（一次只消费一个锚点——
      // 连续多个独立注释行指向同一段落没有明确语义，取第一个）
      if (pending.length > 0 && BLOCK_OPEN_TYPES.has(tok.type) && !tok.attrGet('data-pa')) {
        tok.attrSet('data-pa', pending.shift()!)
      }
    }
  })
}

/** inline token 所在块的 open token：向前回溯找归属块。
 *
 * 归属规则（实测 token 流得出）：
 * - heading 段落：heading_open ← inline → 挂 heading（主形态，契约模板）
 * - 普通段落：paragraph_open ← inline → 挂 <p>
 * - 列表项内段落：bullet_list_open, list_item_open, paragraph_open, inline
 *   → 最近 open 是 paragraph_open，但 li 内段落渲染时不输出 <p>（段落即 li
 *   本身），挂 paragraph_open 等于没挂 → 跳过它继续回溯到 list_item_open
 *
 * 即：先看外层是否 list_item_open（是则挂 li），否则挂最近的块级 open。
 */
function findOpenOf(tokens: readonly { type: string }[], inlineTok: unknown): TokenLike | null {
  const idx = tokens.indexOf(inlineTok as never)
  let nearestOpen: TokenLike | null = null
  for (let i = idx - 1; i >= 0; i--) {
    const t = tokens[i] as TokenLike
    if (t.type === 'list_item_open') {
      // 外层是列表项：li 内段落不渲染 <p>，锚点挂 li 更准
      return t
    }
    if (t.type.endsWith('_open')) {
      if (nearestOpen) return nearestOpen // 已有内层 open，用内层
      nearestOpen = t
      continue // 可能外层还有 list_item_open，继续回溯一层
    }
    if (t.type.endsWith('_close')) return nearestOpen // 走出当前块，用已找到的
  }
  return nearestOpen
}

interface TokenLike {
  type: string
  content: string
  hidden?: boolean
  attrGet: (name: string) => string | null
  attrSet: (name: string, value: string) => void
}
