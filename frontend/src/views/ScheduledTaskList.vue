<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus,
  Edit,
  Delete,
  Document,
  VideoPlay,
  QuestionFilled,
  Open
} from '@element-plus/icons-vue'
import { scheduledTaskApi } from '@/api/scheduledTask'
import { flowApi } from '@/api/flow'
import type { ScheduledTask, ScheduledTaskLog } from '@/api/scheduledTask'
import type { Flow, FlowIOField, FlowDetail } from '@/types/flow'
import type { PaginatedResponse } from '@/types/common'
import FlowInputForm from '@/components/common/FlowInputForm.vue'
import ActionColumn from '@/components/common/ActionColumn.vue'
import { useIsMobile } from '@/composables/useIsMobile'

const router = useRouter()
const { isMobile } = useIsMobile()

const loading = ref(false)
const tableData = ref<ScheduledTask[]>([])
const total = ref(0)

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    name: '',
    is_enabled: undefined as number | undefined
  }
})

const statusMap: Record<number, { text: string; type: 'success' | 'danger' }> = {
  0: { text: '禁用', type: 'danger' },
  1: { text: '启用', type: 'success' }
}

const runStatusMap: Record<number, { text: string; type: 'success' | 'danger' }> = {
  0: { text: '失败', type: 'danger' },
  1: { text: '成功', type: 'success' }
}

const targetMap: Record<string, string> = {
  flow: '流程',
  agent: 'Agent'
}

const scheduleTypeMap: Record<string, { text: string; type: 'primary' | 'warning' }> = {
  cron: { text: '循环', type: 'primary' },
  once: { text: '单次', type: 'warning' }
}

async function loadData() {
  loading.value = true
  try {
    const res = await scheduledTaskApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<ScheduledTask>
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
  handleSearch()
}

function getRowActions(row: any) {
  return [
    {
      key: 'toggle',
      label: row.is_enabled === 1 ? '禁用' : '启用',
      icon: Open,
      btnClass: row.is_enabled === 1 ? 'action-warning' : 'action-success'
    },
    { key: 'trigger', label: '触发', icon: VideoPlay, btnClass: 'action-success' },
    { key: 'edit', label: '编辑', icon: Edit, btnClass: 'action-edit' },
    { key: 'logs', label: '日志', icon: Document, btnClass: 'action-view' },
    { key: 'delete', label: '删除', icon: Delete, btnClass: 'action-delete', danger: true }
  ]
}

function onRowAction(row: any, key: string) {
  switch (key) {
    case 'toggle':
      handleToggle(row)
      break
    case 'trigger':
      handleTrigger(row)
      break
    case 'edit':
      openEditDialog(row)
      break
    case 'logs':
      showLogs(row)
      break
    case 'delete':
      handleDelete(row)
      break
  }
}

async function handleToggle(row: ScheduledTask) {
  try {
    const res = await scheduledTaskApi.toggle(row.id!)
    if (res.data.code === 1) {
      ElMessage.success(row.is_enabled === 1 ? '已禁用' : '已启用')
      loadData()
    }
  } catch {
    // 错误已由拦截器处理
  }
}

async function handleTrigger(row: ScheduledTask) {
  try {
    await ElMessageBox.confirm(`确定要手动触发「${row.name}」吗？`, '手动触发', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    const res = await scheduledTaskApi.trigger(row.id!)
    if (res.data.code === 1) {
      ElMessage.success('已触发执行')
      loadData()
    }
  } catch {
    // 用户取消或错误
  }
}

async function handleDelete(row: ScheduledTask) {
  try {
    await ElMessageBox.confirm(`确定要删除「${row.name}」吗？`, '删除确认', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    const res = await scheduledTaskApi.delete(row.id!)
    if (res.data.code === 1) {
      ElMessage.success('删除成功')
      loadData()
    }
  } catch {
    // 用户取消或错误
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

onMounted(() => {
  loadData()
})

const cronPresets = [
  { label: '每分钟', value: '* * * * *' },
  { label: '每小时', value: '0 * * * *' },
  { label: '每天8点', value: '0 8 * * *' },
  { label: '每天0点', value: '0 0 * * *' },
  { label: '每周一9点', value: '0 9 * * 1' },
  { label: '每月1号', value: '0 0 1 * *' }
]

const dialogVisible = ref(false)
const dialogLoading = ref(false)
const dialogTitle = ref('新建定时任务')
const isEdit = ref(false)
const editId = ref<number>(0)

const targetOptions = ref<Flow[]>([])
const targetLoading = ref(false)
const targetSearchPage = ref(1)
const targetSearchTotal = ref(0)
const targetSearchKeyword = ref('')
const targetHasMore = ref(false)

const inputSchemaLoading = ref(false)
const inputFields = ref<FlowIOField[]>([])
const inputFormData = ref<Record<string, unknown>>({})
const inputFormRef = ref<InstanceType<typeof FlowInputForm>>()

const form = reactive({
  name: '',
  schedule_type: 'cron' as string,
  cron_expression: '0 8 * * *',
  run_at: '' as string,
  target_type: 'flow' as string,
  target_id: undefined as number | undefined,
  is_enabled: 0 as number,
  input_data: ''
})

function resetForm() {
  form.name = ''
  form.schedule_type = 'cron'
  form.cron_expression = '0 8 * * *'
  form.run_at = ''
  form.target_type = 'flow'
  form.target_id = undefined
  form.is_enabled = 0
  form.input_data = ''
  targetOptions.value = []
  targetSearchKeyword.value = ''
  inputFields.value = []
  inputFormData.value = {}
}

async function openCreateDialog() {
  resetForm()
  dialogTitle.value = '新建定时任务'
  isEdit.value = false
  editId.value = 0
  dialogVisible.value = true
  await loadTargets(true)
}

async function openEditDialog(row: ScheduledTask) {
  resetForm()
  dialogTitle.value = '编辑定时任务'
  isEdit.value = true
  editId.value = row.id!
  form.name = row.name || ''
  form.schedule_type = row.schedule_type || 'cron'
  form.cron_expression = row.cron_expression || '0 8 * * *'
  form.run_at = row.run_at || ''
  form.target_type = row.target_type || 'flow'
  form.target_id = row.target_id
  form.is_enabled = row.is_enabled ?? 0
  targetOptions.value = []
  targetSearchKeyword.value = ''
  dialogVisible.value = true
  await loadTargets(true)
  if (form.target_id) {
    await fetchTargetById(form.target_id)
    await loadInputSchema(form.target_id)
    if (row.input_data) {
      inputFormData.value = { ...row.input_data }
    }
  }
}

async function fetchTargetById(id: number) {
  try {
    const res = await flowApi.get(id)
    if (res.data.code === 1 && res.data.data) {
      const item = res.data.data as Flow
      if (!targetOptions.value.some(f => f.id === item.id)) {
        targetOptions.value.unshift(item)
      }
    }
  } catch {
    // 静默处理
  }
}

async function loadInputSchema(targetId: number) {
  inputFields.value = []
  inputFormData.value = {}
  if (!targetId) return
  inputSchemaLoading.value = true
  try {
    const res = await flowApi.get(targetId)
    if (res.data.code === 1 && res.data.data) {
      const flow = res.data.data as FlowDetail
      inputFields.value = flow.input_schema?.fields || []
      if (form.target_type === 'flow' && flow.nodes?.some(n => n.node_type === 'human')) {
        ElMessage.warning('该流程包含人类帮助节点，不支持作为定时任务目标')
        form.target_id = undefined
        inputFields.value = []
        inputFormData.value = {}
      }
    }
  } finally {
    inputSchemaLoading.value = false
  }
}

async function loadTargets(reset = false) {
  if (targetLoading.value) return
  if (reset) {
    targetSearchPage.value = 1
    targetOptions.value = []
    targetHasMore.value = true
  }
  if (!targetHasMore.value) return
  targetLoading.value = true
  try {
    const condition: Record<string, unknown> = { flow_type: form.target_type }
    if (targetSearchKeyword.value) {
      condition.name = targetSearchKeyword.value
    }
    const res = await flowApi.page({
      page: targetSearchPage.value,
      page_size: 20,
      condition
    })
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<Flow>
      if (reset) {
        targetOptions.value = data.items
      } else {
        targetOptions.value = [...targetOptions.value, ...data.items]
      }
      targetSearchTotal.value = data.total
      targetHasMore.value = targetOptions.value.length < data.total
    }
  } finally {
    targetLoading.value = false
  }
}

function handleTargetSearch(query: string) {
  targetSearchKeyword.value = query
  loadTargets(true)
}

function handleTargetScroll({ scrollTop }: { scrollTop: number }) {
  if (targetHasMore.value && !targetLoading.value && scrollTop > 0) {
    targetSearchPage.value++
    loadTargets()
  }
}

async function handleTargetChange(targetId: number) {
  form.input_data = ''
  inputFormData.value = {}
  await loadInputSchema(targetId)
}

function handleTargetTypeChange() {
  form.target_id = undefined
  form.input_data = ''
  targetSearchKeyword.value = ''
  inputFields.value = []
  inputFormData.value = {}
  loadTargets(true)
}

function applyPreset(cron: string) {
  form.cron_expression = cron
}

async function handleSubmit() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入任务名称')
    return
  }
  if (form.schedule_type === 'cron') {
    if (!form.cron_expression.trim()) {
      ElMessage.warning('请输入Cron表达式')
      return
    }
  } else {
    if (!form.run_at) {
      ElMessage.warning('请选择运行时间')
      return
    }
    if (new Date(form.run_at).getTime() < Date.now()) {
      ElMessage.warning('运行时间不能早于当前时间')
      return
    }
  }
  if (!form.target_id) {
    ElMessage.warning('请选择执行目标')
    return
  }

  let inputData: Record<string, unknown> | undefined
  if (inputFormRef.value && inputFields.value.length > 0) {
    const error = inputFormRef.value.validate()
    if (error) {
      ElMessage.warning(error)
      return
    }
    const { input } = inputFormRef.value.collect()
    inputData = Object.keys(input).length > 0 ? input : undefined
  }

  dialogLoading.value = true
  try {
    const payload: Partial<ScheduledTask> = {
      name: form.name,
      schedule_type: form.schedule_type,
      cron_expression: form.schedule_type === 'cron' ? form.cron_expression : undefined,
      run_at: form.schedule_type === 'once' ? form.run_at : undefined,
      target_type: form.target_type,
      target_id: form.target_id,
      is_enabled: form.is_enabled,
      input_data: inputData
    }
    if (isEdit.value) {
      const res = await scheduledTaskApi.update({ id: editId.value, ...payload })
      if (res.data.code === 1) {
        ElMessage.success('更新成功')
        dialogVisible.value = false
        loadData()
      }
    } else {
      const res = await scheduledTaskApi.create(payload)
      if (res.data.code === 1) {
        ElMessage.success('创建成功')
        dialogVisible.value = false
        loadData()
      }
    }
  } finally {
    dialogLoading.value = false
  }
}

const logDialogVisible = ref(false)
const logLoading = ref(false)
const logData = ref<ScheduledTaskLog[]>([])
const logTotal = ref(0)
const logQuery = reactive({ page: 1, page_size: 10, condition: { task_id: 0 as number } })
const logTaskTarget = reactive({ target_type: '' as string, target_id: 0 as number })

const logStatusMap: Record<number, { text: string; type: 'success' | 'danger' | 'warning' }> = {
  0: { text: '运行中', type: 'warning' },
  1: { text: '成功', type: 'success' },
  2: { text: '失败', type: 'danger' }
}

const triggerTypeMap: Record<number, string> = {
  1: '定时触发',
  2: '手动触发'
}

async function showLogs(row: ScheduledTask) {
  logQuery.condition.task_id = row.id!
  logQuery.page = 1
  logTaskTarget.target_type = row.target_type || 'flow'
  logTaskTarget.target_id = row.target_id || 0
  logDialogVisible.value = true
  await loadLogs()
}

async function loadLogs() {
  logLoading.value = true
  try {
    const res = await scheduledTaskApi.logsPage(logQuery)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<ScheduledTaskLog>
      logData.value = data.items
      logTotal.value = data.total
    }
  } finally {
    logLoading.value = false
  }
}
</script>

<template>
  <div class="scheduled-task-page page">
    <div class="page-header">
      <h1 class="page-title">定时任务管理</h1>
      <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建定时任务</el-button>
    </div>

    <el-form :inline="true" class="search-bar" @submit.prevent="handleSearch">
      <el-form-item label="任务名称">
        <el-input
          v-model="queryParams.condition.name"
          placeholder="搜索任务名称"
          clearable
          @keyup.enter="handleSearch"
        />
      </el-form-item>
      <el-form-item label="状态">
        <el-select
          v-model="queryParams.condition.is_enabled"
          placeholder="全部"
          clearable
          @change="handleSearch"
        >
          <el-option label="启用" :value="1" />
          <el-option label="禁用" :value="0" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button class="btn-search" @click="handleSearch">查询</el-button>
        <el-button class="btn-reset" @click="handleReset">重置</el-button>
      </el-form-item>
    </el-form>

    <div class="card-panel">
      <el-table v-loading="loading" :data="tableData" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="任务名称" min-width="150" />
        <el-table-column label="调度类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="scheduleTypeMap[row.schedule_type || 'cron']?.type" size="small">
              {{ scheduleTypeMap[row.schedule_type || 'cron']?.text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="调度配置" width="170">
          <template #default="{ row }">
            <code v-if="(row.schedule_type || 'cron') === 'cron'">{{ row.cron_expression }}</code>
            <span v-else>{{ row.run_at || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="target_type" label="执行目标" width="100">
          <template #default="{ row }">
            {{ targetMap[row.target_type || 'flow'] || row.target_type }}
          </template>
        </el-table-column>
        <el-table-column prop="is_enabled" label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="statusMap[row.is_enabled!]?.type" size="small">
              {{ statusMap[row.is_enabled!]?.text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="next_run_time" label="下次执行" width="170" />
        <el-table-column prop="last_run_time" label="上次执行" width="170" />
        <el-table-column prop="last_run_status" label="上次结果" width="90" align="center">
          <template #default="{ row }">
            <template v-if="row.last_run_status !== null && row.last_run_status !== undefined">
              <el-tag :type="runStatusMap[row.last_run_status]?.type" size="small">
                {{ runStatusMap[row.last_run_status]?.text }}
              </el-tag>
            </template>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" :width="isMobile ? 60 : 260" fixed="right">
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
          layout="total, sizes, prev, pager, next"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="550px" destroy-on-close>
      <el-form label-width="120px" size="default">
        <el-form-item label="任务名称" required>
          <el-input v-model="form.name" placeholder="输入任务名称" />
        </el-form-item>
        <el-form-item label="启用" required>
          <el-switch v-model="form.is_enabled" :active-value="1" :inactive-value="0" />
        </el-form-item>
        <el-form-item label="调度类型" required>
          <el-radio-group v-model="form.schedule_type">
            <el-radio value="cron">循环执行</el-radio>
            <el-radio value="once">执行一次</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="form.schedule_type === 'cron'">
          <el-form-item required>
            <template #label>
              Cron 表达式
              <el-tooltip placement="top">
                <template #content>
                  <div style="line-height: 1.8">
                    <div><b>格式：分 时 日 月 周</b></div>
                    <div>分：0-59</div>
                    <div>时：0-23</div>
                    <div>日：1-31</div>
                    <div>月：1-12</div>
                    <div>周：0-6（0=周日）</div>
                    <div style="margin-top: 4px">特殊符号：* 任意，/ 间隔，- 范围，, 列表</div>
                    <div>示例：*/5 * * * * = 每5分钟</div>
                  </div>
                </template>
                <el-icon style="vertical-align: -2px; margin-left: 2px; color: #94a3b8; cursor: help">
                  <QuestionFilled />
                </el-icon>
              </el-tooltip>
            </template>
            <el-input v-model="form.cron_expression" placeholder="分 时 日 月 周" />
          </el-form-item>
          <el-form-item label="">
            <div class="cron-presets">
              <el-button
                v-for="preset in cronPresets"
                :key="preset.value"
                size="small"
                @click="applyPreset(preset.value)"
              >
                {{ preset.label }}
              </el-button>
            </div>
          </el-form-item>
        </template>
        <el-form-item v-else label="运行时间" required>
          <el-date-picker
            v-model="form.run_at"
            type="datetime"
            placeholder="选择运行时间"
            format="YYYY-MM-DD HH:mm"
            value-format="YYYY-MM-DD HH:mm:ss"
            :disabled-date="(date: Date) => date.getTime() < Date.now() - 24 * 3600 * 1000"
            style="width: 100%"
          />
          <div class="once-tip">到达指定时间后执行一次，执行完毕自动禁用</div>
        </el-form-item>
        <el-form-item label="执行目标" required>
          <el-radio-group v-model="form.target_type" @change="handleTargetTypeChange">
            <el-radio value="flow">流程</el-radio>
            <el-radio value="agent">Agent</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="目标" required>
          <el-select
            v-model="form.target_id"
            :placeholder="form.target_type === 'flow' ? '选择流程' : '选择Agent'"
            style="width: 100%"
            filterable
            remote
            :remote-method="handleTargetSearch"
            :loading="targetLoading"
            @change="handleTargetChange"
          >
            <el-scrollbar max-height="200px" @scroll="handleTargetScroll($event)">
              <el-option
                v-for="item in targetOptions"
                :key="item.id"
                :label="item.name"
                :value="item.id"
              />
              <div v-if="targetLoading" class="loading-more">
                <span>加载中...</span>
              </div>
              <div v-if="!targetHasMore && targetOptions.length > 0" class="no-more">
                <span>已加载全部</span>
              </div>
            </el-scrollbar>
          </el-select>
        </el-form-item>
        <template v-if="inputSchemaLoading">
          <el-form-item label="输入参数">
            <span class="text-muted">加载输入参数中...</span>
          </el-form-item>
        </template>
        <FlowInputForm
          v-else-if="inputFields.length > 0"
          ref="inputFormRef"
          v-model="inputFormData"
          :fields="inputFields"
          label-width="120px"
        />
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="dialogLoading" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="logDialogVisible" title="执行日志" width="800px" destroy-on-close>
      <el-table v-loading="logLoading" :data="logData" stripe size="small">
        <el-table-column prop="trigger_type" label="触发方式" width="90">
          <template #default="{ row }">
            {{ triggerTypeMap[row.trigger_type!] || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="logStatusMap[row.status!]?.type" size="small">
              {{ logStatusMap[row.status!]?.text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="start_time" label="开始时间" width="170" />
        <el-table-column prop="end_time" label="结束时间" width="170" />
        <el-table-column prop="duration_ms" label="耗时" width="90">
          <template #default="{ row }">
            {{ row.duration_ms ? `${(row.duration_ms / 1000).toFixed(1)}s` : '-' }}
          </template>
        </el-table-column>
        <el-table-column
          prop="error_message"
          label="错误信息"
          min-width="150"
          show-overflow-tooltip
        />
        <el-table-column label="查看" width="70" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="logTaskTarget.target_type === 'flow' && row.execution_id"
              type="primary"
              link
              size="small"
              @click="router.push(`/execution?executionId=${row.execution_id}`)"
            >
              记录
            </el-button>
            <el-button
              v-else-if="logTaskTarget.target_type === 'agent' && row.session_id"
              type="primary"
              link
              size="small"
              @click="router.push(`/chat/${logTaskTarget.target_id}?sessionId=${row.session_id}`)"
            >
              会话
            </el-button>
            <span v-else style="color: #c0c4cc">-</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination">
        <el-pagination
          v-model:current-page="logQuery.page"
          :total="logTotal"
          :page-size="logQuery.page_size"
          layout="prev, pager, next"
          size="small"
          @current-change="loadLogs"
        />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.scheduled-task-page .card-panel {
  flex: 1;
  min-height: 200px;
}

.text-muted {
  color: #c0c4cc;
}

.cron-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.once-tip {
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.4;
}

.loading-more,
.no-more {
  padding: 8px 0;
  text-align: center;
  color: #c0c4cc;
  font-size: 12px;
}
</style>
