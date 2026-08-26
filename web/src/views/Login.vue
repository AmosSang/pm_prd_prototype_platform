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
    <div class="login-deco login-deco-1" aria-hidden="true" />
    <div class="login-deco login-deco-2" aria-hidden="true" />
    <div class="login-card" data-testid="login-card">
      <div class="brand-row">
        <span class="brand-dot" aria-hidden="true" />
        <h1>产品方案展示平台</h1>
      </div>
      <p class="sub">邮箱验证码登录</p>

      <el-form label-position="top" @submit.prevent>
        <el-form-item label="邮箱">
          <el-input
            v-model="email"
            placeholder="请输入邮箱"
            size="large"
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
            size="large"
            maxlength="6"
            data-testid="login-code"
            @keyup.enter="submit"
          />
        </el-form-item>

        <el-button
          class="submit-btn"
          type="primary"
          size="large"
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
  position: relative;
  overflow: hidden;
  /* 微暖纸白底 + 顶部极浅品牌色渐变，营造干净氛围而不喧宾夺主 */
  background:
    linear-gradient(180deg, #eef1fc 0%, var(--pp-bg) 42%, var(--pp-bg) 100%);
}
/* 两个大的低饱和装饰光斑（品牌色系），给纯色底一点纵深 */
.login-deco {
  position: absolute;
  border-radius: 50%;
  filter: blur(72px);
  opacity: 0.5;
  pointer-events: none;
}
.login-deco-1 {
  width: 420px;
  height: 420px;
  background: #dce2f7;
  top: -120px;
  right: -80px;
}
.login-deco-2 {
  width: 360px;
  height: 360px;
  background: #e7ecf9;
  bottom: -140px;
  left: -100px;
}
.login-card {
  position: relative;
  z-index: 1;
  background: var(--pp-surface);
  border: 1px solid var(--pp-border);
  border-radius: 16px;
  box-shadow: var(--pp-shadow-lg);
  padding: 40px 44px 28px;
  width: 380px;
}
.brand-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}
.brand-row h1 { font-size: 20px; margin: 0; }
.brand-dot {
  width: 14px;
  height: 14px;
  border-radius: 5px;
  background: linear-gradient(135deg, #4f63d2 0%, #7d8de0 100%);
  box-shadow: 0 2px 6px rgba(79, 99, 210, 0.4);
  flex-shrink: 0;
}
.sub { color: var(--pp-text-3); font-size: 13px; margin: 0 0 22px; }
.send-btn { width: 100%; }
.submit-btn { width: 100%; margin-top: 4px; letter-spacing: 4px; }
.tip { color: var(--pp-text-4); font-size: 12px; text-align: center; margin: 18px 0 0; }

/* 表单标签层级：更清晰的主次 */
.login-card :deep(.el-form-item__label) {
  color: var(--pp-text-2);
  font-weight: 500;
  font-size: 13px;
}
</style>
