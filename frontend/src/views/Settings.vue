<script setup lang="ts">
import { ref, computed, onMounted, watch, inject, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Link, Warning, CircleCheck, QuestionFilled, Bell } from '@element-plus/icons-vue'
import {
  requestPermission as requestBrowserNotifyPermission,
  isPywebview
} from '@/composables/useBrowserNotification'
import {
  configApi,
  type ProviderInfo,
  type GlobalConfigData,
  type UpdateConfigRequest,
  type UpdateCheckResult,
  hashPassword
} from '@/api/config'
import { useMarketplaceStore } from '@/stores/marketplaceStore'
import {
  llmModels,
  CONTEXT_LENGTH_PRESETS,
  parseContextLength
} from '@/components/FlowEditor/config/types'
const router = useRouter()
const marketplaceStore = useMarketplaceStore()
const loading = ref(true)
const saving = ref(false)
const providers = ref<ProviderInfo[]>([])

const config = ref<GlobalConfigData>({})
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
const currentPassword = ref('')
const hasUsername = computed(() => config.value.has_username ?? false)
const executionNotificationEnabled = ref(true)
const notifyPermission = computed(() => {
  if (isPywebview()) return 'granted'
  if (!('Notification' in window)) return 'unsupported'
  return Notification.permission
})

async function handleRequestNotifyPermission() {
  if (isPywebview()) return
  const granted = await requestBrowserNotifyPermission()
  if (!granted) {
    ElMessage.warning('浏览器通知权限已被拒绝，请在浏览器设置中允许通知')
  }
}

const currentVersion = ref('0.1.0')
const updateInfo = ref<UpdateCheckResult | null>(null)
const updateChecking = ref(false)
const forceUpgradeInfo = inject<Ref<UpdateCheckResult | null>>('forceUpgradeInfo')

function clearAutoLoginData() {
  localStorage.removeItem('auto_login')
  localStorage.removeItem('saved_password_hash')
  localStorage.removeItem('saved_password')
  localStorage.removeItem('saved_username')
}

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

async function loadConfig() {
  try {
    const [configRes, providerRes] = await Promise.all([
      configApi.getConfig(),
      configApi.getProviders()
    ])
    config.value = configRes.data.data || {}
    providers.value = providerRes.data.data || []

    selectedProvider.value = config.value.provider || ''
    model.value = config.value.model || ''
    baseUrl.value = config.value.base_url || ''
    contextLength.value = config.value.context_length || undefined
    embeddingModel.value = config.value.embedding_model || ''
    embeddingBaseUrl.value = config.value.embedding_base_url || ''
    executionNotificationEnabled.value = config.value.execution_notification_enabled ?? true
  } catch {
    // error handled by interceptor
  }
}

onMounted(async () => {
  loading.value = true
  await loadConfig()
  loading.value = false
  marketplaceStore.loadStatus()
  checkForUpdates()
})

function onProviderChange() {
  const provider = currentProvider.value
  if (provider) {
    baseUrl.value = provider.default_base_url
  }
}

async function handleSave() {
  if (
    contextLength.value !== undefined &&
    contextLength.value !== '' &&
    parseContextLength(contextLength.value) === undefined
  ) {
    ElMessage.error('上下文窗口格式无效，请输入数字或带单位（如 32000、32K、1M）')
    return
  }

  saving.value = true
  try {
    const data: UpdateConfigRequest = {
      provider: selectedProvider.value || undefined,
      model: model.value.trim() || undefined,
      base_url: baseUrl.value.trim() || undefined,
      context_length: parseContextLength(contextLength.value),
      embedding_model: embeddingModel.value.trim() || undefined,
      embedding_base_url: embeddingBaseUrl.value.trim() || undefined,
      execution_notification_enabled: executionNotificationEnabled.value
    }
    if (apiKey.value.trim()) {
      data.api_key = apiKey.value.trim()
    }
    if (embeddingApiKey.value.trim()) {
      data.embedding_api_key = embeddingApiKey.value.trim()
    }
    if (loginPassword.value) {
      if (!currentPassword.value) {
        ElMessage.error('请输入当前密码')
        return
      }
      if (loginPassword.value !== loginPasswordConfirm.value) {
        ElMessage.error('两次输入的密码不一致')
        return
      }
      if (!hasUsername.value && !loginUsername.value.trim()) {
        ElMessage.error('请输入用户名')
        return
      }
      data.current_password = await hashPassword(currentPassword.value)
      data.login_password = await hashPassword(loginPassword.value)
      if (loginUsername.value.trim()) {
        data.login_username = loginUsername.value.trim()
      }
    }
    await configApi.updateConfig(data)
    ElMessage.success('配置已保存')
    await loadConfig()
    apiKey.value = ''
    embeddingApiKey.value = ''
    loginPassword.value = ''
    loginPasswordConfirm.value = ''
    loginUsername.value = ''
    currentPassword.value = ''
    if (data.login_password) {
      config.value.has_password = true
      config.value.has_username = true
      clearAutoLoginData()
      ElMessage.success('密码已修改，请重新登录')
      router.replace('/login')
      return
    }
  } catch {
    // handled by interceptor
  } finally {
    saving.value = false
  }
}

async function handleSaveMarketplace() {
  try {
    const result = await marketplaceStore.saveConfig(marketplaceStore.serverUrl)
    if (marketplaceStore.connected) {
      ElMessage.success(result?.msg || '连接成功')
    }
  } catch {
    // 连接失败时 axios 拦截器已弹出具体错误提示（如"注册失败: 用户名已存在"）
  }
}

function handleDisconnectMarketplace() {
  marketplaceStore.disconnect()
  ElMessage.success('已断开连接')
}

async function checkForUpdates(refresh = false): Promise<void> {
  updateChecking.value = true
  try {
    const res = await configApi.checkUpdate(refresh)
    updateInfo.value = res.data.data
    if (updateInfo.value) {
      currentVersion.value = updateInfo.value.current_version
    }
    if (forceUpgradeInfo.value) {
      if (updateInfo.value?.force_upgrade && updateInfo.value.has_update) {
        forceUpgradeInfo.value = updateInfo.value
      } else {
        forceUpgradeInfo.value = null
      }
    }
  } catch {
    // handled by interceptor
  } finally {
    updateChecking.value = false
  }
}

function openDownloadUrl(): void {
  if (updateInfo.value?.download_url) {
    window.open(updateInfo.value.download_url, '_blank')
  }
}
</script>

<template>
  <div v-loading="loading" class="settings-page">
    <div class="settings-header">
      <h2>系统设置</h2>
      <p>配置 AI 模型和全局参数。此配置为全局默认值，在以下场景中使用：</p>
      <ul class="usage-list">
        <li>
          <b>内置 AI 助手</b>
          — 对话使用此配置调用 LLM，更新时自动同步
        </li>
        <li>
          <b>新建流程/智能体 LLM 节点</b>
          — 拖入 LLM 节点时自动填充供应商、模型和 Base URL；API Key 留空则自动注入全局值
        </li>
        <li>
          <b>AI 创建节点</b>
          — AI 助手创建智能体时，LLM 节点未指定的配置自动从此处继承
        </li>
      </ul>
    </div>

    <div class="settings-content">
      <el-card shadow="never" class="settings-card">
        <template #header>
          <span class="card-title">AI 模型配置</span>
        </template>

        <el-form label-position="top">
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
              :placeholder="
                config.api_key_masked ? `当前: ${config.api_key_masked}` : '请输入 API Key'
              "
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
            <el-input v-model="baseUrl" placeholder="API 地址" clearable />
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

          <el-button type="primary" :loading="saving" @click="handleSave">保存配置</el-button>
        </el-form>
      </el-card>

      <el-card shadow="never" class="settings-card" style="margin-top: 16px">
        <template #header>
          <span class="card-title">向量模型配置</span>
        </template>

        <el-alert
          title="如不配置，记忆和知识库的向量检索功能将不可用"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 16px"
        />

        <el-form label-position="top">
          <el-form-item label="API Key">
            <el-input
              v-model="embeddingApiKey"
              type="password"
              :placeholder="
                config.embedding_api_key_masked
                  ? `当前: ${config.embedding_api_key_masked}`
                  : '请输入向量模型 API Key'
              "
              show-password
              clearable
            />
          </el-form-item>

          <el-form-item label="模型名称">
            <el-input v-model="embeddingModel" placeholder="如 BAAI/bge-m3" clearable />
          </el-form-item>

          <el-form-item label="Base URL">
            <el-input
              v-model="embeddingBaseUrl"
              placeholder="如 https://api.siliconflow.cn/v1"
              clearable
            />
          </el-form-item>

          <el-button type="primary" :loading="saving" @click="handleSave">保存配置</el-button>
        </el-form>
      </el-card>

      <el-card shadow="never" class="settings-card" style="margin-top: 16px">
        <template #header>
          <span class="card-title">登录安全</span>
        </template>

        <div>
          <el-alert
            title="登录保护已启用"
            type="success"
            :closable="false"
            show-icon
            style="margin-bottom: 16px"
          />
          <el-form label-position="top">
            <el-form-item label="用户名">
              <el-input
                v-model="loginUsername"
                :placeholder="
                  config.username
                    ? `当前: ${config.username}，输入新值可修改`
                    : hasUsername
                      ? '当前已设置，输入新值可修改'
                      : '请输入用户名'
                "
                clearable
              />
            </el-form-item>
            <el-form-item label="当前密码">
              <el-input
                v-model="currentPassword"
                type="password"
                placeholder="请输入当前密码"
                show-password
                clearable
              />
            </el-form-item>
            <el-form-item label="新密码">
              <el-input
                v-model="loginPassword"
                type="password"
                placeholder="请输入新密码"
                show-password
                clearable
              />
            </el-form-item>
            <el-form-item v-if="loginPassword" label="确认新密码">
              <el-input
                v-model="loginPasswordConfirm"
                type="password"
                placeholder="请再次输入新密码"
                show-password
                clearable
              />
            </el-form-item>
            <el-button
              type="primary"
              :disabled="
                !currentPassword ||
                !loginPassword ||
                loginPassword !== loginPasswordConfirm ||
                (!hasUsername && !loginUsername.trim())
              "
              :loading="saving"
              @click="handleSave"
            >
              修改
            </el-button>
          </el-form>
        </div>
      </el-card>

      <el-card shadow="never" class="settings-card" style="margin-top: 16px">
        <template #header>
          <div class="card-title" style="display: flex; align-items: center; gap: 8px">
            <el-icon><Bell /></el-icon>
            <span>通知设置</span>
          </div>
        </template>
        <el-form label-position="top">
          <el-form-item>
            <div
              style="
                display: flex;
                align-items: center;
                justify-content: space-between;
                width: 100%;
              "
            >
              <div>
                <div style="font-size: 14px; font-weight: 500">执行完成通知</div>
                <div style="font-size: 12px; color: #64748b; margin-top: 4px">
                  流程或智能体执行完成时，通过 WebSocket 推送桌面通知（右上角弹窗）
                </div>
              </div>
              <el-switch v-model="executionNotificationEnabled" />
            </div>
          </el-form-item>
          <el-form-item>
            <div
              style="
                display: flex;
                align-items: center;
                justify-content: space-between;
                width: 100%;
              "
            >
              <div>
                <div style="font-size: 14px; font-weight: 500">浏览器桌面通知</div>
                <div style="font-size: 12px; color: #64748b; margin-top: 4px">
                  流程/对话完成或日程提醒时，弹出系统级桌面通知（浏览器后台也能收到）
                </div>
              </div>
              <el-button size="small" @click="handleRequestNotifyPermission">
                {{ notifyPermission === 'granted' ? '已授权' : '请求通知权限' }}
              </el-button>
            </div>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card shadow="never" class="settings-card" style="margin-top: 16px">
        <template #header>
          <div class="card-title" style="display: flex; align-items: center; gap: 8px">
            <el-icon><Link /></el-icon>
            <span>资源市场</span>
            <el-tag
              v-if="marketplaceStore.connected"
              type="success"
              size="small"
              style="margin-left: auto"
            >
              已连接
            </el-tag>
            <el-tag v-else type="info" size="small" style="margin-left: auto">未连接</el-tag>
          </div>
        </template>
        <el-form label-position="top">
          <el-form-item label="服务器地址">
            <el-input
              v-model="marketplaceStore.serverUrl"
              placeholder="例如: https://market.example.com"
              clearable
            />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              :loading="marketplaceStore.loading"
              :disabled="!marketplaceStore.serverUrl.trim()"
              @click="handleSaveMarketplace"
            >
              保存并连接
            </el-button>
            <el-button v-if="marketplaceStore.connected" @click="handleDisconnectMarketplace">
              断开连接
            </el-button>
          </el-form-item>
        </el-form>
        <div v-if="marketplaceStore.connected" class="marketplace-tip">
          已连接，前往
          <el-link type="primary" underline="never" @click="router.push('/marketplace')">
            资源市场
          </el-link>
          浏览和导入资源
        </div>
      </el-card>

      <el-card shadow="never" class="settings-card" style="margin-top: 16px">
        <template #header>
          <div
            class="card-title"
            style="display: flex; align-items: center; justify-content: space-between"
          >
            <span>版本更新</span>
            <el-tag size="small" type="info">v{{ currentVersion }}</el-tag>
          </div>
        </template>

        <div class="update-actions">
          <el-button type="primary" plain :loading="updateChecking" @click="checkForUpdates(true)">
            检查更新
          </el-button>
        </div>

        <template v-if="updateInfo">
          <template v-if="updateInfo.has_update">
            <div
              class="update-notice"
              :class="{ 'update-notice--force': updateInfo.force_upgrade }"
            >
              <div class="update-notice-icon">
                <el-icon :size="20"><Warning /></el-icon>
              </div>
              <div class="update-notice-body">
                <div class="update-notice-title">
                  {{ updateInfo.force_upgrade ? '需要强制升级' : '发现新版本' }}
                  <span class="update-version-tag">v{{ updateInfo.latest_version }}</span>
                </div>
                <div v-if="updateInfo.release_notes" class="update-notice-desc">
                  {{ updateInfo.release_notes }}
                </div>
              </div>
            </div>
            <div class="update-download-row">
              <span v-if="updateInfo.published_at" class="update-pub-time">
                发布于 {{ updateInfo.published_at }}
              </span>
              <el-button type="primary" @click="openDownloadUrl">前往下载</el-button>
            </div>
          </template>
          <div v-else class="update-up-to-date">
            <el-icon :size="16"><CircleCheck /></el-icon>
            <span>已是最新版本</span>
          </div>
        </template>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.marketplace-tip {
  margin-top: 12px;
  font-size: 14px;
  color: #64748b;
  line-height: 1.5;
}

.marketplace-tip :deep(.el-link) {
  display: inline;
  vertical-align: baseline;
}

.settings-page {
  padding: 24px;
  flex: 1;
  overflow-y: auto;
}

.settings-header {
  margin-bottom: 24px;
}

.settings-header h2 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
}

.settings-header p {
  margin: 0;
  font-size: 14px;
  color: #64748b;
}

.usage-list {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: 13px;
  color: #64748b;
  line-height: 1.8;
}

.settings-card :deep(.el-card__header) {
  padding: 16px 20px;
  border-bottom: 1px solid #f1f5f9;
}

.card-title {
  font-weight: 600;
  color: #1e293b;
  font-size: 15px;
}

.full-width {
  width: 100%;
}

.update-actions {
  margin-bottom: 4px;
}

.update-notice {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  margin-top: 8px;
  border-radius: 8px;
  background: #fffbeb;
  border: 1px solid #fde68a;
}

.update-notice--force {
  background: #fef2f2;
  border-color: #fca5a5;
}

.update-notice-icon {
  flex-shrink: 0;
  margin-top: 1px;
  color: #f59e0b;
}

.update-notice--force .update-notice-icon {
  color: #ef4444;
}

.update-notice-body {
  flex: 1;
  min-width: 0;
}

.update-notice-title {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  display: flex;
  align-items: center;
  gap: 8px;
}

.update-version-tag {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.06);
  color: #64748b;
  font-weight: 500;
}

.update-notice-desc {
  margin-top: 6px;
  font-size: 13px;
  color: #64748b;
  line-height: 1.6;
  white-space: pre-line;
}

.update-download-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
}

.update-pub-time {
  font-size: 12px;
  color: #94a3b8;
}

.update-up-to-date {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  padding: 10px 16px;
  border-radius: 8px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  font-size: 14px;
  color: #16a34a;
  font-weight: 500;
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
