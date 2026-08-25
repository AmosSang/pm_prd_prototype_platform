import type { APIRequestContext } from '@playwright/test'

/**
 * E2E 建场工具（T8.1 去 Git 本地化）。
 *
 * 项目内容不再走 git 绑定：API 建项目（只填名称）→ 上传接口落内容
 * （原型 zip / PRD md）。zip 用内置 STORE 型最小构造器（无第三方依赖，
 * 服务端 zipfile 兼容）。
 */

export interface ProjectInfo {
  id: number
  project_id: string
  name: string
}

export interface ProjectContent {
  /** prototype/ 下相对路径 → 文件内容 */
  protoFiles: Record<string, string>
  /** PRD markdown（prd/ 唯一一份；文件名保留） */
  prdFile?: { name: string; content: string }
}

// ───────────────────────── 最小 zip 构造（STORE，无压缩）─────────────────────────

const CRC_TABLE = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c >>> 0
  }
  return t
})()

function crc32(buf: Buffer): number {
  let c = 0xffffffff
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

/** 造 STORE 型 zip（{相对路径: 内容}）；Python zipfile / 平台解压均兼容。 */
export function buildZip(files: Record<string, string>): Buffer {
  const names = Object.keys(files)
  const parts: Buffer[] = []
  const centrals: Buffer[] = []
  let offset = 0
  for (const name of names) {
    const data = Buffer.from(files[name], 'utf-8')
    const nb = Buffer.from(name, 'utf-8')
    const crc = crc32(data)

    const lh = Buffer.alloc(30)
    lh.writeUInt32LE(0x04034b50, 0)
    lh.writeUInt16LE(20, 4) // version needed
    lh.writeUInt16LE(0, 6) // flags
    lh.writeUInt16LE(0, 8) // method: store
    lh.writeUInt16LE(0, 10) // mod time
    lh.writeUInt16LE(0x21, 12) // mod date（1980-01-01）
    lh.writeUInt32LE(crc, 14)
    lh.writeUInt32LE(data.length, 18)
    lh.writeUInt32LE(data.length, 22)
    lh.writeUInt16LE(nb.length, 26)
    lh.writeUInt16LE(0, 28) // extra len
    parts.push(lh, nb, data)

    const ch = Buffer.alloc(46)
    ch.writeUInt32LE(0x02014b50, 0)
    ch.writeUInt16LE(20, 4) // version made by
    ch.writeUInt16LE(20, 6) // version needed
    ch.writeUInt16LE(0, 8) // flags
    ch.writeUInt16LE(0, 10) // method
    ch.writeUInt16LE(0, 12) // time
    ch.writeUInt16LE(0x21, 14) // date
    ch.writeUInt32LE(crc, 16)
    ch.writeUInt32LE(data.length, 20)
    ch.writeUInt32LE(data.length, 24)
    ch.writeUInt16LE(nb.length, 28)
    ch.writeUInt16LE(0, 30) // extra len
    ch.writeUInt16LE(0, 32) // comment len
    ch.writeUInt16LE(0, 34) // disk number
    ch.writeUInt16LE(0, 36) // internal attrs
    ch.writeUInt32LE(0, 38) // external attrs
    ch.writeUInt32LE(offset, 42) // local header offset
    centrals.push(ch, nb)

    offset += 30 + nb.length + data.length
  }
  const cd = Buffer.concat(centrals)
  const eocd = Buffer.alloc(22)
  eocd.writeUInt32LE(0x06054b50, 0)
  eocd.writeUInt16LE(names.length, 8)
  eocd.writeUInt16LE(names.length, 10)
  eocd.writeUInt32LE(cd.length, 12)
  eocd.writeUInt32LE(offset, 16)
  return Buffer.concat([...parts, cd, eocd])
}

// ───────────────────────── API 建场 ─────────────────────────

async function mustOk(res: { ok(): boolean; status(): number; text(): Promise<string> }, what: string) {
  if (!res.ok()) throw new Error(`${what} 失败：${res.status()} ${await res.text()}`)
}

/** API 建项目（T8.1：只填名称；创建者为当前登录用户）。 */
export async function createProject(request: APIRequestContext, name: string): Promise<ProjectInfo> {
  const res = await request.post('/api/projects', { data: { name } })
  await mustOk(res, `createProject(${name})`)
  return (await res.json()).data
}

/** 上传原型 zip（创建者专属；T8.1 最小上传接口）。 */
export async function uploadPrototype(
  request: APIRequestContext,
  pid: number,
  protoFiles: Record<string, string>,
): Promise<void> {
  const res = await request.post(`/api/projects/${pid}/prototype`, {
    multipart: {
      zip: { name: 'proto.zip', mimeType: 'application/zip', buffer: buildZip(protoFiles) },
    },
  })
  await mustOk(res, 'uploadPrototype')
}

/** 上传 PRD markdown（创建者专属；替换 prd/ 旧文档）。 */
export async function uploadPrd(
  request: APIRequestContext,
  pid: number,
  filename: string,
  content: string,
): Promise<void> {
  const res = await request.post(`/api/projects/${pid}/prd`, {
    multipart: {
      file: { name: filename, mimeType: 'text/markdown', buffer: Buffer.from(content, 'utf-8') },
    },
  })
  await mustOk(res, 'uploadPrd')
}

/** 标准建场：建项目 + 上传原型与 PRD。 */
export async function createProjectWithContent(
  request: APIRequestContext,
  name: string,
  content: ProjectContent,
): Promise<ProjectInfo> {
  const p = await createProject(request, name)
  await uploadPrototype(request, p.id, content.protoFiles)
  if (content.prdFile) {
    await uploadPrd(request, p.id, content.prdFile.name, content.prdFile.content)
  }
  return p
}

/** 项目本地目录（评论文件断言用；Playwright cwd = tests/，DATA_DIR 默认 platform/data）。 */
export function projectDirOf(slug: string): string {
  return `../data/projects/${slug}`
}
