/**
 * bridge.js — 注入原型 iframe 的桥接脚本（T1.1）
 *
 * 认证机制：sandbox（无 allow-same-origin）下 iframe origin 为不透明 "null"，
 * 无法按 origin 校验。采用 URL nonce：宿主在 iframe src 的 hash 中携带随机
 * nonce（#pp-nonce=xxx），bridge 每条消息回带，宿主校验匹配后才信任。
 *
 * 协议见《一期技术实现方案-V1.md》§2.2。约束：原生 JS、零依赖、幂等。
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

  // 宿主 → iframe：来源 origin 与 referrer 一致 + nonce 匹配才接受
  // （POSTMESSAGE 宿主侧用 '*' targetOrigin，安全靠本侧 origin + nonce 双重校验）
  window.addEventListener('message', function (event) {
    if (!hostOrigin || event.origin !== hostOrigin) return
    var msg = event.data || {}
    if (!NONCE || msg.nonce !== NONCE) return
    if (msg.type === 'PING') {
      send('ECHO', { echo: 'pong-' + msg.nonce, page: window.location.pathname })
    }
  })

  function ready() {
    send('READY', { page: window.location.pathname })
  }
  if (document.readyState === 'complete') {
    ready()
  } else {
    window.addEventListener('load', ready)
  }
})()
