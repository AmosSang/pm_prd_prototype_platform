<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createUser,
  listUsers,
  renameUser,
  setUserStatus,
  currentUser,
  type ManagedUser,
} from '../auth'

const users = ref<ManagedUser[]>([])
const loading = ref(false)

// 新建用户对话框
const createVisible = ref(false)
const creating = ref(false)
const createForm = reactive({ email: '', name: '' })

// 改名对话框
const renameVisible = ref(false)
const renameTarget = ref<ManagedUser | null>(null)
const renaming = ref(false)
const renameForm = reactive({ name: '' })

async function refresh() {
  loading.value = true
  try {
    users.value = await listUsers()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '用户列表加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.email = ''
  createForm.name = ''
  createVisible.value = true
}

async function onSubmitCreate() {
  if (creating.value) return
  creating.value = true
  try {
    const u = await createUser(createForm.email.trim(), createForm.name.trim())
    ElMessage.success(`用户「${u.name}」创建成功`)
    createVisible.value = false
    await refresh()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  } finally {
    creating.value = false
  }
}

function openRename(u: ManagedUser) {
  renameTarget.value = u
  renameForm.name = u.name
  renameVisible.value = true
}

async function onSubmitRename() {
  if (!renameTarget.value || renaming.value) return
  renaming.value = true
  try {
    const u = await renameUser(renameTarget.value.id, renameForm.name.trim())
    ElMessage.success(`已更新「${u.name}」`)
    // 改的是自己 → 同步内存里的 currentUser（顶栏即时刷新）
    if (currentUser.value && currentUser.value.id === u.id) {
      currentUser.value.name = u.name
    }
    renameVisible.value = false
    renameTarget.value = null
    await refresh()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '修改失败')
  } finally {
    renaming.value = false
  }
}

async function onToggleStatus(u: ManagedUser) {
  const toDisable = !u.disabled
  try {
    await ElMessageBox.confirm(
      toDisable
        ? `停用「${u.name}」（${u.email}）后：该邮箱无法再获取验证码；若已在登录态，下一次任一操作会被强制退出。`
        : `启用「${u.name}」（${u.email}）？`,
      toDisable ? '停用账号' : '启用账号',
      { type: 'warning', confirmButtonText: toDisable ? '停用' : '启用', cancelButtonText: '取消' },
    )
  } catch {
    return // 取消
  }
  try {
    const updated = await setUserStatus(u.id, toDisable)
    ElMessage.success(toDisable ? `「${updated.name}」已停用` : `「${updated.name}」已启用`)
    await refresh()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '操作失败')
  }
}

onMounted(refresh)
</script>

<template>
  <main class="user-manage">
    <header class="bar">
      <h1>用户管理</h1>
      <el-button type="primary" data-testid="user-create-open" @click="openCreate">
        新建用户
      </el-button>
    </header>

    <p v-if="loading" class="hint">加载中…</p>

    <el-table v-else :data="users" class="user-table" data-testid="user-table" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="姓名" min-width="120">
        <template #default="{ row }">{{ row.name }}</template>
      </el-table-column>
      <el-table-column prop="email" label="邮箱" min-width="220" />
      <el-table-column label="角色" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.is_admin" type="warning" size="small">超级管理员</el-tag>
          <el-tag v-else type="info" size="small">普通用户</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.disabled ? 'danger' : 'success'" size="small">
            {{ row.disabled ? '停用' : '启用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ row.created_at.slice(0, 10) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" data-testid="user-rename" @click="openRename(row)">
            改名
          </el-button>
          <el-button
            v-if="!row.is_admin"
            size="small"
            :type="row.disabled ? 'success' : 'danger'"
            plain
            :data-testid="row.disabled ? 'user-enable' : 'user-disable'"
            @click="onToggleStatus(row)"
          >
            {{ row.disabled ? '启用' : '停用' }}
          </el-button>
          <el-tooltip v-else content="不能停用超级管理员" placement="top">
            <el-button size="small" type="danger" plain disabled>停用</el-button>
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createVisible" title="新建用户" width="440px" data-testid="user-create-dialog">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="邮箱" required>
          <el-input v-model="createForm.email" placeholder="请输入登录邮箱" data-testid="user-create-email" />
        </el-form-item>
        <el-form-item label="姓名" required>
          <el-input v-model="createForm.name" placeholder="如：张三" data-testid="user-create-name" maxlength="50" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="creating"
          :disabled="!createForm.email.trim().includes('@') || !createForm.name.trim()"
          data-testid="user-create-submit"
          @click="onSubmitCreate"
        >
          {{ creating ? '创建中…' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="renameVisible" title="修改姓名" width="440px" data-testid="user-rename-dialog">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="姓名" required>
          <el-input
            v-model="renameForm.name"
            placeholder="请输入姓名"
            data-testid="user-rename-name"
            maxlength="50"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renameVisible = false; renameTarget = null">取消</el-button>
        <el-button
          type="primary"
          :loading="renaming"
          :disabled="!renameForm.name.trim()"
          data-testid="user-rename-submit"
          @click="onSubmitRename"
        >
          {{ renaming ? '保存中…' : '保存' }}
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.user-manage {
  max-width: 960px;
  margin: 0 auto;
  padding: 28px 20px 48px;
  font-family: var(--pp-font);
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 22px;
}
.bar h1 { font-size: 22px; margin: 0; font-weight: 600; letter-spacing: 0.2px; }
.hint { color: var(--pp-text-3); font-size: 13px; }
</style>
