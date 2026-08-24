/**
 * bridge.js — 注入原型 iframe 的桥接脚本
 *
 * T1.1：READY / PING-ECHO 通信地基（URL nonce 认证）
 * T1.2：TAKE_SCREENSHOT 整页截图（modern-screenshot，沙箱兼容）+ Blob 回传 + highlight_rect 计算
 * T3.1：锚点体系正向链路——[data-pa] 扫描上报（ANCHOR_REPORT）+ hover 锚点 icon（◈）
 *        + 点击 icon 发 ANCHOR_CLICK（宿主侧滚动右侧文档并高亮 2s）
 * T3.2：反向联动——HIGHLIGHT_ANCHOR（宿主点文档「定位」→ 滚动到锚点元素
 *        + outline 脉冲闪烁 3 次；锚点不在本页时由宿主先切页再发）
 * T4.1：评论模式——SET_COMMENT_MODE 开关 + hover 蓝色高亮 + click 捕获拦截
 *        （stopPropagation/preventDefault）+ payload 一次性采集（ELEMENT_SELECTED，
 *        字段表见技术方案 §2.3）+ ROUTE_CHANGE（SPA 路由切换上报：
 *        pushState/replaceState monkey-patch + hashchange/popstate）
 * T4.2：COLLECT_PAGE——「评论本页」按钮触发页面根（body）payload 采集
 *
 * 认证机制：sandbox（无 allow-same-origin）下 iframe origin 为不透明 "null"，
 * 无法按 origin 校验。采用 URL nonce：宿主在 iframe src 的 hash 中携带随机
 * nonce（#pp-nonce=xxx），bridge 每条消息回带，宿主校验匹配后才信任。
 *
 * 协议见《一期技术实现方案-V1.md》§2.2。约束：原生 JS、零依赖、幂等
 * （modern-screenshot 为平台自托管 vendor，从 /vendor/modern-screenshot.mjs 懒加载）。
 */
;(function () {
  'use strict'

  if (window.__PP_BRIDGE__) return // 幂等：防重复注入
  window.__PP_BRIDGE__ = true

  // 从 URL hash 取宿主下发的 nonce
  var m = /(?:^|#)pp-nonce=([A-Za-z0-9_-]+)/.exec(window.location.hash)
  var NONCE = m ? m[1] : null

  // 宿主 origin：来自 referrer（浏览器设置，frame 内容无法伪造）
  var hostOrigin = document.referrer ? new URL(document.referrer).origin : null

  function send(type, payload) {
    payload = payload || {}
    payload.type = type
    payload.nonce = NONCE
    if (hostOrigin) {
      window.parent.postMessage(payload, hostOrigin)
    }
  }

  // ─── 截图（T1.2）────────────────────────────────────────────
  // 库选型：html2canvas 在沙箱（不透明 origin）下不可用——它克隆文档进隐藏
  // iframe，嵌套 iframe 又是新 origin，contentDocument 读取被浏览器拦截。
  // modern-screenshot 基于 SVG foreignObject 纯内存克隆，沙箱可用。

  var h2cLoading = null
  function loadScreenshotLib() {
    if (window.__PP_SCREENSHOT_LIB__) return Promise.resolve(window.__PP_SCREENSHOT_LIB__)
    if (h2cLoading) return h2cLoading
    h2cLoading = new Promise(function (resolve, reject) {
      // ESM 动态 import（路径相对当前脚本 /bridge.js 所在 origin）
      import(new URL('/vendor/modern-screenshot.mjs', window.location.origin).href)
        .then(function (mod) {
          window.__PP_SCREENSHOT_LIB__ = mod
          resolve(mod)
        })
        .catch(reject)
    })
    return h2cLoading
  }

  /**
   * 整页截图：渲染完整文档（含滚动区域），返回 { blob, highlight }。
   * highlight 与整页截图同坐标系（相对文档左上角）。
   *
   * 沙箱适配（不透明 origin 专用绕法，已 E2E 验证）：
   * modern-screenshot 的 getDefaultStyle 依赖隐藏 iframe 取「元素默认样式」，
   * 而沙箱文档内创建的任何嵌套 iframe 都继承 sandbox 标志（不透明 origin），
   * contentDocument 读取必然被浏览器拦截（包括 about:blank 与 srcdoc）。
   * 绕法：把 context.sandbox 替换为 { contentWindow: null } 假对象——
   * 库对 sandboxWindow 判空后返回空样式表（正常分支，不抛异常）。
   *
   * 绕法的副作用与修正（红框偏移 bug，2026-08-21）：
   * 默认样式表被置空后，库的 applyCssStyleWithOptions 会用 !important
   * 强制克隆 body 的 width/height = 画布尺寸（scrollWidth/scrollHeight）。
   * body margin=0 时两者恰好一致；margin≠0 时真实 body 宽 = 视口宽−margin，
   * 克隆却拉满画布宽 → flex 居中等布局整体错位（实测 margin 20px 16px 时
   * 内容偏 (+16,+20)），红框坐标按真实文档算 → 相对偏移。
   * 修正：onCloneNode 把克隆 body 的 margin/width/height 写回真实计算值
   * （onCloneNode 在 applyCssStyleWithOptions 之后执行，important 可覆盖）。
   */
  function captureFullPage(targetEl) {
    return loadScreenshotLib().then(function (mod) {
      var doc = document.documentElement
      var fullWidth = Math.max(doc.scrollWidth, document.body.scrollWidth)
      var fullHeight = Math.max(doc.scrollHeight, document.body.scrollHeight)
      return mod
        .createContext(document.body, {
          width: fullWidth,
          height: fullHeight,
          scale: 1,
          backgroundColor: '#ffffff',
          autoDestruct: false,
        })
        .then(function (context) {
          // 假 sandbox：短路 getDefaultStyle 的 iframe 依赖（见头注）
          context.sandbox = { contentWindow: null }
          // 红框偏移修正：三件套写回（margin + width + height），
          // 见头注——只写 margin 不够，width/height 被库强制为画布尺寸也会错位
          var cs = window.getComputedStyle(document.body)
          var realMargin =
            cs.marginTop + ' ' + cs.marginRight + ' ' + cs.marginBottom + ' ' + cs.marginLeft
          context.onCloneNode = function (clone) {
            if (clone && clone.nodeName === 'BODY') {
              clone.style.setProperty('margin', realMargin, 'important')
              clone.style.setProperty('width', cs.width, 'important')
              clone.style.setProperty('height', cs.height, 'important')
            }
          }
          // domToBlob(context) 接受 context 对象（isContext 判定后直接用）
          return mod.domToBlob(context)
        })
        .then(function (blob) {
          var highlight = null
          if (targetEl) {
            var rect = targetEl.getBoundingClientRect()
            var sx = window.scrollX || window.pageXOffset
            var sy = window.scrollY || window.pageYOffset
            highlight = {
              x: Math.round(rect.left + sx),
              y: Math.round(rect.top + sy),
              w: Math.round(rect.width),
              h: Math.round(rect.height),
            }
          }
          return { blob: blob, highlight: highlight, width: fullWidth, height: fullHeight }
        })
    })
  }

  // 按 cssPath 定位目标元素；找不到返回 null（宿主可回退到整页无框）
  function queryTarget(cssPath) {
    try {
      return document.querySelector(cssPath)
    } catch (e) {
      return null
    }
  }

  // ─── 锚点体系（T3.1，正向链路）─────────────────────────────
  // 语义：[data-pa] 是「产品锚点」——PRD 与原型两侧共用的段落/组件标识。
  // bridge 负责：扫描上报（ANCHOR_REPORT）、hover 挂 icon（◈）、点击上报
  // （ANCHOR_CLICK，宿主滚动右侧文档并高亮）。反向 HIGHLIGHT_ANCHOR 在 T3.2。

  /** 从元素向上到最近的 [data-pa] 祖先（无则到 body），拼 tag:nth-of-type(n) 链。 */
  function cssPathOf(el) {
    var parts = []
    var node = el
    while (node && node.nodeType === 1 && node !== document.body) {
      var tag = node.tagName.toLowerCase()
      if (node.hasAttribute('data-pa')) {
        // 锚点祖先直接用属性选择器（全局唯一，链到此为止）
        parts.unshift('[data-pa="' + node.getAttribute('data-pa') + '"]')
        return parts.join(' > ')
      }
      var idx = 1
      var sib = node
      while ((sib = sib.previousElementSibling)) {
        if (sib.tagName === node.tagName) idx++
      }
      parts.unshift(tag + ':nth-of-type(' + idx + ')')
      node = node.parentElement
    }
    parts.unshift('body')
    return parts.join(' > ')
  }

  /** 扫描本页全部 [data-pa]，[{id, cssPath}]。 */
  function scanAnchors() {
    var out = []
    var els = document.querySelectorAll('[data-pa]')
    for (var i = 0; i < els.length; i++) {
      out.push({ id: els[i].getAttribute('data-pa'), cssPath: cssPathOf(els[i]) })
    }
    return out
  }

  /** 上报锚点清单（READY 时 + DOM 变化 debounce 后）。 */
  var anchorReportTimer = null
  function reportAnchors() {
    send('ANCHOR_REPORT', { anchors: scanAnchors(), page: window.location.pathname })
  }
  function scheduleAnchorReport() {
    clearTimeout(anchorReportTimer)
    anchorReportTimer = setTimeout(reportAnchors, 300)
  }

  // MutationObserver：SPA/动态渲染场景下 DOM 变化后重报锚点。
  // （T3.2 将扩展为 ROUTE_CHANGE 上报，这里先只重报锚点。）
  var mo = new MutationObserver(function () {
    scheduleAnchorReport()
  })
  function startObserver() {
    mo.observe(document.documentElement, { childList: true, subtree: true })
  }

  // ─── 锚点 hover icon（◈）──────────────────────────────────
  // 单例浮动 icon：pointer-events:auto，fixed 定位在目标元素左上角外侧；
  // hover 到 [data-pa] 元素显示；点击 → ANCHOR_CLICK。
  // 隐藏策略：离开目标后 1s 渐隐（宽限期内 hover 到 icon 恢复常显）。
  //
  // 渐隐宽限期（用户反馈「鼠标挪到 icon 附近 icon 就消失」）：
  // 鼠标离开锚点区域后 icon 不立即消失，而是 1s 内 opacity 1→0 渐隐；
  // 这 1s 内鼠标 hover 到 icon 上 → 恢复不透明并常驻（icon 是 pointer
  // 目标，必须可接住）；之后只有鼠标同时离开 icon 和锚点区域才重新渐隐。
  // 嵌套锚点跳变修复：原型中组件锚点常在页面锚点内部（如按钮在
  // [data-pa=page-timer] main 里），离开组件走到祖先锚点的空白区域时
  // mouseover 会打到祖先锚点 → icon 若换目标会瞬移闪跳。规定：嵌套
  // 锚点一律取最内层（mouseover closest 语义天然如此）；离开最内层后，
  // 祖先地盘不切换（icon 原地渐隐仍可接住）；走到无关兄弟锚点才换目标。
  var anchorIcon = null
  var iconTarget = null // 当前 icon 挂靠的元素
  var iconState = 'hidden' // hidden | showing | fading（fading = 渐隐宽限期中）
  var fadeTimer = null
  var FADE_MS = 1000

  function ensureIcon() {
    if (anchorIcon) return anchorIcon
    var style = document.createElement('style')
    style.textContent =
      '.pp-anchor-icon{position:fixed;z-index:2147483647;cursor:pointer;' +
      'display:none;align-items:center;justify-content:center;width:22px;height:22px;' +
      'border-radius:6px;background:#2b5cff;color:#fff;font-size:13px;line-height:1;' +
      'font-family:system-ui,sans-serif;user-select:none;box-shadow:0 1px 6px rgba(0,0,0,.25);}' +
      '.pp-anchor-icon--fading{opacity:0;transition:opacity 1s linear;}' +
      '.pp-anchor-icon:hover{background:#1e4fd8;}'
    document.head.appendChild(style)

    anchorIcon = document.createElement('div')
    anchorIcon.className = 'pp-anchor-icon'
    anchorIcon.title = '定位到 PRD 对应段落'
    anchorIcon.textContent = '◈'
    anchorIcon.addEventListener('mousedown', function (e) {
      e.preventDefault()
      e.stopPropagation()
    })
    anchorIcon.addEventListener('click', function (e) {
      e.stopPropagation()
      // 用 dataset 快照（showIconFor 时写入）而非 iconTarget 变量——
      // 「接住」状态下 iconTarget 可能被后来的 mouseover 切换到别的锚点
      var anchorId = anchorIcon.dataset.paTarget
      if (anchorId) {
        send('ANCHOR_CLICK', {
          anchorId: anchorId,
          page: window.location.pathname,
        })
        // 点击反馈：icon 短暂变深色
        anchorIcon.style.background = '#1e4fd8'
        setTimeout(function () {
          anchorIcon.style.background = ''
        }, 300)
      }
    })
    document.body.appendChild(anchorIcon)
    return anchorIcon
  }

  function positionIcon(el) {
    var r = el.getBoundingClientRect()
    var icon = ensureIcon()
    // 默认放目标左上角外侧 26px；紧贴左缘时放右上角内侧 4px。
    // 边界钳制：icon 必须完整落在 iframe 视口内——fixed 定位超出 iframe
    // 视口的部分会被宿主页面元素覆盖（pointer events 被劫持，实测 E2E 中
    // 超界 icon 被 .prd-scroll 拦截点击永远点不到）。
    var x = r.left - 26
    var y = r.top
    if (x < 0) x = r.right + 4
    var vw = window.innerWidth || document.documentElement.clientWidth
    var vh = window.innerHeight || document.documentElement.clientHeight
    if (x + 22 > vw) x = Math.max(0, vw - 22)
    if (y + 22 > vh) y = Math.max(0, vh - 22)
    if (y < 0) y = 0
    icon.style.left = Math.round(x) + 'px'
    icon.style.top = Math.round(y) + 'px'
  }

  /** 开始 1s 渐隐宽限期。期间 hover 到 icon 上可恢复（取消 fading class）。 */
  function startFade() {
    if (!anchorIcon || !iconTarget) return
    if (iconState === 'fading') return
    iconState = 'fading'
    clearTimeout(fadeTimer)
    fadeTimer = setTimeout(function () {
      if (iconState !== 'fading') return
      // 到期时鼠标若仍真悬停在 icon 上（渐隐中移入但 mouseover 事件被
      // 合并/吞掉的边缘态），不隐藏——用 :hover 匹配判定（elementFromPoint
      // 不可用：它只看坐标顶层元素，与鼠标实际位置无关，会误判常驻）
      var hovered = false
      try {
        hovered = anchorIcon.matches(':hover')
      } catch (e) { /* 旧浏览器兜底：忽略 */ }
      if (hovered) {
        cancelFade() // 鼠标真在 icon 上：恢复常显（相当于接住）
        return
      }
      hideIcon()
    }, FADE_MS)
    anchorIcon.classList.add('pp-anchor-icon--fading')
  }

  /** 取消渐隐，恢复不透明常显（无淡入动画——接住要立刻实心）。 */
  function cancelFade() {
    clearTimeout(fadeTimer)
    if (anchorIcon) anchorIcon.classList.remove('pp-anchor-icon--fading')
    if (iconState === 'fading') iconState = iconTarget ? 'showing' : 'hidden'
  }

  function showIconFor(el) {
    iconTarget = el
    // 目标快照到 DOM：接住后（icon hover 常显但未重新 mouseover 目标区域）
    // iconTarget 可能被后续 mouseover 切走，click 必须用 icon 位置对应的
    // 锚点（用户看到 icon 挂在哪就定位哪），不能依赖可变闭包变量
    anchorIcon = ensureIcon()
    anchorIcon.dataset.paTarget = el.getAttribute('data-pa')
    positionIcon(el)
    cancelFade() // 新目标：无渐隐 class，实心显示
    anchorIcon.style.display = 'flex'
    iconState = 'showing'
  }

  function hideIcon() {
    clearTimeout(fadeTimer)
    iconTarget = null
    iconState = 'hidden'
    if (anchorIcon) {
      anchorIcon.style.display = 'none'
      anchorIcon.classList.remove('pp-anchor-icon--fading') // 复位
      delete anchorIcon.dataset.paTarget
    }
  }

  /** el 是否 iconTarget 的祖先（含自身）。用于识别「走到祖先锚点地盘」。 */
  function isAncestorOrSelf(el) {
    for (var p = iconTarget; p && p.nodeType === 1; p = p.parentElement) {
      if (p === el) return true
    }
    return false
  }

  // 事件委托：mouseover 单入口驱动状态机（mouseout 在 DOM 事件语义下
  // 不可靠——捕获阶段 mouseover 在进入任何元素时必然触发，等价信息更全）
  document.addEventListener(
    'mouseover',
    function (e) {
      // 评论模式下锚点 icon 让位（不显示不换目标），由评论高亮接管（T4.1）
      if (commentMode) return
      // icon 自身：接住——取消渐隐恢复常显，不换目标
      if (e.target && e.target.classList && e.target.classList.contains('pp-anchor-icon')) {
        if (iconState === 'fading') cancelFade()
        return
      }
      var el = e.target && e.target.closest ? e.target.closest('[data-pa]') : null
      if (!iconTarget) {
        // 无当前目标：hover 到锚点即显示
        if (el) showIconFor(el)
        return
      }
      if (el === iconTarget) {
        // 回到目标：恢复常显
        cancelFade()
        return
      }
      // 有目标但 hover 到别处：
      if (!el) {
        // 非锚点区域：进入渐隐宽限期（1s 内可接住/可回）
        startFade()
        return
      }
      // hover 到了另一个锚点：
      if (isAncestorOrSelf(el)) {
        // 祖先锚点地盘（从按钮走到 main 空白区）：不换目标不闪跳，渐隐
        startFade()
        return
      }
      // 更具体的后代锚点（从 main 走到内部按钮）：切换目标
      if (iconTarget.contains(el)) {
        showIconFor(el)
        return
      }
      // 无关的兄弟锚点：直接换目标
      showIconFor(el)
    },
    true,
  )
  // mouseout 只处理「移出 iframe 窗口」（relatedTarget 为 null）——此时
  // 不再有后续 mouseover，icon 若常显会永久残留，立即渐隐。
  document.addEventListener(
    'mouseout',
    function (e) {
      if (e.relatedTarget === null) {
        startFade()
      }
    },
    true,
  )
  // 滚动/缩放时 icon 位置失效。优化策略：如果当前 hover 的目标元素仍在
  // 视口内，滚动结束后重新定位（200ms debounce）而不是直接隐藏——
  // 「滚动到深处锚点 → hover」场景下 Playwright 的 scrollIntoViewIfNeeded
  // 与 mouseover 几乎同时发生，直接隐藏会吞掉刚触发的显示。
  var scrollRepositionTimer = null
  window.addEventListener(
    'scroll',
    function () {
      if (iconTarget) {
        clearTimeout(scrollRepositionTimer)
        scrollRepositionTimer = setTimeout(function () {
          if (iconTarget) {
            var r = iconTarget.getBoundingClientRect()
            var vh = window.innerHeight || document.documentElement.clientHeight
            if (r.bottom > 0 && r.top < vh) {
              positionIcon(iconTarget) // 还在视口内：重新定位，保持可点
            } else {
              hideIcon()
            }
          }
        }, 200)
      }
    },
    { capture: true, passive: true },
  )
  window.addEventListener('resize', hideIcon)

  // ─── 反向联动（T3.2）：HIGHLIGHT_ANCHOR ─────────────────────
  // 宿主点文档「定位」按钮 → 本页滚动到锚点元素 + outline 脉冲闪烁 3 次。
  // 锚点不在本页时宿主先切 iframe src（等 READY）再发，本侧无需处理跨页。
  var HIGHLIGHT_CLASS = 'pp-anchor-flash'
  var highlightStyleEl = null

  function ensureHighlightStyle() {
    if (highlightStyleEl) return
    highlightStyleEl = document.createElement('style')
    // 闪烁：outline 脉冲 3 次（0.4s/次），结束后移除 class 恢复原样。
    // 用 box-shadow 双层描边模拟 outline 偏移——元素紧贴视口边缘时
    // outline 超界不显示，box-shadow 同层渲染更稳。
    highlightStyleEl.textContent =
      '@keyframes pp-anchor-flash {' +
      '0%{box-shadow:0 0 0 0 rgba(43,92,255,.9);}' +
      '50%{box-shadow:0 0 0 6px rgba(43,92,255,.45);}' +
      '100%{box-shadow:0 0 0 0 rgba(43,92,255,.9);}}' +
      '.' + HIGHLIGHT_CLASS + '{animation:pp-anchor-flash .4s ease-in-out 3;}'
    document.head.appendChild(highlightStyleEl)
  }

  function highlightAnchor(anchorId) {
    if (!anchorId) return false
    var el = document.querySelector('[data-pa="' + anchorId + '"]')
    if (!el) return false
    ensureHighlightStyle()
    // 滚动到视口中部（平滑），再闪烁
    try {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    } catch (e) {
      el.scrollIntoView() // 旧浏览器兜底
    }
    // 重触发动画：先移除再强制 reflow 再加回
    el.classList.remove(HIGHLIGHT_CLASS)
    // eslint-disable-next-line no-unused-expressions
    void el.offsetWidth
    el.classList.add(HIGHLIGHT_CLASS)
    // 动画结束（3 次 × 0.4s = 1.2s，留 200ms 余量）后清理 class
    clearTimeout(el.__ppFlashTimer)
    el.__ppFlashTimer = setTimeout(function () {
      el.classList.remove(HIGHLIGHT_CLASS)
    }, 1600)
    return true
  }

  // ─── 评论模式（T4.1）────────────────────────────────────────
  // 宿主 SET_COMMENT_MODE 开关。开启后：hover 元素蓝色高亮；click 在捕获
  // 阶段拦截（stopPropagation + preventDefault，避免触发原型自身交互——
  // 表单提交、弹窗打开等），一次性采集 DOM 定位 payload（技术方案 §2.3）
  // 发 ELEMENT_SELECTED。锚点 icon（◈）在评论模式下让位。

  var commentMode = false
  var commentHoverEl = null
  var commentStyleEl = null

  /** 当前页面相对 prototype/ 的路径（评论 JSON 的 prototype_page 口径）。
   * /proto/{slug}/prototype/pages/login.html → pages/login.html。
   * SPA 场景 hash 变化不改变本值（路由细节记在 interaction_state.route）。 */
  function currentPage() {
    var m = /^\/proto\/[a-z0-9-]+\/(?:prototype\/)?(.*)$/.exec(window.location.pathname)
    return m ? m[1] : window.location.pathname
  }

  /** 业务 hash：剔除宿主注入的 pp-nonce 认证参数（不属于原型路由）。 */
  function cleanHash() {
    var h = window.location.hash || ''
    if (!h) return ''
    var body = h.charAt(0) === '#' ? h.slice(1) : h
    var kept = body.split('&').filter(function (kv) {
      return kv && kv.indexOf('pp-nonce=') !== 0
    })
    return kept.length ? '#' + kept.join('&') : ''
  }

  /** 元素开标签序列化（含属性），用于 outer_html 的祖先上下文包裹。 */
  function openTagOf(node) {
    var s = '<' + node.tagName.toLowerCase()
    for (var i = 0; i < node.attributes.length; i++) {
      var a = node.attributes[i]
      s += ' ' + a.name + '="' + String(a.value).replace(/"/g, '&quot;') + '"'
    }
    return s + '>'
  }

  /** outer_html：目标元素 outerHTML + 最多 2 层元素祖先的开/闭标签包裹。
   * 设计（产品方案 §3.3「含 2–3 层祖先」）：祖先提供挂载上下文（与
   * css_path 互补），不含兄弟内容（兄弟噪音对 AI 定位无价值且撑爆体积）；
   * body/html 不包（无意义）。超过 4KB 截断。 */
  var OUTER_HTML_LIMIT = 4096
  function outerHtmlWithAncestors(el) {
    var chain = []
    var p = el.parentElement
    while (p && p !== document.body && p.nodeType === 1 && chain.length < 2) {
      chain.unshift(p)
      p = p.parentElement
    }
    var s = ''
    for (var i = 0; i < chain.length; i++) s += openTagOf(chain[i])
    s += el.outerHTML
    for (var j = chain.length - 1; j >= 0; j--) {
      s += '</' + chain[j].tagName.toLowerCase() + '>'
    }
    if (s.length > OUTER_HTML_LIMIT) s = s.slice(0, OUTER_HTML_LIMIT) + '…(截断)'
    return s
  }

  /** text_excerpt：文本内容 200 字截断；表单控件取 value/placeholder 兜底。 */
  function textExcerptOf(el) {
    var t = (el.textContent || '').trim()
    if (!t && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) {
      t = (el.value || el.placeholder || '').trim()
    }
    return t.length > 200 ? t.slice(0, 200) + '…' : t
  }

  /** interaction_state.modal_open：可见 position:fixed 元素近似判断（技术
   * 方案 §2.3「统计可见的 position:fixed 高 z-index 元素」）。命中规则：
   *   a) 覆盖面积 ≥ 视口 40%（全屏遮罩型弹层，如 inset:0 的 modal-mask，
   *      这类常见写法不设 z-index，纯 z-index 判定会漏）
   *   b) z-index ≥ 100 且高度 ≥ 视口 20%（抽屉/面板型；细横条视为固定
   *      导航栏排除，避免普通顶栏误报）
   * bridge 注入物（锚点 icon 等）不计入。 */
  function detectModalOpen() {
    var els = document.querySelectorAll('body *')
    var vw = window.innerWidth
    var vh = window.innerHeight
    if (!vw || !vh) return false
    for (var i = 0; i < els.length; i++) {
      var el = els[i]
      if (el.closest('.pp-anchor-icon')) continue
      var cs = window.getComputedStyle(el)
      if (cs.position !== 'fixed' || cs.display === 'none' || cs.visibility === 'hidden') continue
      if (parseFloat(cs.opacity) < 0.1) continue
      var r = el.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0) continue
      if ((r.width * r.height) / (vw * vh) >= 0.4) return true
      var z = parseInt(cs.zIndex, 10) || 0
      if (z >= 100 && r.height >= vh * 0.2) return true
    }
    return false
  }

  /** payload 一次性采集（技术方案 §2.3 字段表，产品方案 §3.3 DOM 定位组）。
   * 服务端 schema 校验见 server/reviews.py（契约测试 tests/test_reviews.py）。 */
  function collectPayload(el) {
    var isPageRoot = el === document.body || el === document.documentElement
    var nearest = el.parentElement ? el.parentElement.closest('[data-pa]') : null
    return {
      target_type: isPageRoot ? 'page' : 'dom',
      prototype_page: currentPage(),
      anchor_id: (el.getAttribute && el.getAttribute('data-pa')) || '',
      nearest_anchor_id: nearest ? nearest.getAttribute('data-pa') : '',
      css_path: isPageRoot ? 'body' : cssPathOf(el),
      outer_html: outerHtmlWithAncestors(el),
      text_excerpt: textExcerptOf(el),
      interaction_state: {
        modal_open: detectModalOpen(),
        viewport: window.innerWidth + 'x' + window.innerHeight,
        scroll_y: Math.round(window.scrollY || window.pageYOffset || 0),
        route: currentPage() + cleanHash(),
      },
    }
  }

  function ensureCommentStyle() {
    if (commentStyleEl) return
    commentStyleEl = document.createElement('style')
    commentStyleEl.textContent =
      'html.pp-comment-mode{cursor:crosshair;}' +
      '.pp-comment-hover{box-shadow:0 0 0 2px #2b5cff !important;cursor:crosshair;}'
    document.head.appendChild(commentStyleEl)
  }

  function clearCommentHover() {
    if (commentHoverEl) {
      commentHoverEl.classList.remove('pp-comment-hover')
      commentHoverEl = null
    }
  }

  function setCommentMode(on) {
    commentMode = !!on
    ensureCommentStyle()
    document.documentElement.classList.toggle('pp-comment-mode', commentMode)
    if (commentMode) {
      hideIcon() // 锚点 icon 让位（mouseover 状态机入口已短路，这里清掉残留显示）
    } else {
      clearCommentHover()
    }
  }

  // hover 高亮：mouseover 捕获阶段委托（与锚点 icon 同一事件模型）。
  // body/html 不加高亮（整页变蓝无意义），点击仍可选（target_type=page）。
  document.addEventListener(
    'mouseover',
    function (e) {
      if (!commentMode) return
      var el = e.target
      if (!el || !el.closest) return
      if (el.closest('.pp-anchor-icon')) return
      if (el === document.documentElement || el === document.body) {
        clearCommentHover()
        return
      }
      if (commentHoverEl === el) return
      clearCommentHover()
      commentHoverEl = el
      el.classList.add('pp-comment-hover')
    },
    true,
  )
  // 移出 iframe 窗口时清高亮（无后续 mouseover，防残留）
  document.addEventListener(
    'mouseout',
    function (e) {
      if (commentMode && e.relatedTarget === null) clearCommentHover()
    },
    true,
  )

  // 评论模式 click：捕获阶段拦截——在原型自身的冒泡 handler 之前执行，
  // preventDefault 阻止默认行为（表单提交/链接导航），stopPropagation 阻断
  // 原型交互逻辑；采集后上报。局限：原型若也用捕获阶段且先注册，仍会先执行
  // （罕见写法，一期接受，记录在案）。
  document.addEventListener(
    'click',
    function (e) {
      if (!commentMode) return
      var el = e.target
      if (!el || !el.closest) return
      if (el.closest('.pp-anchor-icon')) return // bridge 注入物不是评论目标
      e.preventDefault()
      e.stopPropagation()
      send('ELEMENT_SELECTED', { payload: collectPayload(el) })
    },
    true,
  )

  // ─── ROUTE_CHANGE（T4.1，SPA 路由切换上报）──────────────────
  // 多页原型的页面切换 = iframe src 变化 = 整页重载（READY 已覆盖）；
  // SPA 原型的路由切换（pushState/replaceState/hash 变化）页面不重载，
  // bridge 主动上报，宿主侧追踪当前路由。
  function notifyRouteChange() {
    send('ROUTE_CHANGE', { page: currentPage(), route: currentPage() + cleanHash() })
  }
  var rawPushState = history.pushState
  history.pushState = function () {
    var r = rawPushState.apply(this, arguments)
    notifyRouteChange()
    return r
  }
  var rawReplaceState = history.replaceState
  history.replaceState = function () {
    var r = rawReplaceState.apply(this, arguments)
    notifyRouteChange()
    return r
  }
  window.addEventListener('hashchange', notifyRouteChange)
  window.addEventListener('popstate', notifyRouteChange)

  // ─── 消息分发 ───────────────────────────────────────────────

  // 宿主 → iframe：来源 origin 与 referrer 一致 + nonce 匹配才接受
  // （宿主侧 targetOrigin 用 '*'，安全靠本侧 origin + nonce 双重校验）
  window.addEventListener('message', function (event) {
    if (!hostOrigin || event.origin !== hostOrigin) return
    var msg = event.data || {}
    if (!NONCE || msg.nonce !== NONCE) return

    if (msg.type === 'PING') {
      send('ECHO', { echo: 'pong-' + msg.nonce, page: window.location.pathname })
    } else if (msg.type === 'SET_COMMENT_MODE') {
      setCommentMode(!!msg.enabled)
    } else if (msg.type === 'COLLECT_PAGE') {
      // T4.2「评论本页」按钮：宿主请求采集页面根（body）payload——
      // 页面评论目标为页面根元素（产品方案 §4.5），走 ELEMENT_SELECTED
      // 统一入口，宿主收到后弹评论框
      send('ELEMENT_SELECTED', { payload: collectPayload(document.body) })
    } else if (msg.type === 'HIGHLIGHT_ANCHOR') {
      // 反向联动（T3.2）：false 表示本页没有该锚点（正常不会发生——
      // 宿主已按页面地图切页；万一落空回 ACK 让宿主 toast 提示）
      var hit = highlightAnchor(msg.anchorId)
      send('HIGHLIGHT_ACK', { anchorId: msg.anchorId, hit: hit })
    } else if (msg.type === 'TAKE_SCREENSHOT') {
      var target = msg.cssPath ? queryTarget(msg.cssPath) : null
      captureFullPage(target)
        .then(function (result) {
          // Blob 经 postMessage 结构化克隆直接回传
          send('SCREENSHOT_RESULT', {
            requestId: msg.requestId,
            blob: result.blob,
            highlight: result.highlight,
            width: result.width,
            height: result.height,
          })
        })
        .catch(function (err) {
          send('SCREENSHOT_ERROR', { requestId: msg.requestId, error: String(err && err.message) })
        })
    }
  })

  // ─── 就绪上报 ───────────────────────────────────────────────

  function ready() {
    send('READY', { page: window.location.pathname })
    reportAnchors() // 首次锚点清单随 READY 一起上报
    startObserver()
  }
  if (document.readyState === 'complete') {
    ready()
  } else {
    window.addEventListener('load', ready)
  }
})()
