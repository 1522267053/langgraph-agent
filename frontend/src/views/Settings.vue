<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Link, Warning, CircleCheck } from '@element-plus/icons-vue'
import { requestPermission as requestBrowserNotifyPermission } from '@/composables/useBrowserNotification'
import {
  configApi,
  updateApi,
  type GlobalConfigData,
  type UpdateConfigRequest,
  type UpdateStatus,
  hashPassword
} from '@/api/config'
import { useMarketplaceStore } from '@/stores/marketplaceStore'
import { parseContextLength } from '@/components/FlowEditor/config/types'
import AiProviderConfig from '@/components/common/AiProviderConfig.vue'
const router = useRouter()
const route = useRoute()
const marketplaceStore = useMarketplaceStore()
const loading = ref(true)
const saving = ref(false)
const activeTab = ref((route.query.tab as string) || 'llm')

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
  if (!('Notification' in window)) return 'unsupported'
  return Notification.permission
})

async function handleRequestNotifyPermission() {
  const granted = await requestBrowserNotifyPermission()
  if (!granted) {
    ElMessage.warning('浏览器通知权限已被拒绝，请在浏览器设置中允许通知')
  }
}

const currentVersion = ref('0.1.0')
const updateStatus = ref<UpdateStatus | null>(null)
const updateChecking = ref(false)
let statusTimer: ReturnType<typeof setInterval> | null = null

const hasUpdate = computed(() => {
  const s = updateStatus.value
  if (!s) return false
  if (s.has_update) return true
  return ['downloading', 'ready', 'applying', 'failed'].includes(s.state)
})

function clearAutoLoginData() {
  localStorage.removeItem('auto_login')
  localStorage.removeItem('saved_password_hash')
  localStorage.removeItem('saved_password')
  localStorage.removeItem('saved_username')
}

async function loadConfig() {
  try {
    const configRes = await configApi.getConfig()
    config.value = configRes.data.data || {}

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
  await loadUpdateStatus()
  loading.value = false
  marketplaceStore.loadStatus()
  checkForUpdates()
})

onUnmounted(() => {
  if (statusTimer) clearInterval(statusTimer)
})

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
      // 空串显式发送，用于清空 base_url（虚拟供应商）；真实供应商后端会回退官方默认值
      base_url: baseUrl.value.trim(),
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

async function loadUpdateStatus(): Promise<void> {
  try {
    const res = await updateApi.getStatus()
    updateStatus.value = res.data.data
    if (updateStatus.value) {
      currentVersion.value = updateStatus.value.current_version
      if (updateStatus.value.last_result) {
        updateApi.ack().catch(() => {})
      }
      if (updateStatus.value.state === 'downloading' || updateStatus.value.state === 'applying') {
        startStatusPolling()
      }
    }
  } catch {
    // handled by interceptor
  }
}

async function checkForUpdates(): Promise<void> {
  updateChecking.value = true
  try {
    const res = await updateApi.checkUpdate()
    const info = res.data.data
    if (info) {
      currentVersion.value = info.current_version
    }
    if (info?.has_update && info.force_upgrade && updateStatus.value?.state === 'idle') {
      await triggerDownload()
    } else {
      await loadUpdateStatus()
    }
  } catch {
    // handled by interceptor
  } finally {
    updateChecking.value = false
  }
}

async function triggerDownload(): Promise<void> {
  try {
    const res = await updateApi.download()
    updateStatus.value = res.data.data
    startStatusPolling()
  } catch {
    // handled by interceptor
  }
}

async function applyUpdate(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定重启并更新到 v${updateStatus.value?.version}？更新期间服务将短暂中断。`,
      '更新确认',
      {
        confirmButtonText: '重启更新',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
  } catch {
    return
  }
  try {
    const res = await updateApi.apply()
    const data = res.data.data
    if (data?.manual_update) {
      ElMessageBox.alert(
        `${data.message || '检测到服务由 systemd 托管，请手动更新'}<br><br><strong>更新包路径：</strong><br><code>${data.package_path}</code>`,
        '需要手动更新',
        { confirmButtonText: '我知道了', type: 'warning', dangerouslyUseHTMLString: true }
      )
      return
    }
    ElMessage.success('更新已启动，服务即将重启...')
  } catch {
    // handled by interceptor
  }
}

async function cancelDownload(): Promise<void> {
  try {
    const res = await updateApi.cancel()
    updateStatus.value = res.data.data
    stopStatusPolling()
  } catch {
    // handled by interceptor
  }
}

function startStatusPolling(): void {
  if (statusTimer) return
  statusTimer = setInterval(async () => {
    try {
      const res = await updateApi.getStatus()
      updateStatus.value = res.data.data
      if (updateStatus.value && !['downloading', 'applying'].includes(updateStatus.value.state)) {
        stopStatusPolling()
      }
    } catch {
      // ignore polling errors
    }
  }, 3000)
}

function stopStatusPolling(): void {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
}

function openDownloadUrl(): void {
  const url = updateStatus.value?.download_url
  if (url) {
    window.open(url, '_blank')
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
      <el-tabs v-model="activeTab" class="settings-tabs">
        <el-tab-pane label="AI 模型配置" name="llm">
          <div class="settings-card">
            <el-form label-position="top">
              <AiProviderConfig
                v-model:provider="selectedProvider"
                v-model:model="model"
                v-model:api-key="apiKey"
                v-model:base-url="baseUrl"
                v-model:context-length="contextLength"
                show-context-length
                :reset-on-provider-change="false"
                label-position="top"
                :api-key-placeholder="
                  config.api_key_masked ? `当前: ${config.api_key_masked}` : '请输入 API Key'
                "
              />

              <el-button type="primary" :loading="saving" style="margin-top: 16px" @click="handleSave">
                保存配置
              </el-button>
            </el-form>
          </div>
        </el-tab-pane>

        <el-tab-pane label="向量模型配置" name="embedding">
          <div class="settings-card">
            <el-alert
              title="如不配置，记忆和知识库的向量检索功能将不可用，会退化成sql的模糊搜索"
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
          </div>
        </el-tab-pane>

        <el-tab-pane label="登录安全" name="security">
          <div class="settings-card">
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
        </el-tab-pane>

        <el-tab-pane label="通知设置" name="notification">
          <div class="settings-card">
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
          </div>
        </el-tab-pane>

        <el-tab-pane label="资源市场" name="marketplace">
          <div class="settings-card">
            <div class="card-title" style="display: flex; align-items: center; gap: 8px; margin-bottom: 16px">
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
          </div>
        </el-tab-pane>

        <el-tab-pane name="update">
          <template #label>
            <el-badge :is-dot="hasUpdate" class="update-tab-badge">
              <span>版本更新</span>
            </el-badge>
          </template>
          <div class="settings-card">
            <div
              class="card-title"
              style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px"
            >
              <span>版本更新</span>
              <el-tag size="small" type="info">v{{ currentVersion }}</el-tag>
            </div>

            <div class="update-actions">
              <el-button type="primary" plain :loading="updateChecking" @click="checkForUpdates">
                检查更新
              </el-button>
            </div>

            <!-- 上次更新结果 -->
            <div
              v-if="updateStatus?.last_result"
              class="update-result-notice"
              :class="{
                'update-result--success': updateStatus.last_result.result === 'success',
                'update-result--fail': ['failed', 'rolled_back', 'interrupted'].includes(
                  updateStatus.last_result.result
                )
              }"
            >
              <el-icon :size="16">
                <CircleCheck v-if="updateStatus.last_result.result === 'success'" />
                <Warning v-else />
              </el-icon>
              <span>
                {{
                  updateStatus.last_result.result === 'success'
                    ? '上次更新成功'
                    : updateStatus.last_result.result === 'rolled_back'
                      ? '上次更新失败，已自动回滚'
                      : updateStatus.last_result.result === 'interrupted'
                        ? '上次更新被中断'
                        : '上次更新失败'
                }}
                <span v-if="updateStatus.last_result.error" class="update-result-error">
                  （{{ updateStatus.last_result.error }}）
                </span>
              </span>
            </div>

            <!-- 下载中 -->
            <template v-if="updateStatus?.state === 'downloading'">
              <div class="update-progress">
                <div class="update-progress-info">
                  正在下载 v{{ updateStatus.version }}... {{ updateStatus.progress }}%
                </div>
                <el-progress :percentage="updateStatus.progress" :stroke-width="8" :show-text="false" />
                <div class="update-download-row">
                  <el-button size="small" @click="cancelDownload">取消下载</el-button>
                </div>
              </div>
            </template>

            <!-- 更新应用中：新版已启动，等待 updater 写入最终结果 -->
            <template v-else-if="updateStatus?.state === 'applying'">
              <div class="update-progress">
                <div class="update-progress-info">正在完成更新 v{{ updateStatus.version }}...</div>
                <el-progress indeterminate :percentage="50" :stroke-width="8" :show-text="false" />
                <div class="update-notice-desc">服务已重启，正在确认更新结果，请稍候</div>
              </div>
            </template>

            <!-- 就绪，待重启 -->
            <template v-else-if="updateStatus?.state === 'ready'">
              <div
                class="update-notice"
                :class="{ 'update-notice--force': updateStatus.force_upgrade }"
              >
                <div class="update-notice-icon">
                  <el-icon :size="20"><Warning /></el-icon>
                </div>
                <div class="update-notice-body">
                  <div class="update-notice-title">
                    {{ updateStatus.force_upgrade ? '请尽快重启升级' : '新版本已就绪' }}
                    <span class="update-version-tag">v{{ updateStatus.version }}</span>
                  </div>
                  <div v-if="updateStatus.release_notes" class="update-notice-desc">
                    {{ updateStatus.release_notes }}
                  </div>
                </div>
              </div>
              <div class="update-download-row">
                <el-button type="primary" @click="applyUpdate">重启更新</el-button>
                <el-button @click="openDownloadUrl">前往下载</el-button>
              </div>
            </template>

            <!-- 下载/更新失败（更新包校验不过时同样进入此态，可重新下载） -->
            <template v-else-if="updateStatus?.state === 'failed'">
              <div class="update-notice update-notice--force">
                <div class="update-notice-body">
                  <div class="update-notice-title">更新失败</div>
                  <div v-if="updateStatus.error" class="update-notice-desc">
                    {{ updateStatus.error }}
                  </div>
                </div>
              </div>
              <div class="update-download-row">
                <el-button type="primary" @click="triggerDownload">重试下载</el-button>
                <el-button @click="openDownloadUrl">前往下载</el-button>
              </div>
            </template>

            <!-- 发现新版本（待下载） -->
            <template v-else-if="updateStatus?.has_update">
              <div
                class="update-notice"
                :class="{ 'update-notice--force': updateStatus.force_upgrade }"
              >
                <div class="update-notice-icon">
                  <el-icon :size="20"><Warning /></el-icon>
                </div>
                <div class="update-notice-body">
                  <div class="update-notice-title">
                    {{ updateStatus.force_upgrade ? '需要强制升级' : '发现新版本' }}
                    <span class="update-version-tag">v{{ updateStatus.latest_version }}</span>
                  </div>
                  <div v-if="updateStatus.release_notes" class="update-notice-desc">
                    {{ updateStatus.release_notes }}
                  </div>
                </div>
              </div>
              <div class="update-download-row">
                <span v-if="updateStatus.published_at" class="update-pub-time">
                  发布于 {{ updateStatus.published_at }}
                </span>
                <el-button type="primary" @click="triggerDownload">下载更新</el-button>
                <el-button @click="openDownloadUrl">前往下载</el-button>
              </div>
            </template>

            <!-- 已是最新 -->
            <div v-else-if="updateStatus" class="update-up-to-date">
              <el-icon :size="16"><CircleCheck /></el-icon>
              <span>已是最新版本</span>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
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

.settings-card {
  width: 100%;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 24px;
  box-sizing: border-box;
}

.settings-tabs :deep(.el-tabs__header) {
  margin-bottom: 20px;
}

.update-tab-badge :deep(.el-badge__content.is-dot) {
  top: 8px;
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

.update-result-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 10px 16px;
  border-radius: 8px;
  font-size: 13px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  color: #64748b;
}

.update-result--success {
  background: #f0fdf4;
  border-color: #bbf7d0;
  color: #16a34a;
}

.update-result--fail {
  background: #fef2f2;
  border-color: #fca5a5;
  color: #dc2626;
}

.update-result-error {
  color: inherit;
  opacity: 0.8;
}

.update-progress {
  margin-top: 12px;
}

.update-progress-info {
  font-size: 13px;
  color: #475569;
  margin-bottom: 8px;
  font-weight: 500;
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
