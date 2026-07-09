<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { configApi, type ProviderInfo, type InitConfigRequest, hashPassword } from '@/api/config'
import {
  llmModels,
  CONTEXT_LENGTH_PRESETS,
  parseContextLength
} from '@/components/FlowEditor/config/types'

const router = useRouter()

const loading = ref(false)
const submitting = ref(false)
const providers = ref<ProviderInfo[]>([])

const selectedProvider = ref('')
const apiKey = ref('')
const model = ref('')
const baseUrl = ref('')
const contextLength = ref<number | undefined>(undefined)

const embeddingApiKey = ref('')
const embeddingModel = ref('')
const embeddingBaseUrl = ref('')

const loginPassword = ref('')
const loginPasswordConfirm = ref('')
const loginUsername = ref('')

const currentProvider = computed(() => {
  return providers.value.find(p => p.name === selectedProvider.value)
})

const modelOptions = computed(() => {
  return llmModels[selectedProvider.value] || []
})

watch(model, val => {
  const found = modelOptions.value.find(m => m.value === val)
  if (found?.context_length) {
    contextLength.value = found.context_length
  } else {
    contextLength.value = undefined
  }
})

const canSubmit = computed(() => {
  if (
    !(selectedProvider.value && apiKey.value.trim() && model.value.trim() && baseUrl.value.trim())
  ) {
    return false
  }
  if (!loginPassword.value || !loginUsername.value.trim()) return false
  if (loginPassword.value !== loginPasswordConfirm.value) return false
  return true
})

onMounted(async () => {
  loading.value = true
  try {
    const res = await configApi.getProviders()
    providers.value = res.data.data || []
    if (providers.value.length > 0) {
      selectedProvider.value = providers.value[0].name
    }
  } catch {
    // error handled by interceptor
  } finally {
    loading.value = false
  }
})

function onProviderChange() {
  const provider = currentProvider.value
  if (provider) {
    baseUrl.value = provider.default_base_url
  }
  model.value = ''
  contextLength.value = undefined
}

async function handleSubmit() {
  if (!canSubmit.value) return

  if (
    contextLength.value !== undefined &&
    contextLength.value !== '' &&
    parseContextLength(contextLength.value) === undefined
  ) {
    ElMessage.error('上下文窗口格式无效，请输入数字或带单位（如 32000、32K、1M）')
    return
  }

  submitting.value = true
  try {
    localStorage.removeItem('auto_login')
    localStorage.removeItem('saved_username')
    localStorage.removeItem('saved_password_hash')
    const data: InitConfigRequest = {
      provider: selectedProvider.value,
      api_key: apiKey.value.trim(),
      model: model.value.trim(),
      base_url: baseUrl.value.trim() || undefined,
      context_length: parseContextLength(contextLength.value),
      embedding_api_key: embeddingApiKey.value.trim() || undefined,
      embedding_base_url: embeddingBaseUrl.value.trim() || undefined,
      embedding_model: embeddingModel.value.trim() || undefined,
      login_password: await hashPassword(loginPassword.value),
      login_username: loginUsername.value.trim()
    }
    await configApi.initConfig(data)
    ElMessage.success('配置成功')
    router.replace('/chat')
  } catch {
    // error already handled by interceptor
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
          <img src="/logo.ico" alt="logo" />
        </div>
        <h1 class="setup-title">欢迎使用 AI Agent OS</h1>
        <p class="setup-subtitle">请先配置 AI 模型，即可开始使用</p>
      </div>

      <div v-loading="loading" class="setup-form">
        <el-form label-position="top" @submit.prevent="handleSubmit">
          <el-form-item label="AI 供应商">
            <el-select
              v-model="selectedProvider"
              placeholder="请选择供应商"
              class="full-width"
              @change="onProviderChange"
            >
              <el-option v-for="p in providers" :key="p.name" :label="p.label" :value="p.name" />
            </el-select>
          </el-form-item>

          <el-form-item label="API Key">
            <el-input
              v-model="apiKey"
              type="password"
              placeholder="请输入 API Key"
              show-password
              clearable
            />
          </el-form-item>

          <el-form-item label="模型">
            <el-select
              v-if="modelOptions.length > 0"
              v-model="model"
              placeholder="请选择模型"
              class="full-width"
              filterable
              clearable
            >
              <el-option
                v-for="m in modelOptions"
                :key="m.value"
                :label="m.label"
                :value="m.value"
              />
            </el-select>
            <el-input v-else v-model="model" placeholder="请输入模型名称" clearable />
          </el-form-item>

          <el-form-item label="Base URL">
            <el-input
              v-model="baseUrl"
              placeholder="API 地址（自动从供应商获取，可自定义）"
              clearable
            />
          </el-form-item>

          <el-form-item>
            <template #label>
              上下文窗口
              <el-tooltip
                content="用于上下文自动压缩，当对话占用超过 80% 时自动压缩旧消息。不填则不会自动压缩。"
              >
                <el-icon class="context-tip-icon"><QuestionFilled /></el-icon>
              </el-tooltip>
            </template>
            <el-select
              v-model="contextLength"
              placeholder="选择或输入上下文大小"
              style="width: calc(100% - 80px)"
              filterable
              allow-create
              default-first-option
              clearable
            >
              <el-option
                v-for="item in CONTEXT_LENGTH_PRESETS"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
            <span class="context-length-unit" style="margin-left: 8px">tokens</span>
          </el-form-item>

          <el-divider />
          <p class="section-label">向量模型配置（可选）</p>

          <el-alert
            title="如不配置，记忆和知识库的向量检索功能将不可用"
            type="warning"
            :closable="false"
            show-icon
            style="margin-bottom: 16px"
          />

          <el-form-item label="API Key">
            <el-input
              v-model="embeddingApiKey"
              type="password"
              placeholder="请输入向量模型 API Key"
              show-password
              clearable
            />
          </el-form-item>

          <el-form-item label="模型名称">
            <el-input
              v-model="embeddingModel"
              placeholder="如 BAAI/bge-m3、text-embedding-v3"
              clearable
            />
          </el-form-item>

          <el-form-item label="Base URL">
            <el-input
              v-model="embeddingBaseUrl"
              placeholder="如 https://api.siliconflow.cn/v1"
              clearable
            />
          </el-form-item>

          <el-divider />
          <p class="section-label">登录安全</p>

          <el-alert
            title="请设置登录用户名和密码，用于保护系统访问。"
            type="info"
            :closable="false"
            show-icon
            style="margin-bottom: 16px"
          />

          <el-form-item label="用户名">
            <el-input v-model="loginUsername" placeholder="请输入登录用户名" clearable />
          </el-form-item>

          <el-form-item label="登录密码">
            <el-input
              v-model="loginPassword"
              type="password"
              placeholder="请输入登录密码"
              show-password
              clearable
            />
          </el-form-item>

          <el-form-item label="确认密码">
            <el-input
              v-model="loginPasswordConfirm"
              type="password"
              placeholder="请再次输入密码"
              show-password
              clearable
            />
          </el-form-item>

          <el-button
            type="primary"
            size="large"
            class="full-width submit-btn"
            :loading="submitting"
            :disabled="!canSubmit"
            @click="handleSubmit"
          >
            开始使用
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
  max-width: 460px;
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

.section-label {
  font-size: 14px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 12px;
}

.submit-btn {
  margin-top: 8px;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  border-radius: 10px;
}
.context-length-display {
  display: flex;
  align-items: center;
  gap: 6px;
}
.context-length-unit {
  font-size: 12px;
  color: #909399;
}
.context-tip-icon {
  margin-left: 4px;
  cursor: help;
  color: #909399;
}
</style>
