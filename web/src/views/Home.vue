<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError } from '../api'
import { createProject, listProjects, type ProjectInfo } from '../projects'

const backendStatus = ref<'checking' | 'ok' | 'fail'>('checking')
const projects = ref<ProjectInfo[]>([])
const loading = ref(false)

// 新建项目对话框
const dialogVisible = ref(false)
const creating = ref(false)
const form = reactive({
  name: '',
  repo_url: '',
  token: '',
  branch: 'main',
})

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
    const p = await createProject({ ...form })
    ElMessage.success(`项目「${p.name}」绑定成功`)
    dialogVisible.value = false
    form.name = ''
    form.repo_url = ''
    form.token = ''
    form.branch = 'main'
    await refresh()
  } catch (e) {
    // clone 失败的后端中文提示（认证失败/仓库不存在/网络）
    ElMessage.error(e instanceof Error ? e.message : '绑定失败')
  } finally {
    creating.value = false
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
        绑定新仓库
      </el-button>
    </header>

    <p v-if="loading" class="hint">加载中…</p>

    <section v-else class="cards">
      <!-- demo 项目（fixture 直连，无 DB 记录） -->
      <div class="card demo" data-testid="project-card-demo">
        <h2>演示项目（内置）</h2>
        <p class="meta">demo · main · 本地 fixture</p>
        <p>
          <router-link to="/demo/bridge">T1.1 沙箱桥接 →</router-link>
          <router-link to="/demo/shot">T1.2 截图链路 →</router-link>
        </p>
      </div>

      <div
        v-for="p in projects"
        :key="p.id"
        class="card"
        :data-testid="`project-card-${p.project_id}`"
      >
        <h2>{{ p.name }}</h2>
        <p class="meta">{{ p.project_id }} · {{ p.branch }}</p>
        <p class="repo" :title="p.repo_url">{{ p.repo_url }}</p>
        <p v-if="p.sync_error" class="err">同步异常：{{ p.sync_error }}</p>
        <router-link class="open" :to="`/project/${p.project_id}`" data-testid="open-project">
          打开分屏查看器 →
        </router-link>
      </div>

      <p v-if="projects.length === 0" class="hint">
        还没有绑定的项目，点右上角「绑定新仓库」开始
      </p>
    </section>

    <el-dialog v-model="dialogVisible" title="绑定新仓库" width="520px">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="项目名" required>
          <el-input v-model="form.name" placeholder="如：CRM 改版" data-testid="form-name" maxlength="50" />
        </el-form-item>
        <el-form-item label="仓库地址" required>
          <el-input v-model="form.repo_url" placeholder="https://gitlab.example.com/grp/repo.git" data-testid="form-repo-url" />
        </el-form-item>
        <el-form-item label="Access Token" required>
          <el-input
            v-model="form.token"
            type="password"
            show-password
            placeholder="GitLab project access token（加密存储）"
            data-testid="form-token"
          />
        </el-form-item>
        <el-form-item label="分支">
          <el-input v-model="form.branch" placeholder="main" data-testid="form-branch" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="creating"
          :disabled="!form.name || !form.repo_url || !form.token"
          data-testid="form-submit"
          @click="onSubmit"
        >
          {{ creating ? '克隆中…' : '绑定并克隆' }}
        </el-button>
      </template>
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
.card .repo {
  color: #666;
  font-size: 12px;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card .err { color: #d33; font-size: 12px; }
.card .open { color: #3b82f6; text-decoration: none; font-size: 13px; display: inline-block; margin-top: 8px; }
.card.demo { border-style: dashed; }
.card a { color: #3b82f6; text-decoration: none; margin-right: 12px; font-size: 13px; }
.hint { color: #999; font-size: 13px; }
</style>
