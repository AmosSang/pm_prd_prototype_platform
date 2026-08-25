/** 原型 iframe 的 origin（可配置，单一事实源）。
 *
 * 解析优先级：
 * 1. `VITE_PROTO_ORIGIN`（web/.env，构建期注入）——生产若原型由独立 host:port 提供，
 *    显式设为 `http://<host>:<PORT>`（PORT 即后端 PORT）；
 * 2. 开发（Vite dev）：默认 `http://localhost:8081`（后端 Flask 默认端口）；
 * 3. 生产构建版：默认 `window.location.origin`——Nginx 同域反代 /proto 时无需配置
 *    （等价于后端 WEB_ORIGIN 指向的宿主）。
 */
export function resolveProtoOrigin(): string {
  const v = ((import.meta.env.VITE_PROTO_ORIGIN as string | undefined) || '').trim()
  if (v) return v.replace(/\/+$/, '')
  return import.meta.env.DEV ? 'http://localhost:8081' : window.location.origin
}

export const PROTO_ORIGIN = resolveProtoOrigin()
