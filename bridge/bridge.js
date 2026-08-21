/**
 * bridge.js — 注入原型 iframe 的桥接脚本
 *
 * T1.1：READY / PING-ECHO 通信地基（URL nonce 认证）
 * T1.2：TAKE_SCREENSHOT 整页截图（modern-screenshot，沙箱兼容）+ Blob 回传 + highlight_rect 计算
 *
 * 认证机制：sandbox（无 allow-same-origin）下 iframe origin 为不透明 "null"，
 * 无法按 origin 校验。采用 URL nonce：宿主在 iframe src 的 hash 中携带随机
 * nonce（#pp-nonce=xxx），bridge 每条消息回带，宿主校验匹配后才信任。
 *
 * 协议见《一期技术实现方案-V1.md》§2.2。约束：原生 JS、零依赖、幂等
 * （html2canvas 为平台自托管 vendor，从 /vendor/html2canvas.min.js 懒加载）。
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
   * 库对 sandboxWindow 判空后返回空样式表（正常分支，不抛异常），
   * 代价是 diff 样式包含少量浏览器默认值，视觉影响可忽略。
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

  // ─── 消息分发 ───────────────────────────────────────────────

  // 宿主 → iframe：来源 origin 与 referrer 一致 + nonce 匹配才接受
  // （宿主侧 targetOrigin 用 '*'，安全靠本侧 origin + nonce 双重校验）
  window.addEventListener('message', function (event) {
    if (!hostOrigin || event.origin !== hostOrigin) return
    var msg = event.data || {}
    if (!NONCE || msg.nonce !== NONCE) return

    if (msg.type === 'PING') {
      send('ECHO', { echo: 'pong-' + msg.nonce, page: window.location.pathname })
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
  }
  if (document.readyState === 'complete') {
    ready()
  } else {
    window.addEventListener('load', ready)
  }
})()
