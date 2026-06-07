<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { Plus, Refresh, QuestionFilled, Edit, Delete } from '@element-plus/icons-vue'
import { mcpServerApi } from '@/api/mcpServer'
import ActionColumn from '@/components/common/ActionColumn.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import type {
  McpServer,
  McpServerConfig,
  McpServerCreate,
  McpServerTestResult,
  McpToolInfo
} from '@/api/mcpServer'
import type { McpTransportType } from '@/types/mcpServer'
import type { PaginatedResponse } from '@/types/common'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

const { isMobile } = useIsMobile()

const loading = ref(false)
const tableData = ref<McpServer[]>([])
const total = ref(0)

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    name: '',
    transport: undefined as 'stdio' | 'sse' | 'streamable-http' | undefined,
    is_enabled: undefined as number | undefined
  }
})

const transportOptions = [
  { label: 'stdio', value: 'stdio' },
  { label: 'sse', value: 'sse' },
  { label: 'streamable-http', value: 'streamable-http' }
]

const statusOptions = [
  { label: '启用', value: 1 },
  { label: '禁用', value: 0 }
]

const dialogVisible = ref(false)
const dialogTitle = computed(() => (isEdit.value ? '编辑MCP服务器' : '新建MCP服务器'))
const isEdit = ref(false)
const formRef = ref<FormInstance>()
const formLoading = ref(false)

const defaultConfig: McpServerConfig = {
  command: '',
  args: [],
  env: {},
  url: '',
  headers: {},
  timeout: undefined
}

const formData = reactive<McpServerCreate & { id?: number }>({
  name: '',
  description: '',
  transport: 'stdio',
  is_enabled: 1,
  keep_alive: 1,
  config: { ...defaultConfig }
})

const envKey = ref('')
const envValue = ref('')
const headerKey = ref('')
const headerValue = ref('')
const argsInput = ref('')

const jsonInput = ref('')
const jsonExpanded = ref<string[]>([])

const testDialogVisible = ref(false)
const testResult = ref<McpServerTestResult | null>(null)
const testLoading = ref(false)
const testServerId = ref<number | null>(null)
const testServerName = ref('')

const rules: FormRules = {
  name: [
    { required: true, message: '请输入服务器名称', trigger: 'blur' },
    { max: 100, message: '名称不能超过100个字符', trigger: 'blur' }
  ],
  transport: [{ required: true, message: '请选择传输类型', trigger: 'change' }]
}

async function loadData() {
  loading.value = true
  try {
    const res = await mcpServerApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<McpServer>
      tableData.value = data.items
      total.value = data.total
    }
  } finally {
    loading.value = false
  }
}

function getRowActions(_row: any) {
  return [
    { key: 'edit', label: '编辑', icon: Edit, btnClass: 'action-edit' },
    { key: 'refresh', label: '刷新', icon: Refresh, btnClass: 'action-refresh' },
    { key: 'delete', label: '删除', icon: Delete, btnClass: 'action-delete', danger: true }
  ]
}

function onRowAction(row: any, key: string) {
  switch (key) {
    case 'edit':
      handleEdit(row)
      break
    case 'refresh':
      handleRefresh(row)
      break
    case 'delete':
      handleDelete(row)
      break
  }
}

function handleSearch() {
  queryParams.page = 1
  loadData()
}

function handleReset() {
  queryParams.condition = { name: '', transport: undefined, is_enabled: undefined }
  handleSearch()
}

function handleCreate() {
  isEdit.value = false
  resetForm()
  dialogVisible.value = true
}

function handleEdit(row: McpServer) {
  isEdit.value = true
  resetForm()
  loadServerDetail(row.id)
}

async function loadServerDetail(id: number) {
  formLoading.value = true
  try {
    const res = await mcpServerApi.get(id)
    if (res.data.code === 1) {
      const data = res.data.data
      formData.id = data.id
      formData.name = data.name || ''
      formData.description = data.description || ''
      formData.transport = data.transport || 'stdio'
      formData.is_enabled = data.is_enabled ?? 1
      formData.keep_alive = data.keep_alive ?? 1
      if (data.config) {
        formData.config = { ...defaultConfig, ...data.config }
      }
      dialogVisible.value = true
    }
  } finally {
    formLoading.value = false
  }
}

function resetForm() {
  formData.id = undefined
  formData.name = ''
  formData.description = ''
  formData.transport = 'stdio'
  formData.is_enabled = 1
  formData.keep_alive = 1
  formData.config = { ...defaultConfig }
  envKey.value = ''
  envValue.value = ''
  headerKey.value = ''
  headerValue.value = ''
  argsInput.value = ''
  formRef.value?.clearValidate()
}

async function handleSubmit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  formLoading.value = true
  try {
    const config = buildConfig()
    const data = {
      ...formData,
      config
    }

    if (isEdit.value) {
      await mcpServerApi.update(data as Parameters<typeof mcpServerApi.update>[0])
      ElMessage.success('更新成功')
    } else {
      await mcpServerApi.create(data)
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    loadData()
  } finally {
    formLoading.value = false
  }
}

function buildConfig(): McpServerConfig {
  const config: McpServerConfig = {}

  if (formData.transport === 'stdio') {
    if (formData.config?.command) config.command = formData.config.command
    if (formData.config?.args?.length) config.args = formData.config.args
    if (formData.config?.env && Object.keys(formData.config.env).length)
      config.env = formData.config.env
    if (formData.config?.cwd) config.cwd = formData.config.cwd
  } else {
    if (formData.config?.url) config.url = formData.config.url
    if (formData.config?.headers && Object.keys(formData.config.headers).length)
      config.headers = formData.config.headers
  }

  if (formData.config?.timeout) config.timeout = formData.config.timeout

  return config
}

function handleDelete(row: McpServer) {
  ElMessageBox.confirm(`确定要删除MCP服务器「${row.name}」吗？`, '提示', {
    type: 'warning'
  })
    .then(async () => {
      await mcpServerApi.delete(row.id!)
      ElMessage.success('删除成功')
      loadData()
    })
    .catch(() => {})
}

async function handleToolStatusChange(tool: McpToolInfo, isEnabled: number) {
  if (!testServerId.value) return
  const prev = tool.is_enabled
  tool.is_enabled = isEnabled
  try {
    await mcpServerApi.updateToolStatus(testServerId.value, tool.name, isEnabled)
  } catch {
    tool.is_enabled = prev
  }
}

async function handleRefresh(row: McpServer) {
  testLoading.value = true
  testResult.value = null
  testServerId.value = row.id ?? null
  testServerName.value = row.name ?? ''
  testDialogVisible.value = true
  try {
    const res = await mcpServerApi.refresh(row.id!)
    if (res.data.code === 1) {
      testResult.value = res.data.data
      ElMessage.success('刷新完成')
      loadData()
    } else {
      testResult.value = { success: false, tools: [], error: res.data.msg }
    }
  } catch (e) {
    const error = e as { message?: string }
    testResult.value = { success: false, tools: [], error: error.message || '刷新失败' }
  } finally {
    testLoading.value = false
  }
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

function addEnv() {
  if (!envKey.value.trim()) return
  if (!formData.config) formData.config = { ...defaultConfig }
  if (!formData.config.env) formData.config.env = {}
  formData.config.env[envKey.value.trim()] = envValue.value
  envKey.value = ''
  envValue.value = ''
}

function removeEnv(key: string) {
  if (formData.config?.env) {
    delete formData.config.env[key]
  }
}

function addHeader() {
  if (!headerKey.value.trim()) return
  if (!formData.config) formData.config = { ...defaultConfig }
  if (!formData.config.headers) formData.config.headers = {}
  formData.config.headers[headerKey.value.trim()] = headerValue.value
  headerKey.value = ''
  headerValue.value = ''
}

function removeHeader(key: string) {
  if (formData.config?.headers) {
    delete formData.config.headers[key]
  }
}

function parseArgs(input: string): string[] {
  const args: string[] = []
  let current = ''
  let inQuote: string | null = null
  for (let i = 0; i < input.length; i++) {
    const ch = input[i]
    if (inQuote) {
      if (ch === inQuote) {
        inQuote = null
      } else {
        current += ch
      }
    } else if (ch === "'" || ch === '"') {
      inQuote = ch
    } else if (/\s/.test(ch)) {
      if (current) {
        args.push(current)
        current = ''
      }
    } else {
      current += ch
    }
  }
  if (current) args.push(current)
  return args
}

function addArg() {
  if (!argsInput.value.trim()) return
  if (!formData.config) formData.config = { ...defaultConfig }
  if (!formData.config.args) formData.config.args = []
  formData.config.args.push(...parseArgs(argsInput.value.trim()))
  argsInput.value = ''
}

function removeArg(index: number) {
  formData.config?.args?.splice(index, 1)
}

const commandPreview = computed(() => {
  if (formData.transport !== 'stdio') return ''
  const cmd = formData.config?.command
  if (!cmd) return ''
  const args = formData.config?.args?.filter(Boolean)
  if (!args?.length) return cmd
  return [cmd, ...args.map(a => (a.includes(' ') ? `"${a}"` : a))].join(' ')
})

function getTransportLabel(transport: string): string {
  return transport
}

function getStatusText(isEnabled: number | undefined): string {
  return isEnabled === 1 ? '启用' : '禁用'
}

function getStatusType(isEnabled: number | undefined): '' | 'success' | 'danger' {
  return isEnabled === 1 ? 'success' : 'danger'
}

function _applyJsonEntry(key: string, entry: Record<string, unknown>): void {
  if (entry.command) {
    formData.transport = 'stdio'
  } else if (entry.url) {
    const rawType = String(entry.type || 'sse').replace(/_/g, '-')
    formData.transport = rawType as McpTransportType
  }
  formData.name = key
  const configCopy = { ...entry }
  delete configCopy.type
  formData.config = { ...defaultConfig, ...configCopy } as McpServerConfig
  jsonExpanded.value = []
  jsonInput.value = ''
}

function parseJson(): void {
  if (!jsonInput.value.trim()) {
    ElMessage.warning('请输入JSON配置')
    return
  }
  try {
    const parsed = JSON.parse(jsonInput.value) as Record<string, unknown>
    const servers = parsed.mcpServers as Record<string, Record<string, unknown>> | undefined
    if (!servers || typeof servers !== 'object') {
      ElMessage.error('JSON格式错误：缺少 mcpServers 字段')
      return
    }
    const entries = Object.entries(servers)
    if (entries.length === 0) {
      ElMessage.error('mcpServers 为空')
      return
    }
    if (entries.length === 1) {
      _applyJsonEntry(entries[0][0], entries[0][1])
      return
    }
    const radios = entries
      .map(([k, v], i) => {
        const label = v.command
          ? `stdio: ${v.command} ${Array.isArray(v.args) ? v.args[0] || '' : ''}`
          : v.url
            ? `${(v.type as string) || 'sse'}: ${v.url}`
            : '未知类型'
        return `<label style="display:flex;align-items:center;gap:8px;padding:4px 0;cursor:pointer">
            <input type="radio" name="mcp-select" value="${i}" style="margin:0" />
            <strong>${k}</strong>
            <span style="color:#909399;font-size:12px">${label}</span>
          </label>`
      })
      .join('')
    ElMessageBox.alert(
      `<div style="display:flex;flex-direction:column;gap:4px">${radios}</div>`,
      '选择要导入的MCP服务器',
      {
        dangerouslyUseHTMLString: true,
        confirmButtonText: '取消',
        showCancelButton: true,
        cancelButtonText: '导入选中的服务器',
        distinguishCancelAndClose: true
      }
    )
      .catch(action => action)
      .then(action => {
        if (action !== 'cancel') return
        const radios = document.querySelectorAll<HTMLInputElement>('input[name="mcp-select"]')
        const selectedIndex = Array.from(radios).findIndex(r => r.checked)
        if (selectedIndex < 0) {
          ElMessage.warning('请选择一个服务器')
          return
        }
        _applyJsonEntry(entries[selectedIndex][0], entries[selectedIndex][1])
      })
  } catch {
    ElMessage.error('JSON解析失败，请检查格式')
  }
}

onMounted(() => {
  loadData()
})
</script>

<template>
  <div class="mcp-server-list-page page">
    <div class="page-header">
      <h1 class="page-title">MCP服务器管理</h1>
      <el-button type="primary" :icon="Plus" @click="handleCreate">新建服务器</el-button>
    </div>

    <div class="search-bar">
      <el-form inline>
        <el-form-item label="服务器名称">
          <el-input v-model="queryParams.condition.name" placeholder="请输入" clearable />
        </el-form-item>
        <el-form-item label="传输类型">
          <el-select v-model="queryParams.condition.transport" placeholder="全部" clearable>
            <el-option
              v-for="item in transportOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="queryParams.condition.is_enabled" placeholder="全部" clearable>
            <el-option
              v-for="item in statusOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button class="btn-search" @click="handleSearch">查询</el-button>
          <el-button class="btn-reset" @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="card-panel table-container">
      <el-table v-loading="loading" :data="tableData" stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="服务器名称" min-width="150" />
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column prop="transport" label="传输类型" width="150">
          <template #default="{ row }">
            <el-tag size="small">{{ getTransportLabel(row.transport) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_enabled" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.is_enabled)" size="small">
              {{ getStatusText(row.is_enabled) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="keep_alive" label="保持连接" width="100">
          <template #default="{ row }">
            <el-tag :type="row.keep_alive === 1 ? 'success' : 'info'" size="small">
              {{ row.keep_alive === 1 ? '保持' : '释放' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_connected_at" label="刷新时间" width="180" />
        <el-table-column prop="create_time" label="创建时间" width="180" />
        <el-table-column label="操作" :width="isMobile ? 60 : 180" fixed="right">
          <template #default="{ row }">
            <ActionColumn :actions="getRowActions(row)" @action="onRowAction(row, $event)" />
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

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="600px" destroy-on-close>
      <el-form
        ref="formRef"
        v-loading="formLoading"
        :model="formData"
        :rules="rules"
        label-width="100px"
      >
        <div class="json-import-section">
          <el-link
            type="primary"
            underline="never"
            @click="jsonExpanded = jsonExpanded.length ? [] : ['json']"
          >
            {{ jsonExpanded.length ? '收起JSON导入' : '从JSON导入' }}
          </el-link>
          <template v-if="jsonExpanded.length">
            <el-input
              v-model="jsonInput"
              type="textarea"
              :rows="4"
              placeholder='粘贴MCP JSON配置，例如: { "mcpServers": { "name": { "command": "npx", "args": ["..."] } } }'
              style="margin-top: 8px"
            />
            <el-button type="primary" size="small" style="margin-top: 8px" @click="parseJson">
              解析并填充
            </el-button>
          </template>
        </div>

        <el-form-item label="服务器名称" prop="name">
          <el-input v-model="formData.name" placeholder="请输入服务器名称" maxlength="100" />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="formData.description"
            type="textarea"
            :rows="2"
            placeholder="请输入描述"
          />
        </el-form-item>
        <el-form-item label="传输类型" prop="transport">
          <el-select v-model="formData.transport" style="width: 100%">
            <el-option
              v-for="item in transportOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="启用状态" prop="is_enabled">
          <el-radio-group v-model="formData.is_enabled">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">禁用</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="保持连接">
          <div style="display: flex; align-items: center; gap: 8px">
            <el-switch
              v-model="formData.keep_alive"
              :active-value="1"
              :inactive-value="0"
              active-text="保持"
              inactive-text="释放"
            />
            <el-tooltip
              content="开启后工具调用之间保持连接不释放，适用于浏览器操作等需要维护状态的场景。关闭则每次调用完成后自动释放连接（如 xlsx 编辑）。"
              placement="top"
            >
              <el-icon style="color: #909399; cursor: help"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
        </el-form-item>

        <el-divider content-position="left">配置详情</el-divider>

        <el-form-item v-if="commandPreview" label="命令预览">
          <el-input :model-value="commandPreview" readonly>
            <template #append>
              <el-button @click="navigator.clipboard.writeText(commandPreview)">复制</el-button>
            </template>
          </el-input>
        </el-form-item>

        <template v-if="formData.transport === 'stdio'">
          <el-form-item label="执行命令">
            <el-input v-model="formData.config!.command" placeholder="例如: npx" />
          </el-form-item>
          <el-form-item label="命令参数">
            <div class="list-input">
              <div class="list-items">
                <el-tag
                  v-for="(arg, index) in formData.config?.args"
                  :key="index"
                  closable
                  class="list-tag"
                  @close="removeArg(index)"
                >
                  {{ arg }}
                </el-tag>
              </div>
              <div class="list-input-row">
                <el-input v-model="argsInput" placeholder="添加参数" @keyup.enter="addArg" />
                <el-button @click="addArg">添加</el-button>
              </div>
            </div>
          </el-form-item>
          <el-form-item label="环境变量">
            <div class="list-input">
              <div class="list-items">
                <el-tag
                  v-for="(value, key) in formData.config?.env"
                  :key="key"
                  closable
                  class="list-tag"
                  @close="removeEnv(key as string)"
                >
                  {{ key }}={{ value }}
                </el-tag>
              </div>
              <div class="list-input-row">
                <el-input v-model="envKey" placeholder="变量名" style="width: 120px" />
                <el-input v-model="envValue" placeholder="变量值" style="flex: 1" />
                <el-button @click="addEnv">添加</el-button>
              </div>
            </div>
          </el-form-item>
        </template>

        <template v-else>
          <el-form-item label="服务器URL">
            <el-input
              v-model="formData.config!.url"
              placeholder="例如: http://localhost:8080/sse"
            />
          </el-form-item>
          <el-form-item label="请求头">
            <div class="list-input">
              <div class="list-items">
                <el-tag
                  v-for="(value, key) in formData.config?.headers"
                  :key="key"
                  closable
                  class="list-tag"
                  @close="removeHeader(key as string)"
                >
                  {{ key }}: {{ value }}
                </el-tag>
              </div>
              <div class="list-input-row">
                <el-input v-model="headerKey" placeholder="Header名" style="width: 140px" />
                <el-input v-model="headerValue" placeholder="Header值" style="flex: 1" />
                <el-button @click="addHeader">添加</el-button>
              </div>
            </div>
          </el-form-item>
        </template>

        <el-form-item label="超时时间">
          <el-input-number
            v-model="formData.config!.timeout"
            :min="1"
            :max="600"
            :step="10"
            placeholder="默认60秒"
            controls-position="right"
          />
          <span style="margin-left: 10px">秒</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="formLoading" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="testDialogVisible" title="测试连接结果" width="600px">
      <div v-loading="testLoading">
        <template v-if="testResult">
          <el-alert
            :title="testResult.success ? '连接成功' : '连接失败'"
            :type="testResult.success ? 'success' : 'error'"
            :description="testResult.error"
            show-icon
            class="test-alert"
          />
          <div v-if="testResult.tools && testResult.tools.length > 0" class="tools-section">
            <h4>可用工具 ({{ testResult.tools.length }})</h4>
            <div class="tools-name-hint">
              工具将以
              <code>mcp__{{ testServerName }}__工具名</code>
              格式传递给 AI，避免不同服务器同名工具冲突
            </div>
            <el-table :data="testResult.tools" stripe size="small" max-height="300">
              <el-table-column prop="name" label="工具名称" width="180" />
              <el-table-column label="AI调用名称" width="200">
                <template #default="{ row: tool }">
                  <code>mcp__{{ testServerName }}__{{ tool.name }}</code>
                </template>
              </el-table-column>
              <el-table-column prop="description" label="描述" show-overflow-tooltip />
              <el-table-column label="启用" width="80" align="center">
                <template #default="{ row: tool }">
                  <el-switch
                    :model-value="tool.is_enabled !== 0"
                    size="small"
                    @change="(val: boolean) => handleToolStatusChange(tool, val ? 1 : 0)"
                  />
                </template>
              </el-table-column>
            </el-table>
          </div>
          <el-empty v-else-if="testResult.success" description="无可用工具" />
        </template>
      </div>
      <template #footer>
        <el-button @click="testDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.list-input {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.list-items {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.list-tag {
  max-width: 100%;
}

.list-input-row {
  display: flex;
  gap: 8px;
  width: 100%;
}
</style>
