/** MCP 服务地址（可配置，单一事实源，T12.2）。
 *
 * 解析优先级：
 * 1. `VITE_MCP_URL`（web/.env，构建期注入）——生产若 MCP 独立域名/端口，显式配置；
 * 2. 开发（Vite dev）：默认 `http://localhost:8082/mcp`（T12.3 MCP 服务默认端口）；
 * 3. 生产构建版：默认 `window.location.origin + '/mcp'`——Nginx 同域反代 /mcp 时无需配置。
 */
export function resolveMcpUrl(): string {
  const v = ((import.meta.env.VITE_MCP_URL as string | undefined) || '').trim()
  if (v) return v.replace(/\/+$/, '')
  return import.meta.env.DEV ? 'http://localhost:8082/mcp' : `${window.location.origin}/mcp`
}

export const MCP_URL = resolveMcpUrl()
