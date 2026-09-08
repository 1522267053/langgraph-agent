<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  providerConnectionApi,
  type AIProviderConnectionInfo,
  type AIProviderConnectionPayload
} from '@/api/aiProviderConnection'
import { aiProviderApi, type ProviderInfo, type ModelInfo } from '@/api/ai_provider'
import { CONTEXT_LENGTH_PRESETS, parseContextLength } from '@/components/FlowEditor/config/types'

const loading = ref(false)
const connections = ref<AIProviderConnectionInfo[]>([])

const providerList = ref<ProviderInfo[]>([])
const dialogModelOptions = ref<{ value: string; label: string; contextLength?: number }[]>([])

const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)

const form = ref<{
  provider_id: string
  api_key: string
  base_url: string
  default_model: string
  context_length: number | undefined
  remark: string
  is_enabled: number
}>(emptyForm())

function emptyForm() {
  return {
    provider_id: '',
    api_key: '',
    base_url: '',
    default_model: '',
    context_length: undefined as number | undefined,
    remark: '',
    is_enabled: 1
  }
}

/** 编辑时 api_key 显示为脱敏占位（留空提交 = 不修改） */
const apiKeyPlaceholder = computed(() => {
  if (editingId.value) {
    const conn = connections.value.find(c => c.id === editingId.value)
    if (conn?.api_key) return `当前: ${conn.api_key}（留空不修改）`
    return '当前未设置（留空不修改）'
  }
  return '请输入 API Key'
})

async function loadConnections() {
  loading.value = true
  try {
    const res = await providerConnectionApi.page({ page: 1, page_size: 100 })
    connections.value = res.data.data?.items || []
  } catch {
    /* handled by interceptor */
  } finally {
    loading.value = false
  }
}

async function loadProviders() {
  try {
    const res = await aiProviderApi.list()
    providerList.value = res.data.data || []
  } catch {
    /* silent */
  }
}

async function onProviderChange(providerId: string, keepCurrent = false) {
  dialogModelOptions.value = []
  // 编辑回显时仅加载模型列表，不清空已保存的默认模型、不自动填默认 Base URL
  if (!keepCurrent) {
    form.value.default_model = ''
    const provider = providerList.value.find(p => p.name === providerId)
    if (provider?.default_base_url && !form.value.base_url) {
      form.value.base_url = provider.default_base_url
    }
  }
  if (!providerId) return
  try {
    const res = await aiProviderApi.getModels(providerId)
    dialogModelOptions.value = (res.data.data || []).map((m: ModelInfo) => ({
      value: m.model_id,
      label: m.name,
      contextLength: m.limits?.context ?? undefined
    }))
  } catch {
    /* silent */
  }
}

/** 选中模型时按模型元数据自动填充上下文窗口（用户主动选择，直接覆盖） */
function onModelChange(modelId: string) {
  if (!modelId) return
  const opt = dialogModelOptions.value.find(m => m.value === modelId)
  if (opt?.contextLength) {
    form.value.context_length = opt.contextLength
  }
}

function openCreate() {
  editingId.value = null
  form.value = emptyForm()
  dialogModelOptions.value = []
  dialogVisible.value = true
}

function openEdit(conn: AIProviderConnectionInfo) {
  editingId.value = conn.id
  form.value = {
    provider_id: conn.provider_id,
    api_key: '',
    base_url: conn.base_url || '',
    default_model: conn.default_model || '',
    context_length: conn.context_length || undefined,
    remark: conn.remark || '',
    is_enabled: conn.is_enabled
  }
  onProviderChange(conn.provider_id, true)
  dialogVisible.value = true
}

async function handleSave() {
  if (!form.value.provider_id) {
    ElMessage.error({ message: '请选择供应商', duration: 5000 })
    return
  }
  if (!editingId.value && !form.value.api_key.trim()) {
    ElMessage.error({ message: '请输入 API Key', duration: 5000 })
    return
  }
  if (
    form.value.context_length !== undefined &&
    form.value.context_length !== ('' as unknown as number) &&
    parseContextLength(form.value.context_length) === undefined
  ) {
    ElMessage.error({
      message: '上下文窗口格式无效，请输入数字或带单位（如 32000、32K、1M）',
      duration: 5000
    })
    return
  }

  const payload: AIProviderConnectionPayload = {
    provider_id: form.value.provider_id,
    base_url: form.value.base_url.trim() || null,
    default_model: form.value.default_model.trim() || null,
    context_length: parseContextLength(form.value.context_length) ?? null,
    is_enabled: form.value.is_enabled,
    remark: form.value.remark.trim() || null
  }
  if (form.value.api_key.trim()) {
    payload.api_key = form.value.api_key.trim()
  }

  saving.value = true
  try {
    if (editingId.value) {
      payload.id = editingId.value
      await providerConnectionApi.update(payload)
      ElMessage.success({ message: '连接已更新', duration: 5000 })
    } else {
      await providerConnectionApi.create(payload)
      ElMessage.success({ message: '连接已添加', duration: 5000 })
    }
    dialogVisible.value = false
    await loadConnections()
  } catch {
    /* handled by interceptor */
  } finally {
    saving.value = false
  }
}

async function handleSetDefault(conn: AIProviderConnectionInfo) {
  try {
    await providerConnectionApi.setDefault(conn.id)
    ElMessage.success({ message: `已将「${conn.provider_name}」设为默认连接`, duration: 5000 })
    await loadConnections()
  } catch {
    /* handled by interceptor */
  }
}

async function handleToggleEnabled(conn: AIProviderConnectionInfo) {
  try {
    await providerConnectionApi.update({
      id: conn.id,
      provider_id: conn.provider_id,
      is_enabled: conn.is_enabled
    })
  } catch {
    conn.is_enabled = conn.is_enabled === 1 ? 0 : 1
  }
}

async function handleDelete(conn: AIProviderConnectionInfo) {
  const isDefault = conn.is_default === 1
  try {
    await ElMessageBox.confirm(
      isDefault
        ? `「${conn.provider_name}」是全局默认连接，删除后默认标记将自动转移到其他启用的连接。确定删除？`
        : `确定删除供应商连接「${conn.provider_name}」？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await providerConnectionApi.delete(conn.id)
    ElMessage.success({ message: '连接已删除', duration: 5000 })
    await loadConnections()
  } catch {
    /* handled by interceptor */
  }
}

/** 简单同步模型元数据（models.dev），复用供应商列表接口 */
const syncing = ref(false)
async function handleSync() {
  if (syncing.value) return
  syncing.value = true
  try {
    await aiProviderApi.sync()
    await loadProviders()
    ElMessage.success({ message: '供应商与模型数据已同步', duration: 5000 })
  } catch {
    /* handled by interceptor */
  } finally {
    syncing.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadConnections(), loadProviders()])
})
</script>

<template>
  <div v-loading="loading" class="connection-manager">
    <el-alert
      title="连接的供应商可在 Agent 对话页跨供应商切换模型；「默认」连接同时作为内置 AI 助手和新建 LLM 节点的默认配置"
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    />

    <div class="toolbar">
      <el-button type="primary" :icon="Plus" @click="openCreate">添加连接</el-button>
      <el-tooltip content="从 models.dev 刷新供应商与模型元数据" placement="top">
        <el-button :loading="syncing" :icon="Refresh" @click="handleSync">同步数据</el-button>
      </el-tooltip>
    </div>

    <el-table :data="connections" style="width: 100%">
      <el-table-column label="供应商" min-width="140">
        <template #default="{ row }">
          <div class="provider-cell">
            <span>{{ row.provider_name }}</span>
            <el-tag v-if="row.is_default === 1" type="success" size="small">默认</el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="API Key">
        <template #default="{ row }">
          <span class="muted">{{ row.api_key || '未设置' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Base URL" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="muted">{{ row.base_url || '供应商默认' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="默认模型" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="muted">{{ row.default_model || '未设置' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="70">
        <template #default="{ row }">
          <el-switch
            v-model="row.is_enabled"
            :active-value="1"
            :inactive-value="0"
            @change="handleToggleEnabled(row)"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.is_default !== 1"
            size="small"
            link
            type="primary"
            @click="handleSetDefault(row)"
          >
            设为默认
          </el-button>
          <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" link type="danger" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无供应商连接，点击「添加连接」开始配置" :image-size="72" />
      </template>
    </el-table>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑供应商连接' : '添加供应商连接'"
      width="520px"
      destroy-on-close
    >
      <el-form label-position="top">
        <el-form-item label="供应商" required>
          <el-select
            v-model="form.provider_id"
            placeholder="选择供应商"
            filterable
            :disabled="!!editingId"
            style="width: 100%"
            @change="onProviderChange"
          >
            <el-option v-for="p in providerList" :key="p.name" :label="p.label" :value="p.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key" :required="!editingId">
          <el-input
            v-model="form.api_key"
            type="password"
            :placeholder="apiKeyPlaceholder"
            show-password
            clearable
          />
        </el-form-item>
        <el-form-item label="默认模型">
          <el-select
            v-model="form.default_model"
            placeholder="选择或输入模型名称"
            style="width: 100%"
            filterable
            allow-create
            default-first-option
            clearable
            :disabled="!form.provider_id"
            @change="onModelChange"
          >
            <el-option
              v-for="m in dialogModelOptions"
              :key="m.value"
              :label="m.label"
              :value="m.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.base_url" placeholder="留空使用供应商默认地址" clearable />
        </el-form-item>
        <el-form-item label="上下文窗口">
          <el-select
            v-model="form.context_length"
            placeholder="选择或输入上下文大小"
            style="width: 100%"
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
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" placeholder="可选" clearable />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_enabled" :active-value="1" :inactive-value="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.connection-manager {
  width: 100%;
}

.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.provider-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.muted {
  color: #64748b;
}
</style>
