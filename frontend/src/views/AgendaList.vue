<script setup lang="ts">
import { ref, reactive, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Edit, Delete, Check, Calendar, List } from '@element-plus/icons-vue'
import FullCalendar from '@fullcalendar/vue3'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import zhCnLocale from '@fullcalendar/core/locales/zh-cn'
import type { CalendarOptions, DateSelectArg, EventClickArg, EventDropArg } from '@fullcalendar/core'
import { agendaApi } from '@/api/agenda'
import type { Agenda, AgendaCondition } from '@/api/agenda'
import type { PaginatedResponse } from '@/types/common'
import ActionColumn from '@/components/common/ActionColumn.vue'
import { useIsMobile } from '@/composables/useIsMobile'

const { isMobile } = useIsMobile()

const loading = ref(false)
const calendarLoading = ref(false)
const tableData = ref<Agenda[]>([])
const total = ref(0)
const viewMode = ref<'list' | 'calendar'>('list')

const queryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    title: '',
    category: undefined as string | undefined,
    status: undefined as number | undefined,
    start_date: '',
    end_date: ''
  } as AgendaCondition
})

// ---- 映射表 ----
const categoryMap: Record<string, { text: string; type: string }> = {
  work: { text: '工作', type: 'primary' },
  life: { text: '生活', type: 'success' },
  study: { text: '学习', type: 'warning' },
  other: { text: '其他', type: 'info' }
}

const priorityMap: Record<number, { text: string; type: string }> = {
  1: { text: '低', type: 'info' },
  2: { text: '中', type: 'warning' },
  3: { text: '高', type: 'danger' }
}

const statusMap: Record<number, { text: string; type: string }> = {
  0: { text: '待办', type: 'primary' },
  1: { text: '进行中', type: 'warning' },
  2: { text: '已完成', type: 'success' }
}

const categoryOptions = [
  { label: '工作', value: 'work' },
  { label: '生活', value: 'life' },
  { label: '学习', value: 'study' },
  { label: '其他', value: 'other' }
]

const priorityOptions = [
  { label: '低', value: 1 },
  { label: '中', value: 2 },
  { label: '高', value: 3 }
]

const statusOptions = [
  { label: '待办', value: 0 },
  { label: '进行中', value: 1 },
  { label: '已完成', value: 2 }
]

const recurrenceOptions = [
  { label: '不重复', value: 'none' },
  { label: '每日', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' }
]

const colorPresets = ['#409EFF', '#67C23A', '#E6A23C', '#F56C6C', '#909399', '#9B59B6']

function formatDatetime(d: Date | null | undefined): string {
  if (!d) return ''
  const y = d.getFullYear()
  const mo = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  const s = String(d.getSeconds()).padStart(2, '0')
  return `${y}-${mo}-${day} ${h}:${mi}:${s}`
}

function parseDatetime(str: string | null | undefined): Date | null {
  if (!str) return null
  const d = new Date(str.replace(' ', 'T'))
  return isNaN(d.getTime()) ? null : d
}

// ---- 数据加载 ----
async function loadData() {
  loading.value = true
  try {
    const res = await agendaApi.page(queryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<Agenda>
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
  queryParams.condition = {
    title: '',
    category: undefined,
    status: undefined,
    start_date: '',
    end_date: ''
  }
  handleSearch()
}

function handlePageChange() {
  loadData()
}

function handleSizeChange() {
  queryParams.page = 1
  loadData()
}

// ---- 行操作 ----
function getRowActions(row: Agenda) {
  return [
    {
      key: 'complete',
      label: '完成',
      icon: Check,
      btnClass: 'action-success',
      visible: row.status !== 2
    },
    { key: 'edit', label: '编辑', icon: Edit, btnClass: 'action-edit' },
    { key: 'delete', label: '删除', icon: Delete, btnClass: 'action-delete', danger: true }
  ].filter((a) => a.visible !== false)
}

async function onRowAction(row: Agenda, key: string) {
  switch (key) {
    case 'complete':
      await handleComplete(row)
      break
    case 'edit':
      openEditDialog(row)
      break
    case 'delete':
      await handleDelete(row)
      break
  }
}

async function handleComplete(row: Agenda) {
  try {
    await ElMessageBox.confirm(`确认将「${row.title}」标记为已完成？`, '提示', {
      type: 'info'
    })
    const res = await agendaApi.complete(row.id!)
    if (res.data.code === 1) {
      ElMessage.success('已完成')
      refreshAfterChange()
    }
  } catch {
    // 用户取消
  }
}

async function handleDelete(row: Agenda) {
  try {
    await ElMessageBox.confirm(`确认删除日程「${row.title}」？`, '警告', {
      type: 'warning'
    })
    const res = await agendaApi.delete(row.id!)
    if (res.data.code === 1) {
      ElMessage.success('删除成功')
      refreshAfterChange()
    }
  } catch {
    // 用户取消
  }
}

// ---- 新建/编辑 Dialog ----
const dialogVisible = ref(false)
const dialogTitle = ref('新建日程')
const isEdit = ref(false)
const editId = ref<number | null>(null)
const dateRange = ref<[Date, Date] | null>(null)
const remindDate = ref<Date | null>(null)

const form = reactive({
  title: '',
  description: '',
  start_time: '',
  end_time: '',
  category: 'other',
  priority: 2,
  location: '',
  recurrence: 'none',
  status: 0,
  color: '#409EFF',
  remind_at: ''
})

function resetForm() {
  form.title = ''
  form.description = ''
  form.start_time = ''
  form.end_time = ''
  form.category = 'other'
  form.priority = 2
  form.location = ''
  form.recurrence = 'none'
  form.status = 0
  form.color = '#409EFF'
  form.remind_at = ''
  dateRange.value = null
  remindDate.value = null
}

function openCreateDialog() {
  isEdit.value = false
  editId.value = null
  dialogTitle.value = '新建日程'
  resetForm()
  dialogVisible.value = true
}

function openEditDialog(row: Agenda) {
  isEdit.value = true
  editId.value = row.id ?? null
  dialogTitle.value = '编辑日程'
  form.title = row.title ?? ''
  form.description = row.description ?? ''
  form.start_time = row.start_time ?? ''
  form.end_time = row.end_time ?? ''
  form.category = row.category ?? 'other'
  form.priority = row.priority ?? 2
  form.location = row.location ?? ''
  form.recurrence = row.recurrence ?? 'none'
  form.status = row.status ?? 0
  form.color = row.color ?? '#409EFF'
  form.remind_at = row.remind_at ?? ''
  const startD = parseDatetime(row.start_time)
  const endD = parseDatetime(row.end_time)
  dateRange.value = startD && endD ? [startD, endD] : null
  remindDate.value = parseDatetime(row.remind_at)
  dialogVisible.value = true
}

function onDateRangeChange(val: [Date, Date] | null) {
  if (val) {
    form.start_time = formatDatetime(val[0])
    form.end_time = formatDatetime(val[1])
  } else {
    form.start_time = ''
    form.end_time = ''
  }
}

async function handleSubmit() {
  if (!form.title.trim()) {
    ElMessage.warning('请输入标题')
    return
  }
  form.remind_at = formatDatetime(remindDate.value)

  try {
    if (isEdit.value && editId.value) {
      const res = await agendaApi.update({ id: editId.value, ...form })
      if (res.data.code === 1) {
        ElMessage.success('更新成功')
        dialogVisible.value = false
        refreshAfterChange()
      }
    } else {
      const res = await agendaApi.create({ ...form })
      if (res.data.code === 1) {
        ElMessage.success('创建成功')
        dialogVisible.value = false
        refreshAfterChange()
      }
    }
  } catch {
    // API 拦截器已处理错误提示
  }
}

// ---- 日历视图 ----
let calendarApi: any = null

const calendarOptions = reactive<CalendarOptions>({
  plugins: [dayGridPlugin, timeGridPlugin, interactionPlugin],
  initialView: 'dayGridMonth',
  locale: zhCnLocale,
  headerToolbar: {
    left: 'prev,next today',
    center: 'title',
    right: 'dayGridMonth,timeGridWeek,timeGridDay'
  },
  height: 700,
  editable: true,
  dayMaxEvents: 3,
  events: [],
  eventClick: (info: EventClickArg) => {
    const agenda = info.event.extendedProps as Agenda
    if (agenda.id) {
      openEditDialog(agenda)
    }
  },
  datesSet: (info: DateSelectArg) => {
    calendarApi = info.view.calendar
    const start = formatDate(info.startStr)
    const end = formatDate(info.endStr)
    if (start && end) {
      loadCalendarEvents(start, end)
    }
  },
  eventDrop: async (info: EventDropArg) => {
    await handleEventChange(info)
  },
  eventClassNames: (info: { event: { extendedProps: Agenda } }) => {
    return info.event.extendedProps.status === 2 ? ['agenda-completed'] : []
  },
  eventResize: async (info: { event: { extendedProps: Agenda; start: Date | null; end: Date | null; id: string }; revert: () => void }) => {
    await handleEventChange(info)
  },
})

async function handleEventChange(info: { event: { extendedProps: Agenda; start: Date | null; end: Date | null; id: string }; revert: () => void }) {
  const agenda = info.event.extendedProps as Agenda
  if (!agenda.id) return
  const start = info.event.start
  const end = info.event.end
  const updateData: Record<string, any> = { id: agenda.id }
  if (start) updateData.start_time = formatDatetime(start)
  if (end) updateData.end_time = formatDatetime(end)
  try {
    const res = await agendaApi.update(updateData)
    if (res.data.code !== 1) {
      info.revert()
    }
  } catch {
    info.revert()
  }
}

function refreshCalendar() {
  if (calendarApi) {
    const view = calendarApi.view
    const start = formatDate(view.currentStart.toISOString())
    const end = formatDate(view.currentEnd.toISOString())
    loadCalendarEvents(start, end)
  }
}

function refreshAfterChange() {
  loadData()
  if (viewMode.value === 'calendar') {
    refreshCalendar()
  }
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

async function loadCalendarEvents(start_date: string, end_date: string) {
  calendarLoading.value = true
  try {
    const res = await agendaApi.calendarEvents(start_date, end_date)
    if (res.data.code === 1) {
      const items = res.data.data as Agenda[]
      calendarOptions.events = items
        .filter((item) => item.start_time)
        .map((item) => {
          const isMultiDay = item.start_time && item.end_time &&
            item.start_time.slice(0, 10) !== item.end_time.slice(0, 10)
          return {
            id: String(item.id),
            title: item.title || '',
            start: item.start_time,
            end: item.end_time || undefined,
            allDay: isMultiDay || undefined,
            editable: item.status !== 2,
            backgroundColor: item.color || '#409EFF',
            borderColor: item.color || '#409EFF',
            extendedProps: item
          }
        })
    }
  } catch {
    // 忽略加载错误
  } finally {
    calendarLoading.value = false
  }
}

watch(viewMode, (val) => {
  if (val === 'calendar') {
    // 初始加载当前月份数据，datesSet 会接管后续视图切换
    const now = new Date()
    const start = formatDate(now.toISOString())
    const endMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0)
    const end = formatDate(endMonth.toISOString())
    loadCalendarEvents(start, end)
  }
})

onMounted(() => {
  loadData()
})
</script>

<template>
  <div class="agenda-page page">
    <div class="page-header">
      <h1 class="page-title">日程管理</h1>
      <div class="header-actions">
        <el-radio-group v-model="viewMode">
          <el-radio-button value="list">
            <el-icon><List /></el-icon>
            <span style="margin-left: 4px">列表</span>
          </el-radio-button>
          <el-radio-button value="calendar">
            <el-icon><Calendar /></el-icon>
            <span style="margin-left: 4px">日历</span>
          </el-radio-button>
        </el-radio-group>
        <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建日程</el-button>
      </div>
    </div>

    <!-- 列表视图 -->
    <template v-if="viewMode === 'list'">
      <el-form :inline="true" class="search-bar" @submit.prevent="handleSearch">
        <el-form-item label="标题">
          <el-input
            v-model="queryParams.condition.title"
            placeholder="搜索标题"
            clearable
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="分类">
          <el-select
            v-model="queryParams.condition.category"
            placeholder="全部分类"
            clearable
            @change="handleSearch"
          >
            <el-option
              v-for="opt in categoryOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="queryParams.condition.status"
            placeholder="全部状态"
            clearable
            @change="handleSearch"
          >
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

      <div class="card-panel">
        <el-table v-loading="loading" :data="tableData" stripe>
          <el-table-column prop="title" label="标题" min-width="160">
            <template #default="{ row }">
              <div class="agenda-title-cell">
                <span
                  v-if="row.color"
                  class="color-dot"
                  :style="{ backgroundColor: row.color }"
                />
                <span :class="{ 'text-done': row.status === 2 }">{{ row.title }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="category" label="分类" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="categoryMap[row.category]?.type" size="small">
                {{ categoryMap[row.category]?.text || row.category }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="priority" label="优先级" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="priorityMap[row.priority]?.type" size="small" effect="plain">
                {{ priorityMap[row.priority]?.text || '-' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="start_time" label="开始时间" width="170" />
          <el-table-column prop="end_time" label="结束时间" width="170" />
          <el-table-column prop="location" label="地点" min-width="120" show-overflow-tooltip />
          <el-table-column prop="remind_at" label="提醒时间" width="170">
            <template #default="{ row }">
              <span v-if="row.remind_at" :class="{ 'text-muted': row.is_reminded === 1 }">
                {{ row.remind_at }}
              </span>
              <span v-else class="text-muted">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="statusMap[row.status]?.type" size="small">
                {{ statusMap[row.status]?.text || '-' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" :width="isMobile ? 60 : 200" fixed="right">
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
    </template>

    <!-- 日历视图 -->
    <template v-else>
      <div class="card-panel calendar-panel" v-loading="calendarLoading">
        <FullCalendar :options="calendarOptions" />
      </div>
    </template>

    <!-- 新建/编辑 Dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="600px"
      destroy-on-close
    >
      <el-form label-width="90px" size="default">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="输入日程标题" />
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="dateRange"
            type="datetimerange"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            format="YYYY-MM-DD HH:mm"
            style="width: 100%"
            @change="onDateRangeChange"
          />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="form.category" style="width: 100%">
            <el-option
              v-for="opt in categoryOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority" style="width: 100%">
            <el-option
              v-for="opt in priorityOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="form.location" placeholder="输入地点（可选）" />
        </el-form-item>
        <el-form-item label="重复">
          <el-select v-model="form.recurrence" style="width: 100%">
            <el-option
              v-for="opt in recurrenceOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="提醒时间">
          <el-date-picker
            v-model="remindDate"
            type="datetime"
            placeholder="选择提醒时间"
            format="YYYY-MM-DD HH:mm"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="颜色">
          <div class="color-picker-row">
            <el-color-picker v-model="form.color" :predefine="colorPresets" />
            <div class="color-presets">
              <span
                v-for="c in colorPresets"
                :key="c"
                class="color-preset-dot"
                :style="{ backgroundColor: c }"
                @click="form.color = c"
              />
            </div>
          </div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            placeholder="输入备注（可选）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.agenda-title-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.color-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.text-done {
  text-decoration: line-through;
  color: var(--el-text-color-placeholder);
}

.text-muted {
  color: var(--el-text-color-placeholder);
}

.calendar-panel {
  padding: 16px;
}

.calendar-panel :deep(.fc) {
  font-family: inherit;
}

.calendar-panel :deep(.fc-toolbar-title) {
  font-size: 1.1rem;
}

.calendar-panel :deep(.fc-event) {
  cursor: pointer;
  font-size: 0.8rem;
  padding: 1px 2px;
}

.calendar-panel :deep(.fc-event.agenda-completed) {
  opacity: 0.55;
}

.calendar-panel :deep(.fc-event.agenda-completed .fc-event-title) {
  text-decoration: line-through;
}

.color-picker-row {
  display: flex;
  align-items: center;
  gap: 16px;
}

.color-presets {
  display: flex;
  gap: 8px;
}

.color-preset-dot {
  display: inline-block;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  cursor: pointer;
  border: 2px solid transparent;
  transition: border-color 0.2s;
}

.color-preset-dot:hover {
  border-color: var(--el-border-color-darker);
}
</style>
