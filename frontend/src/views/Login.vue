<script setup lang="ts">
import { ref, onMounted, nextTick, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { authApi } from '@/api/auth'
import { sha256 } from '@/utils/crypto'

const AUTO_LOGIN_KEY = 'auto_login'
const SAVED_USERNAME_KEY = 'saved_username'
const SAVED_PASSWORD_HASH_KEY = 'saved_password_hash'

const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const rememberMe = ref(false)
const submitting = ref(false)
const autoLogging = ref(false)
const canSubmit = computed(() => {
  return !!username.value.trim() && !!password.value.trim()
})

function saveAutoLogin(user: string, passwordHash: string) {
  localStorage.setItem(AUTO_LOGIN_KEY, '1')
  localStorage.setItem(SAVED_USERNAME_KEY, user)
  localStorage.setItem(SAVED_PASSWORD_HASH_KEY, passwordHash)
}

function clearAutoLogin() {
  localStorage.removeItem(AUTO_LOGIN_KEY)
  localStorage.removeItem(SAVED_USERNAME_KEY)
  localStorage.removeItem(SAVED_PASSWORD_HASH_KEY)
}

function getSavedUsername(): string | null {
  return localStorage.getItem(SAVED_USERNAME_KEY)
}

function getSavedPasswordHash(): string | null {
  return localStorage.getItem(SAVED_PASSWORD_HASH_KEY)
}

onMounted(async () => {
  try {
    await authApi.check()
  } catch {
    // ignore
  }

  const isAutoLogin = localStorage.getItem(AUTO_LOGIN_KEY) === '1'
  if (!isAutoLogin) return

  const savedHash = getSavedPasswordHash()
  if (!savedHash) return

  const savedUser = getSavedUsername() || ''
  if (!savedUser) return

  rememberMe.value = true
  username.value = savedUser
  autoLogging.value = true

  await nextTick()
  try {
    await authApi.login(savedUser, savedHash, { showError: false })
    const redirect = (route.query.redirect as string) || '/'
    router.replace(redirect)
  } catch {
    ElMessage.error('自动登录失败，请手动输入')
    clearAutoLogin()
    autoLogging.value = false
  }
})

async function handleLogin() {
  if (!canSubmit.value) return

  submitting.value = true
  try {
    const hash = await sha256(password.value.trim())
    await authApi.login(username.value.trim(), hash)

    if (rememberMe.value) {
      saveAutoLogin(username.value.trim(), hash)
    } else {
      clearAutoLogin()
    }

    ElMessage.success('登录成功')
    const redirect = (route.query.redirect as string) || '/'
    router.replace(redirect)
  } catch {
    password.value = ''
    rememberMe.value = false
    clearAutoLogin()
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="setup-container">
    <div class="setup-card">
      <div class="setup-header">
        <div class="setup-logo">
          <img src="/logo_256x256.ico" alt="logo" />
        </div>
        <h1 class="setup-title">AI Agent OS</h1>
        <p class="setup-subtitle">{{ autoLogging ? '正在自动登录...' : '请输入登录信息' }}</p>
      </div>

      <div class="setup-form">
        <el-form label-position="top" @submit.prevent="handleLogin">
          <el-form-item label="用户名">
            <el-input
              v-model="username"
              placeholder="请输入用户名"
              clearable
              :disabled="autoLogging"
            />
          </el-form-item>

          <el-form-item label="登录密码">
            <el-input
              v-model="password"
              type="password"
              placeholder="请输入登录密码"
              show-password
              clearable
              :disabled="autoLogging"
            />
          </el-form-item>

          <el-form-item>
            <el-checkbox v-model="rememberMe" :disabled="autoLogging">记住登录信息</el-checkbox>
          </el-form-item>

          <el-button
            type="primary"
            size="large"
            class="full-width submit-btn"
            :loading="submitting || autoLogging"
            :disabled="!canSubmit"
            @click="handleLogin"
          >
            登录
          </el-button>
        </el-form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.setup-container {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #334155 100%);
  padding: 24px 20px;
  overflow-y: auto;
}

.setup-card {
  width: 100%;
  max-width: 400px;
  background: #fff;
  border-radius: 16px;
  padding: 48px 40px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
}

.setup-header {
  text-align: center;
  margin-bottom: 36px;
}

.setup-logo {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  border-radius: 14px;
  overflow: hidden;
  background: #f1f5f9;
  display: flex;
  align-items: center;
  justify-content: center;
}

.setup-logo img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.setup-title {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 8px;
}

.setup-subtitle {
  font-size: 14px;
  color: #64748b;
  margin: 0;
}

.setup-form :deep(.el-form-item__label) {
  font-weight: 600;
  color: #334155;
  font-size: 13px;
}

.full-width {
  width: 100%;
}

.submit-btn {
  margin-top: 8px;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  border-radius: 10px;
}
</style>
