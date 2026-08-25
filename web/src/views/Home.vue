<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError } from '../api'
import { createProject, listProjects, type ProjectInfo } from '../projects'

const backendStatus = ref<'checking' | 'ok' | 'fail'>('checking')
const projects = ref<ProjectInfo[]>([])
const loading = ref(false)

// 新建项目对话框（T8.1：只填名称；内容由上传接口补充，T8.2）
const dialogVisible = ref(false)
const creating = ref(false)
const form = reactive({
  name: '',
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
    const p = await createProject({ name: form.name })
    ElMessage.success(`项目「${p.name}」创建成功`)
    dialogVisible.value = false
    form.name = ''
    await refresh()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
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
        新建项目
      </el-button>
    </header>

    <p v-if="loading" class="hint">加载中…</p>

    <section v-else class="cards">
      <!-- demo 项目（fixture 直连，无 DB 记录） -->
      <div class="card demo" data-testid="project-card-demo">
        <h2>演示项目（内置）</h2>
        <p class="meta">demo · 本地 fixture</p>
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
        <p class="meta">{{ p.project_id }} · 创建者 {{ p.creator.name }}</p>
        <div class="card-actions">
          <router-link class="open" :to="`/project/${p.project_id}`" data-testid="open-project">
            打开分屏查看器 →
          </router-link>
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
.card .open { color: #3b82f6; text-decoration: none; font-size: 13px; display: inline-block; }
.card.demo { border-style: dashed; }
.card a { color: #3b82f6; text-decoration: none; margin-right: 12px; font-size: 13px; }
.hint { color: #999; font-size: 13px; }
</style>
