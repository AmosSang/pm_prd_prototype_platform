/**
 * markdown-it 锚点插件（T3.1）
 *
 * 语法：块级 HTML 注释 `<!-- pa: xxx -->`（T 增强支持同行多个 `<!-- pa: a -->`：
 * `data-pa` 存空格分隔的多个 ID，可点击弹出列表选择定位）：
 *   ## 3.1 登录页 <!-- pa: page-login -->   → 同 heading 行尾的注释，挂该 heading
 *   <!-- pa: login-form -->\n表单说明         → 独立注释行，优先归并到「前一个
 *                                             段落/标题」；前块非段落/标题则挂下一个块
 *
 * 实现分两种情况（markdown-it token 流层面）：
 * 1. 行尾注释：`<!-- pa -->` 是 heading 段落内的 html_inline token，
 *    归属其所在块级 open token → 给 open token 的 data-pa 追加 ID（可多）
 * 2. 独立注释行：html_block token 单独成块，渲染输出为空 → 锚点进「待附加」
 *    队列；优先挂到「前一个段落/标题」（heading_open / 非列表项 paragraph_open），
 *    否则等下一个块级 open token（heading/paragraph/table/ol/ul）出现时消费
 *
 * 渲染后 v-html 里的 DOM 形如 <h2 data-pa="page-login">3.1 登录页</h2>（多锚点
 * 为 <p data-pa="a b">…</p>），宿主用 [data-pa] 选择器做滚动联动。
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
    const pending: string[] = [] // 独立注释且无「前一段落/标题」可挂时的待附加（挂下一个块）
    const tokens = state.tokens

    for (let i = 0; i < tokens.length; i++) {
      const tok = tokens[i]

      // 情况 2：行尾注释（html_inline）→ 归属其所在块级 open token 的 attrs
      // markdown-it 的 token 流：heading_open, inline, heading_close —— html_inline
      // 是 inline 的 children；需要回溯到本块的 open token（同层级最近一个）。
      if (tok.type === 'inline' && tok.children) {
        for (const child of tok.children) {
          if (child.type === 'html_inline' && child.content) {
            const m = ANCHOR_RE.exec(child.content)
            if (m) {
              const open = findOpenOf(tokens, tok)
              if (open) addAttr(open, m[1]) // 同行/同块多个锚点 → data-pa 追加
              child.content = ''
              child.hidden = true
            }
          }
        }
      }

      // 情况 1：块级注释（独立成行，可含同行多个 `<!-- pa: x -->`）→ 优先归并到
      // 「前一个段落/标题」；前块非段落/标题或无前块 → 排队给下一个块。
      if (tok.type === 'html_block' && tok.content) {
        const ids = extractAnchorIds(tok.content)
        if (ids.length) {
          const prev = prevParagraphOrHeading(tokens, i)
          if (prev) {
            for (const id of ids) addAttr(prev, id)
          } else {
            pending.push(...ids)
          }
          tok.content = '' // html_block 渲染直接拼 content，置空即隐藏
          tok.hidden = true
          continue
        }
      }

      // 消费待附加队列：下一个块级 open token 一次性附加全部（同块多个锚点同挂）
      if (pending.length > 0 && BLOCK_OPEN_TYPES.has(tok.type)) {
        for (const id of pending) addAttr(tok, id)
        pending.length = 0
      }
    }
  })
}

/** 提取内容里全部锚点 ID（同行多个 `<!-- pa: x -->` 都收，供 list 弹窗用）。 */
function extractAnchorIds(content: string): string[] {
  const out: string[] = []
  const re = /<!--\s*pa:\s*([a-z0-9-]+)\s*-->/g
  let m: RegExpExecArray | null
  while ((m = re.exec(content))) out.push(m[1])
  return out
}

/** 给 block open token 的 data-pa 追加锚点 ID（空格分隔，支持同块多个锚点）。 */
function addAttr(tok: TokenLike, id: string): void {
  const cur = String(tok.attrGet('data-pa') || '').trim()
  const arr = cur ? cur.split(/\s+/) : []
  if (!arr.includes(id)) {
    arr.push(id)
    tok.attrSet('data-pa', arr.join(' '))
  }
}

/** 独立注释行的「前一个段落/标题」open token；前块是列表项/表格等非段落
 * （不渲染成 <p>/<h>）或无前块 → 返回 null（走下一块）。 */
function prevParagraphOrHeading(
  tokens: readonly { type: string }[],
  idx: number,
): TokenLike | null {
  for (let i = idx - 1; i >= 0; i--) {
    const t = tokens[i] as TokenLike
    if (t.type === 'heading_open') return t
    if (t.type === 'paragraph_open') {
      // li 内段落不渲染成 <p>（段落即 li 本身），不算「段落/标题」→ 走下一块
      if (i > 0 && (tokens[i - 1] as TokenLike).type === 'list_item_open') return null
      return t
    }
    // 其它块级 open（table/list/li/blockquote 等）不往上穿透：到此为止
    if (t.type.endsWith('_open')) return null
  }
  return null
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
  attrGet: (name: string) => string | number | null
  attrSet: (name: string, value: string) => void
}
