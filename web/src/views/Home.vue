<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ApiError } from '../api'
import {
  createProject,
  deleteProject,
  listProjects,
  uploadPrototype,
  uploadPrd,
  type ProjectInfo,
} from '../projects'

const backendStatus = ref<'checking' | 'ok' | 'fail'>('checking')
const projects = ref<ProjectInfo[]>([])
const loading = ref(false)

// 新建项目对话框（T8.1：只填名称；内容由上传接口补充）
const dialogVisible = ref(false)
const creating = ref(false)
const form = reactive({
  name: '',
})

// 内容上传对话框（T8.2：创建者专属；zip 带进度条）
const uploadFor = ref<ProjectInfo | null>(null)
const protoUploading = ref(false)
const protoPercent = ref(0)
const protoInput = ref<HTMLInputElement | null>(null)
const prdInput = ref<HTMLInputElement | null>(null)
const PROTO_MAX = 100 * 1024 * 1024
const PRD_MAX = 5 * 1024 * 1024
const uploadTitle = computed(() => (uploadFor.value ? `上传内容 · ${uploadFor.value.name}` : ''))

async function refresh() {
  loading.value = true
  try {
    projects.value = await listProjects()
  } catch (e) {
    // 401 已由 api.ts 统一跳转；此处提示其余错误
    if (!(e instanceof ApiError && e.code === 401)) {
      ElMessage.error(e instanceof Error ? e.message : '项目列表加载失败')
    }
  } finally {
    loading.value = false
  }
}

async function onSubmit() {
  if (creating.value) return
  creating.value = true
  try {
    const p = await createProject({ name: form.name })
    ElMessage.success(`项目「${p.name}」创建成功`)
    dialogVisible.value = false
    form.name = ''
    await refresh()
    // 新建项目直接打开上传（空项目第一步就是传内容）
    openUpload(p)
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  } finally {
    creating.value = false
  }
}

function openUpload(p: ProjectInfo) {
  uploadFor.value = p
  protoPercent.value = 0
  protoUploading.value = false
}

function onProtoPicked(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f || !uploadFor.value) return
  if (!f.name.toLowerCase().endsWith('.zip')) {
    ElMessage.error('原型包必须是 zip 格式')
    return
  }
  if (f.size > PROTO_MAX) {
    ElMessage.error('原型包超过 100MB 上限')
    return
  }
  protoUploading.value = true
  protoPercent.value = 0
  uploadPrototype(uploadFor.value.id, f, (pct) => (protoPercent.value = pct))
    .then(() => {
      ElMessage.success('原型上传成功')
      protoPercent.value = 100
      refresh()
    })
    .catch((err) => {
      ElMessage.error(err instanceof Error ? err.message : '上传失败')
    })
    .finally(() => {
      protoUploading.value = false
      if (protoInput.value) protoInput.value.value = '' // 同名文件可重选
    })
}

function onPrdPicked(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f || !uploadFor.value) return
  if (!f.name.toLowerCase().endsWith('.md')) {
    ElMessage.error('仅支持 markdown 文档')
    return
  }
  if (f.size > PRD_MAX) {
    ElMessage.error('文档超过 5MB 上限')
    return
  }
  uploadPrd(uploadFor.value.id, f)
    .then(() => {
      ElMessage.success('PRD 上传成功')
      refresh()
    })
    .catch((err) => {
      ElMessage.error(err instanceof Error ? err.message : '上传失败')
    })
    .finally(() => {
      if (prdInput.value) prdInput.value.value = ''
    })
}

async function onDelete(p: ProjectInfo) {
  try {
    await ElMessageBox.confirm(
      `删除项目「${p.name}」将同时清除其原型、PRD 与全部评论，且不可恢复。`,
      '删除项目',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 取消
  }
  try {
    await deleteProject(p.id)
    ElMessage.success(`项目「${p.name}」已删除`)
    refresh()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败')
  }
}

onMounted(async () => {
  refresh()
  try {
    const res = await fetch('/api/health')
    const body = await res.json()
    backendStatus.value = body.code === 0 ? 'ok' : 'fail'
  } catch {
    backendStatus.value = 'fail'
  }
})
</script>

<template>
  <main class="home">
    <header class="bar">
      <h1>项目列表</h1>
      <span class="status">
        后端连接状态：
        <span :class="backendStatus">{{
          backendStatus === 'ok' ? '正常' : backendStatus === 'checking' ? '检测中…' : '异常'
        }}</span>
      </span>
      <el-button type="primary" data-testid="new-project" @click="dialogVisible = true">
        新建项目
      </el-button>
    </header>

    <p v-if="loading" class="hint">加载中…</p>

    <section v-else class="cards">
      <div
        v-for="p in projects"
        :key="p.id"
        class="card"
        :data-testid="`project-card-${p.project_id}`"
      >
        <h2>{{ p.name }}</h2>
        <p class="meta">{{ p.project_id }} · 创建者 {{ p.creator.name }}</p>
        <p v-if="p.content_updated_at" class="meta">内容更新于 {{ p.content_updated_at.slice(0, 10) }}</p>
        <div class="card-actions">
          <router-link class="open" :to="`/project/${p.project_id}`" data-testid="open-project">
            打开分屏查看器 →
          </router-link>
          <span v-if="p.is_creator" class="owner-actions">
            <el-button size="small" :data-testid="`upload-${p.project_id}`" @click="openUpload(p)">
              上传内容
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :data-testid="`del-${p.project_id}`"
              @click="onDelete(p)"
            >
              删除
            </el-button>
          </span>
        </div>
      </div>

      <p v-if="projects.length === 0" class="hint">
        还没有项目，点右上角「新建项目」开始
      </p>
    </section>

    <el-dialog v-model="dialogVisible" title="新建项目" width="520px">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="项目名" required>
          <el-input v-model="form.name" placeholder="如：CRM 改版" data-testid="form-name" maxlength="50" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="creating"
          :disabled="!form.name"
          data-testid="form-submit"
          @click="onSubmit"
        >
          {{ creating ? '创建中…' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- T8.2 内容上传（创建者专属）：zip 选择即传（进度条）；md 选择即传 -->
    <el-dialog :model-value="uploadFor !== null" :title="uploadTitle" width="560px" data-testid="upload-dialog" @update:model-value="uploadFor = null">
      <div class="upload-zone">
        <h3>原型（zip）</h3>
        <p class="tip">≤100MB；zip 根顶层无 html 时自动进入唯一子目录一层（如 dist/ 打包）</p>
        <input
          ref="protoInput"
          type="file"
          accept=".zip"
          class="hidden-input"
          data-testid="proto-file"
          @change="onProtoPicked"
        />
        <el-progress
          v-if="protoUploading || protoPercent > 0"
          :percentage="protoPercent"
          data-testid="upload-progress"
          :status="protoPercent === 100 ? 'success' : undefined"
        />
        <el-button
          type="primary"
          :loading="protoUploading"
          data-testid="proto-pick-btn"
          @click="protoInput?.click()"
        >
          {{ protoUploading ? '上传中…' : '选择 zip 并上传' }}
        </el-button>
      </div>
      <div class="upload-zone">
        <h3>PRD（markdown）</h3>
        <p class="tip">≤5MB；上传后替换现有 PRD 文档（唯一一份）</p>
        <input
          ref="prdInput"
          type="file"
          accept=".md"
          class="hidden-input"
          data-testid="prd-file"
          @change="onPrdPicked"
        />
        <el-button data-testid="prd-pick-btn" @click="prdInput?.click()">选择 md 并上传</el-button>
      </div>
    </el-dialog>
  </main>
</template>

<style scoped>
.home {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 20px;
  font-family: system-ui, -apple-system, 'PingFang SC', sans-serif;
}
.bar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}
.bar h1 {
  font-size: 20px;
  margin: 0;
}
.status { color: #888; font-size: 13px; }
.checking { color: #999; }
.ok { color: #2e9e44; font-weight: 600; }
.fail { color: #d33; font-weight: 600; }
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.card {
  background: #fff;
  border: 1px solid #e6e8ec;
  border-radius: 10px;
  padding: 16px 18px;
}
.card h2 { font-size: 15px; margin: 0 0 6px; }
.card .meta { color: #999; font-size: 12px; margin: 0 0 8px; }
.card .err { color: #d33; font-size: 12px; }
.card-actions { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; }
.owner-actions { display: inline-flex; gap: 8px; }
.card .open { color: #3b82f6; text-decoration: none; font-size: 13px; display: inline-block; }
.hint { color: #999; font-size: 13px; }
.upload-zone { margin-bottom: 20px; }
.upload-zone h3 { font-size: 14px; margin: 0 0 4px; }
.upload-zone .tip { color: #999; font-size: 12px; margin: 0 0 10px; }
.upload-zone .el-progress { margin-bottom: 10px; max-width: 360px; }
.hidden-input { display: none; }
</style>
