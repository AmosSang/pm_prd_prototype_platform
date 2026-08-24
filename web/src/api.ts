/** 统一 API 客户端：401 自动跳登录，错误统一抛 Error(msg)。 */

export class ApiError extends Error {
  code: number
  constructor(msg: string, code: number) {
    super(msg)
    this.code = code
  }
}

async function request<T = any>(url: string, init?: RequestInit): Promise<T> {
  // FormData（文件上传）不能手动设 Content-Type——浏览器须自动生成
  // multipart boundary，强设 application/json 会让后端解析不到文件
  const isForm = init?.body instanceof FormData
  const res = await fetch(url, {
    credentials: 'include',
    headers: isForm
      ? { ...(init?.headers || {}) }
      : { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  let body: any = null
  try {
    body = await res.json()
  } catch {
    throw new ApiError(`响应解析失败（${res.status}）`, res.status)
  }
  if (res.status === 401) {
    // 未登录 → 跳登录页（带来源，登录后回来）
    const back = encodeURIComponent(location.pathname + location.search)
    if (!location.pathname.startsWith('/login')) {
      location.href = `/login?back=${back}`
    }
    throw new ApiError(body?.msg || '未登录', 401)
  }
  if (body.code !== 0) {
    throw new ApiError(body.msg || `请求失败（${res.status}）`, body.code ?? res.status)
  }
  return body.data as T
}

export const api = {
  get: <T = any>(url: string) => request<T>(url),
  post: <T = any>(url: string, data?: unknown) =>
    request<T>(url, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) }),
  patch: <T = any>(url: string, data?: unknown) =>
    request<T>(url, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: <T = any>(url: string) => request<T>(url, { method: 'DELETE' }),
  upload: <T = any>(url: string, fd: FormData) => request<T>(url, { method: 'POST', body: fd }),
}
