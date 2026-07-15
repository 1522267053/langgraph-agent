<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { Plus, Refresh, Edit, Delete, View, CopyDocument } from '@element-plus/icons-vue'
import { webhookApi } from '@/api/webhook'
import { flowApi } from '@/api/flow'
import ActionColumn from '@/components/common/ActionColumn.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import type { WebhookConfig, WebhookCreate } from '@/types/webhook'
import type { Flow, FlowIOField } from '@/types/flow'
import type { PaginatedResponse } from '@/types/common'
import { ElMessage, ElMessageBox } from 'element-plus'

const { isMobile } = useIsMobile()

const loading = ref(false)
const tableData = ref<WebhookConfig[]>([])
const total = ref(0)

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    name: '',
    is_enabled: undefined as number | undefined
  }
})

async function loadData() {
  loading.value = true
  try {
    const res = await webhookApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<WebhookConfig>
      tableData.value = data.items
      total.value = data.total
    }
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  queryParams.page = 1
  loadData()
}

function handleReset() {
  queryParams.condition.name = ''
  queryParams.condition.is_enabled = undefined
  queryParams.page = 1
  loadData()
}

function handlePageChange(page: number) {
  queryParams.page = page
  loadData()
}

function handleSizeChange(size: number) {
  queryParams.page_size = size
  queryParams.page = 1
  loadData()
}

const statusOptions = [
  { label: '启用', value: 1 },
  { label: '禁用', value: 0 }
]

// ---- 流程列表（供选择） ----
const flowList = ref<Flow[]>([])
const agentList = ref<Flow[]>([])

async function loadFlows() {
  try {
    const [flowRes, agentRes] = await Promise.all([
      flowApi.page({ page: 1, page_size: 100, condition: { flow_type: 'flow' } }),
      flowApi.page({ page: 1, page_size: 100, condition: { flow_type: 'agent' } })
    ])
    flowList.value = flowRes.data.data?.items || []
    agentList.value = agentRes.data.data?.items || []
  } catch {
    // ignore
  }
}

const allFlows = computed(() => [...flowList.value, ...agentList.value])

function flowName(flowId: number): string {
  const flow = allFlows.value.find(f => f.id === flowId)
  return flow?.name || `#${flowId}`
}

function flowType(flowId: number): string {
  const flow = allFlows.value.find(f => f.id === flowId)
  return flow?.flow_type === 'agent' ? '智能体' : '流程'
}

// ---- 新增/编辑弹窗 ----
const dialogVisible = ref(false)
const isEdit = ref(false)
const formLoading = ref(false)

const formData = reactive<WebhookCreate & { id?: number }>({
  flow_id: undefined,
  name: '',
  description: '',
  input_config: undefined,
  is_enabled: 1
})

const inputConfigText = ref('')

/** 根据流程输入字段生成默认参数模板 */
function generateInputTemplate(fields: FlowIOField[]): string {
  if (!fields || fields.length === 0) return ''
  const template: Record<string, unknown> = {}
  for (const field of fields) {
    switch (field.type) {
      case 'number':
        template[field.name] = 0
        break
      case 'boolean':
        template[field.name] = false
        break
      case 'array':
      case 'file_list':
        template[field.name] = []
        break
      case 'object':
        template[field.name] = {}
        break
      default:
        template[field.name] = ''
    }
  }
  return JSON.stringify(template, null, 2)
}

/** 选择流程时自动填充输入参数模板 */
watch(
  () => formData.flow_id,
  newId => {
    if (!newId) {
      inputConfigText.value = ''
      return
    }
    const flow = allFlows.value.find(f => f.id === newId)
    const fields = (flow as Flow & { input_schema?: { fields?: FlowIOField[] } })?.input_schema
      ?.fields
    inputConfigText.value = fields?.length ? generateInputTemplate(fields) : ''
  }
)

function resetForm() {
  formData.flow_id = undefined
  formData.name = ''
  formData.description = ''
  formData.input_config = undefined
  formData.is_enabled = 1
  formData.id = undefined
  inputConfigText.value = ''
}

function openCreate() {
  resetForm()
  isEdit.value = false
  dialogVisible.value = true
}

function openEdit(row: WebhookConfig) {
  resetForm()
  isEdit.value = true
  formData.id = row.id
  formData.flow_id = row.flow_id
  formData.name = row.name
  formData.description = row.description || ''
  formData.is_enabled = row.is_enabled
  inputConfigText.value = row.input_config ? JSON.stringify(row.input_config, null, 2) : ''
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!formData.name.trim()) {
    ElMessage.warning('请输入名称')
    return
  }
  if (!formData.flow_id) {
    ElMessage.warning('请选择流程')
    return
  }

  if (inputConfigText.value.trim()) {
    try {
      formData.input_config = JSON.parse(inputConfigText.value)
    } catch {
      ElMessage.warning('输入参数模板 JSON 格式错误')
      return
    }
  } else {
    formData.input_config = undefined
  }

  formLoading.value = true
  try {
    if (isEdit.value && formData.id) {
      await webhookApi.update({
        id: formData.id,
        flow_id: formData.flow_id,
        name: formData.name,
        description: formData.description || undefined,
        input_config: formData.input_config,
        is_enabled: formData.is_enabled
      })
      ElMessage.success('更新成功')
    } else {
      await webhookApi.create({
        flow_id: formData.flow_id,
        name: formData.name,
        description: formData.description || undefined,
        input_config: formData.input_config,
        is_enabled: formData.is_enabled
      })
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    await loadData()
  } finally {
    formLoading.value = false
  }
}

// ---- 删除 ----
async function handleDelete(row: WebhookConfig) {
  try {
    await ElMessageBox.confirm(`确定删除 Webhook「${row.name}」吗？`, '提示', {
      type: 'warning'
    })
    await webhookApi.delete(row.id!)
    ElMessage.success('删除成功')
    await loadData()
  } catch {
    // cancelled
  }
}

// ---- 查看详情 ----
const detailDialogVisible = ref(false)
const detailWebhook = ref<WebhookConfig | null>(null)

function showDetail(row: WebhookConfig) {
  detailWebhook.value = row
  detailDialogVisible.value = true
}

function getWsUrl(row: WebhookConfig): string {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  const host = location.host
  return `${protocol}://${host}/ws/trigger/${row.token}`
}

async function copyUrl(url: string) {
  try {
    await navigator.clipboard.writeText(url)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessageBox.alert(url, 'WebSocket URL', {
      confirmButtonText: '复制',
      callback: () => {
        navigator.clipboard.writeText(url).then(() => ElMessage.success('已复制'))
      }
    })
  }
}

// ---- 操作按钮 ----
function getRowActions() {
  return [
    { key: 'detail', label: '查看详情', icon: View, btnClass: 'action-detail' },
    { key: 'edit', label: '编辑', icon: Edit, btnClass: 'action-edit' },
    { key: 'delete', label: '删除', icon: Delete, btnClass: 'action-delete', danger: true }
  ]
}

function onRowAction(row: WebhookConfig, key: string) {
  switch (key) {
    case 'detail':
      showDetail(row)
      break
    case 'edit':
      openEdit(row)
      break
    case 'delete':
      handleDelete(row)
      break
  }
}

onMounted(() => {
  loadFlows()
  loadData()
})
</script>

<template>
  <div class="webhook-list-page page">
    <div class="page-header">
      <h1 class="page-title">Webhook 管理</h1>
      <div class="header-actions">
        <el-button type="primary" :icon="Plus" @click="openCreate">新建 Webhook</el-button>
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </div>

    <div class="search-bar">
      <el-form inline>
        <el-form-item label="名称">
          <el-input
            v-model="queryParams.condition.name"
            placeholder="搜索名称..."
            clearable
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="queryParams.condition.is_enabled" placeholder="全部" clearable>
            <el-option
              v-for="opt in statusOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button class="btn-search" @click="handleSearch">查询</el-button>
          <el-button class="btn-reset" @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div v-loading="loading" class="card-panel table-container">
      <el-table :data="tableData" style="width: 100%">
        <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.description" class="wh-desc">{{ row.description }}</span>
            <span v-else class="wh-no-desc">-</span>
          </template>
        </el-table-column>
        <el-table-column label="流程类型" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="flowType(row.flow_id) === '智能体' ? 'success' : 'primary'">
              {{ flowType(row.flow_id) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="关联流程" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="wh-flow-name">{{ flowName(row.flow_id) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_enabled === 1" size="small" type="success">启用</el-tag>
            <el-tag v-else size="small" type="danger">禁用</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="call_count" label="调用次数" width="100" align="center">
          <template #default="{ row }">
            <span class="wh-count">{{ row.call_count || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="last_call_time" label="最后调用" width="160">
          <template #default="{ row }">
            {{ row.last_call_time || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="create_time" label="创建时间" width="160">
          <template #default="{ row }">
            {{ row.create_time || '' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" :width="isMobile ? 60 : 200" fixed="right">
          <template #default="{ row }">
            <ActionColumn :actions="getRowActions()" @action="onRowAction(row, $event)" />
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="queryParams.page"
          v-model:page-size="queryParams.page_size"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>

    <!-- 新增/编辑弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEdit ? '编辑 Webhook' : '新建 Webhook'"
      width="600px"
    >
      <el-form label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="formData.name" placeholder="Webhook 名称" />
        </el-form-item>
        <el-form-item label="关联流程" required>
          <el-select v-model="formData.flow_id" placeholder="选择流程" style="width: 100%">
            <el-option-group v-if="flowList.length" label="流程">
              <el-option v-for="f in flowList" :key="f.id" :label="f.name" :value="f.id!" />
            </el-option-group>
            <el-option-group v-if="agentList.length" label="智能体">
              <el-option v-for="a in agentList" :key="a.id" :label="a.name" :value="a.id!" />
            </el-option-group>
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="formData.description" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
        <el-form-item label="默认输入参数模板（JSON）">
          <el-input
            v-model="inputConfigText"
            type="textarea"
            :rows="4"
            placeholder='{"key": "value"}'
          />
          <div class="form-tip">execute 指令的参数会覆盖模板中的同名键</div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="formData.is_enabled" :active-value="1" :inactive-value="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="formLoading" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>

    <!-- 查看详情弹窗 -->
    <el-dialog v-model="detailDialogVisible" title="Webhook 详情" width="700px">
      <template v-if="detailWebhook">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="名称">
            {{ detailWebhook.name }}
          </el-descriptions-item>
          <el-descriptions-item label="关联流程">
            {{ flowName(detailWebhook.flow_id) }}
          </el-descriptions-item>
          <el-descriptions-item label="流程类型">
            <el-tag
              size="small"
              :type="flowType(detailWebhook.flow_id) === '智能体' ? 'success' : 'primary'"
            >
              {{ flowType(detailWebhook.flow_id) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag v-if="detailWebhook.is_enabled === 1" size="small" type="success">启用</el-tag>
            <el-tag v-else size="small" type="danger">禁用</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="WebSocket 地址">
            <div class="url-display">
              <el-input :model-value="getWsUrl(detailWebhook)" readonly class="url-input">
                <template #append>
                  <el-button :icon="CopyDocument" @click="copyUrl(getWsUrl(detailWebhook))">
                    复制
                  </el-button>
                </template>
              </el-input>
            </div>
          </el-descriptions-item>
          <el-descriptions-item label="描述">
            {{ detailWebhook.description || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="默认输入参数">
            <pre v-if="detailWebhook.input_config" class="json-preview">{{
              JSON.stringify(detailWebhook.input_config, null, 2)
            }}</pre>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item label="调用次数">
            {{ detailWebhook.call_count || 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="最后调用">
            {{ detailWebhook.last_call_time || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">
            {{ detailWebhook.create_time || '-' }}
          </el-descriptions-item>
        </el-descriptions>

        <!-- 协议说明 -->
        <el-collapse class="proto-guide">
          <el-collapse-item title="WebSocket 协议说明" name="protocol">
            <div class="proto-section">
              <h4>客户端 → 服务端 指令</h4>
              <pre class="code-block">
// 发送执行指令（Agent 类型）
{"action": "execute", "message": "你好"}

// 指定会话执行（多轮对话）
{"action": "execute", "message": "继续", "session_id": 123}

// 注册远程工具（Agent 可反向调用客户端函数）
{"action": "register_tools", "tools": [
  {"name": "get_data", "description": "查询数据",
   "parameters": {"type": "object",
     "properties": {"query": {"type": "string"}},
     "required": ["query"]}}
]}

// 返回工具执行结果（回应 tool_invoke）
{"action": "tool_result", "call_id": "xxx", "result": "结果"}

// 创建 / 切换 / 列表 / 删除 会话
{"action": "create_session", "title": "新对话"}
{"action": "switch_session", "session_id": 123}
{"action": "list_sessions"}
{"action": "delete_session", "session_id": 123}

// 心跳
"ping"</pre
              >
            </div>
            <div class="proto-section">
              <h4>服务端 → 客户端 事件</h4>
              <pre class="code-block">
// 连接确认
{"type": "connected", "data": {"flow_type": "agent", ...}}

// 执行事件（实时流式，同 SSE）
{"type": "node_content", "data": {"content": "你好"}}   // LLM 逐字输出
{"type": "tool_call_start", "data": {"tool_name": "..."}}
{"type": "flow_done", "data": {"status": "success"}}

// 远程工具调用请求
{"type": "tool_invoke", "data": {"call_id": "xxx", "name": "get_data", "args": {...}}}

// 心跳响应
"pong"</pre
              >
            </div>
          </el-collapse-item>
        </el-collapse>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  gap: 12px;
}

.wh-desc {
  font-size: 13px;
  color: #475569;
}

.wh-no-desc {
  color: #cbd5e1;
}

.wh-flow-name {
  font-size: 13px;
  color: #475569;
  margin-left: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.wh-count {
  font-weight: 600;
  color: #1e293b;
}

.form-tip {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 4px;
}

.url-display {
  width: 100%;
}

.url-input {
  font-family: monospace;
  font-size: 12px;
}

.json-preview {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 8px;
  font-size: 12px;
  max-height: 200px;
  overflow: auto;
  margin: 0;
}

.proto-guide {
  margin-top: 16px;
}

.proto-section {
  margin-bottom: 12px;
}

.proto-section h4 {
  font-size: 13px;
  color: #1e293b;
  margin: 0 0 6px 0;
}

.code-block {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 10px;
  font-size: 12px;
  font-family: 'Courier New', monospace;
  overflow-x: auto;
  margin: 0;
  line-height: 1.6;
  color: #334155;
}
</style>
