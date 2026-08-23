<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { requestCode, verifyLogin } from '../auth'
import { ApiError } from '../api'

const route = useRoute()
const router = useRouter()

const email = ref('')
const code = ref('')
const sending = ref(false)
const verifying = ref(false)
const codeSent = ref(false)
const countdown = ref(0)
let timer: number | undefined

async function sendCode() {
  if (sending.value || countdown.value > 0) return
  sending.value = true
  try {
    await requestCode(email.value)
    codeSent.value = true
    ElMessage.success('验证码已发送，请查收邮件')
    countdown.value = 60
    timer = window.setInterval(() => {
      countdown.value--
      if (countdown.value <= 0) window.clearInterval(timer)
    }, 1000)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '发送失败')
  } finally {
    sending.value = false
  }
}

async function submit() {
  if (verifying.value) return
  verifying.value = true
  try {
    await verifyLogin(email.value, code.value)
    ElMessage.success('登录成功')
    const back = typeof route.query.back === 'string' ? route.query.back : '/'
    router.replace(back)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '登录失败')
  } finally {
    verifying.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <div class="login-card" data-testid="login-card">
      <h1>产品方案展示平台</h1>
      <p class="sub">邮箱验证码登录</p>

      <el-form label-position="top" @submit.prevent>
        <el-form-item label="邮箱">
          <el-input
            v-model="email"
            placeholder="请输入邮箱"
            data-testid="login-email"
            :disabled="verifying"
            @keyup.enter="sendCode"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            class="send-btn"
            :loading="sending"
            :disabled="!email.includes('@') || countdown > 0"
            data-testid="login-send"
            @click="sendCode"
          >
            {{ countdown > 0 ? `${countdown}s 后可重发` : codeSent ? '重新发送验证码' : '发送验证码' }}
          </el-button>
        </el-form-item>

        <el-form-item label="验证码">
          <el-input
            v-model="code"
            placeholder="6 位数字"
            maxlength="6"
            data-testid="login-code"
            @keyup.enter="submit"
          />
        </el-form-item>

        <el-button
          class="submit-btn"
          type="primary"
          :loading="verifying"
          :disabled="!codeSent || code.length !== 6"
          data-testid="login-submit"
          @click="submit"
        >
          登 录
        </el-button>
      </el-form>

      <p class="tip">邮箱未开通权限请联系管理员</p>
    </div>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f6f8;
}
.login-card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.06);
  padding: 40px 44px 28px;
  width: 360px;
}
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: #888; font-size: 13px; margin: 0 0 20px; }
.send-btn { width: 100%; }
.submit-btn { width: 100%; margin-top: 4px; }
.tip { color: #bbb; font-size: 12px; text-align: center; margin: 16px 0 0; }
</style>
