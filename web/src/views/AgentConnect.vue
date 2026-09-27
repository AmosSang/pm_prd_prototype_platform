<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createToken, listTokens, revokeToken, type ApiTokenInfo } from '../agent'
import { currentUser } from '../auth'
import { MCP_URL } from '../mcp-url'

const tokens = ref<ApiTokenInfo[]>([])
const loading = ref(false)

// 生成对话框：两步（填名称 → 一次性明文展示）
const createVisible = ref(false)
const creating = ref(false)
const createForm = reactive({ name: '' })
const created = ref<(ApiTokenInfo & { plaintext: string }) | null>(null)

/** 配置模板（占位版，页面常驻展示）：mcp.json 粘贴即用。 */
const configTemplate = computed(() => buildConfig('<你的 Token（ppp_ 开头，生成时显示一次）>'))

/** 配置（已含真实 Token 版，生成后展示）。 */
const configWithToken = computed(() => (created.value ? buildConfig(created.value.plaintext) : ''))

/** 一次性明文（生成后展示；v-else 分支内非空）。 */
const plaintext = computed(() => created.value?.plaintext || '')

function buildConfig(token: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        'product-plan-platform': {
          type: 'http',
          url: MCP_URL,
          headers: { Authorization: `Bearer ${token}` },
        },
      },
    },
    null,
    2,
  )
}

function fmtTime(s: string | null): string {
  if (!s) return '—'
  return s.replace('T', ' ').slice(0, 16)
}

async function refresh() {
  loading.value = true
  try {
    tokens.value = await listTokens()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : 'Token 列表加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.name = ''
  created.value = null
  createVisible.value = true
}

async function onSubmitCreate() {
  if (creating.value) return
  creating.value = true
  try {
    created.value = await createToken(createForm.name.trim())
    await refresh()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '生成失败')
  } finally {
    creating.value = false
  }
}

function closeCreate() {
  createVisible.value = false
  created.value = null
}

async function onRevoke(t: ApiTokenInfo) {
  try {
    await ElMessageBox.confirm(
      `撤销「${t.name}」后，正在使用该 Token 的 Agent 下一次调用即被拒绝（立即生效，不可恢复）。`,
      '撤销 Token',
      { type: 'warning', confirmButtonText: '撤销', cancelButtonText: '取消' },
    )
  } catch {
    return // 取消
  }
  try {
    await revokeToken(t.id)
    ElMessage.success(`「${t.name}」已撤销`)
    await refresh()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '撤销失败')
  }
}

async function copyText(text: string, tip = '已复制') {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(tip)
  } catch {
    // 非安全上下文（http 且非 localhost）clipboard 不可用时的兜底
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      ElMessage.success(tip)
    } catch {
      ElMessage.warning('复制失败，请手动选择文本复制')
    }
    document.body.removeChild(ta)
  }
}

onMounted(refresh)
</script>

<template>
  <main class="agent-connect">
    <header class="bar">
      <h1>Agent 接入</h1>
      <el-button type="primary" data-testid="agent-token-create-open" @click="openCreate">
        生成 Token
      </el-button>
    </header>

    <el-alert type="info" :closable="false" class="intro">
      把平台接入支持 MCP 的 AI Agent（如 WorkBuddy）：Agent 可读取项目、拉取评论与 PRD、上传新版原型。
      Token 绑定当前账号<template v-if="currentUser">（{{ currentUser.name }}）</template>，
      权限与网页端一致；工具调用受 60 次/分钟限流。
    </el-alert>

    <section class="card">
      <h2>1. Token 管理</h2>
      <p class="section-hint">每个 Token 独立撤销；明文仅在生成时显示一次，丢失请重新生成。</p>
      <p v-if="loading" class="hint">加载中…</p>
      <p v-else-if="tokens.length === 0" class="hint">还没有 Token，点击右上角「生成 Token」创建。</p>
      <el-table v-else :data="tokens" data-testid="agent-token-table" stripe>
        <el-table-column prop="name" label="名称" min-width="180" />
        <el-table-column label="生成时间" width="170">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="最近使用" width="170">
          <template #default="{ row }">{{ fmtTime(row.last_used_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.revoked ? 'info' : 'success'" size="small">
              {{ row.revoked ? '已撤销' : '有效' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!row.revoked"
              size="small"
              type="danger"
              plain
              data-testid="agent-token-revoke"
              @click="onRevoke(row)"
            >
              撤销
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="card">
      <h2>2. 接入配置</h2>
      <p class="section-hint">
        MCP 服务地址：
        <code class="inline-code" data-testid="agent-mcp-url">{{ MCP_URL }}</code>
        <el-button size="small" link type="primary" @click="copyText(MCP_URL, 'MCP 地址已复制')">
          复制地址
        </el-button>
      </p>
      <p class="section-hint">
        把下面配置粘贴到 WorkBuddy 的 mcp.json（用户级 <code class="inline-code">~/.workbuddy/mcp.json</code>，
        或项目级 <code class="inline-code">&lt;项目&gt;/.workbuddy/mcp.json</code>），
        将 <code class="inline-code">&lt;你的 Token&gt;</code> 替换为生成时显示的明文：
      </p>
      <div class="code-block">
        <pre data-testid="agent-config-template">{{ configTemplate }}</pre>
        <el-button
          size="small"
          class="copy-btn"
          data-testid="agent-config-copy"
          @click="copyText(configTemplate, '配置已复制')"
        >
          复制
        </el-button>
      </div>
      <el-alert
        type="warning"
        :closable="false"
        title="Token 是账号凭证，等同于登录态；请勿粘贴到公开渠道。泄露后请立即在下方列表撤销。"
      />
    </section>

    <section class="card">
      <h2>3. 常用指令示例</h2>
      <ul class="examples">
        <li>「列出产品方案展示平台上我可以访问的所有项目」</li>
        <li>「拉取『XX 项目』的全部评论，按修改计划流程整理成待办」</li>
        <li>「把这份 zip 作为『XX 项目』的新版原型上传」（需管理者权限，覆盖旧版本）</li>
      </ul>
      <p class="section-hint">
        配置完成后，在 WorkBuddy 对话里直接说需求即可，无需指定工具名。
      </p>
    </section>

    <el-dialog
      v-model="createVisible"
      :title="created ? 'Token 已生成' : '生成 Token'"
      width="560px"
      :close-on-click-modal="false"
      data-testid="agent-token-dialog"
      @closed="created = null"
    >
      <!-- 第一步：命名 -->
      <el-form v-if="!created" label-position="top" @submit.prevent>
        <el-form-item label="用途名称（便于日后辨认，如 workbuddy-agent）" required>
          <el-input
            v-model="createForm.name"
            placeholder="如：workbuddy-agent"
            data-testid="agent-token-name"
            maxlength="50"
            @keyup.enter="onSubmitCreate"
          />
        </el-form-item>
      </el-form>

      <!-- 第二步：一次性明文 + 粘贴即用配置 -->
      <template v-else>
        <p class="plain-warn">
          Token 明文<strong>仅显示这一次</strong>，关闭后无法再次查看。请立即复制保存。
        </p>
        <div class="code-block">
          <pre class="token-plain" data-testid="agent-token-plaintext">{{ plaintext }}</pre>
          <el-button size="small" class="copy-btn" @click="copyText(plaintext, 'Token 已复制')">
            复制
          </el-button>
        </div>
        <p class="section-hint">粘贴即用配置（已内含此 Token）：</p>
        <div class="code-block">
          <pre data-testid="agent-token-config">{{ configWithToken }}</pre>
          <el-button
            size="small"
            class="copy-btn"
            @click="copyText(configWithToken, '配置已复制，可直接粘贴到 mcp.json')"
          >
            复制
          </el-button>
        </div>
      </template>

      <template #footer>
        <template v-if="!created">
          <el-button @click="closeCreate">取消</el-button>
          <el-button
            type="primary"
            :loading="creating"
            :disabled="!createForm.name.trim()"
            data-testid="agent-token-create-submit"
            @click="onSubmitCreate"
          >
            {{ creating ? '生成中…' : '生成' }}
          </el-button>
        </template>
        <el-button v-else type="primary" data-testid="agent-token-done" @click="closeCreate">
          我已保存，关闭
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.agent-connect {
  max-width: 860px;
  margin: 0 auto;
  padding: 28px 20px 48px;
  font-family: var(--pp-font);
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
}
.bar h1 { font-size: 22px; margin: 0; font-weight: 600; letter-spacing: 0.2px; }
.intro { margin-bottom: 20px; }
.card {
  background: var(--pp-surface);
  border: 1px solid var(--pp-border);
  border-radius: 10px;
  padding: 18px 20px 20px;
  margin-bottom: 18px;
}
.card h2 { font-size: 15px; margin: 0 0 6px; font-weight: 600; }
.section-hint { color: var(--pp-text-3); font-size: 13px; margin: 6px 0; }
.hint { color: var(--pp-text-3); font-size: 13px; }
.inline-code {
  background: #f5f6f8;
  border: 1px solid var(--pp-border);
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
  font-family: var(--pp-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
}
.code-block { position: relative; margin: 8px 0 12px; }
.code-block pre {
  background: #f7f8fa;
  border: 1px solid var(--pp-border);
  border-radius: 8px;
  padding: 12px 14px;
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  font-family: var(--pp-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  white-space: pre-wrap;
  word-break: break-all;
}
.copy-btn { position: absolute; top: 8px; right: 8px; }
.token-plain { color: #b26a00; font-weight: 600; }
.plain-warn { font-size: 13px; margin: 0 0 10px; color: var(--pp-text-2); }
.plain-warn strong { color: #d03050; }
.examples { margin: 8px 0; padding-left: 20px; font-size: 13px; line-height: 2; color: var(--pp-text-2); }
</style>
