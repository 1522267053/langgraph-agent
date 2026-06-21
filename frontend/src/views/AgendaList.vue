<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Edit, Delete, Check, Calendar, List } from '@element-plus/icons-vue'
import FullCalendar from '@fullcalendar/vue3'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import zhCnLocale from '@fullcalendar/core/locales/zh-cn'
import type {
  CalendarOptions,
  DateSelectArg,
  EventClickArg,
  EventDropArg
} from '@fullcalendar/core'
import { agendaApi } from '@/api/agenda'
import type { Agenda, AgendaCondition } from '@/api/agenda'

const loading = ref(false)
const calendarLoading = ref(false)
const allAgendas = ref<Agenda[]>([])
const viewMode = ref<'list' | 'calendar'>('list')

const queryParams = reactive({
  condition: {
    title: '',
    category: undefined as string | undefined,
    status: undefined as number | undefined
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
  { label: '仅工作日', value: 'weekday' },
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

// ---- 日期分组工具 ----

function getToday(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function getTomorrow(): string {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function getWeekRange(): { start: string; end: string } {
  const now = new Date()
  const dayOfWeek = now.getDay()
  const mondayOffset = dayOfWeek === 0 ? -6 : 1 - dayOfWeek
  const monday = new Date(now)
  monday.setDate(now.getDate() + mondayOffset)
  const sunday = new Date(monday)
  sunday.setDate(monday.getDate() + 6)
  const fmt = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return { start: fmt(monday), end: fmt(sunday) }
}

function getNextWeekRange(): { start: string; end: string } {
  const now = new Date()
  const dayOfWeek = now.getDay()
  const mondayOffset = dayOfWeek === 0 ? -6 : 1 - dayOfWeek
  const nextMonday = new Date(now)
  nextMonday.setDate(now.getDate() + mondayOffset + 7)
  const nextSunday = new Date(nextMonday)
  nextSunday.setDate(nextMonday.getDate() + 6)
  const fmt = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return { start: fmt(nextMonday), end: fmt(nextSunday) }
}

function formatDateLabel(dateStr: string): string {
  if (!dateStr) return ''
  const d = new Date(dateStr.replace(' ', 'T'))
  const month = d.getMonth() + 1
  const day = d.getDate()
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  const wd = weekdays[d.getDay()]
  return `${month}月${day}日 ${wd}`
}

function getDateOnly(dateStr: string | null | undefined): string {
  if (!dateStr) return ''
  return dateStr.slice(0, 10)
}

const groupedAgendas = computed(() => {
  const today = getToday()
  const tomorrow = getTomorrow()
  const thisWeek = getWeekRange()
  const nextWeek = getNextWeekRange()

  const groups: { key: string; label: string; items: Agenda[] }[] = [
    { key: 'today', label: `今天 ${formatDateLabel(today)}`, items: [] },
    { key: 'tomorrow', label: `明天 ${formatDateLabel(tomorrow)}`, items: [] },
    { key: 'this_week', label: `本周 (${formatDateLabel(thisWeek.start)}-${formatDateLabel(thisWeek.end)})`, items: [] },
    { key: 'next_week', label: `下周 (${formatDateLabel(nextWeek.start)}-${formatDateLabel(nextWeek.end)})`, items: [] },
    { key: 'future', label: '未来', items: [] },
    { key: 'earlier', label: '更早', items: [] },
    { key: 'no_date', label: '未设置时间', items: [] }
  ]

  for (const item of allAgendas.value) {
    const dateOnly = getDateOnly(item.start_time)
    if (!dateOnly) {
      groups[6].items.push(item)
    } else if (dateOnly === today) {
      groups[0].items.push(item)
    } else if (dateOnly === tomorrow) {
      groups[1].items.push(item)
    } else if (dateOnly >= thisWeek.start && dateOnly <= thisWeek.end) {
      groups[2].items.push(item)
    } else if (dateOnly >= nextWeek.start && dateOnly <= nextWeek.end) {
      groups[3].items.push(item)
    } else if (dateOnly < today) {
      groups[5].items.push(item)
    } else {
      groups[4].items.push(item)
    }
  }

  for (const g of groups) {
    g.items.sort((a, b) => {
      if (!a.start_time && !b.start_time) return 0
      if (!a.start_time) return 1
      if (!b.start_time) return -1
      return a.start_time.localeCompare(b.start_time)
    })
  }

  return groups.filter(g => g.items.length > 0)
})

// ---- 数据加载 ----
async function loadData() {
  loading.value = true
  try {
    // 加载前后各 3 个月的数据
    const now = new Date()
    const startD = new Date(now.getFullYear(), now.getMonth() - 3, 1)
    const endD = new Date(now.getFullYear(), now.getMonth() + 4, 0)
    const fmt = (d: Date) =>
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    const res = await agendaApi.calendarEvents(fmt(startD), fmt(endD))
    if (res.data.code === 1) {
      let items = res.data.data as Agenda[]
      // 客户端过滤
      if (queryParams.condition.title) {
        const q = queryParams.condition.title.toLowerCase()
        items = items.filter(i => i.title?.toLowerCase().includes(q))
      }
      if (queryParams.condition.category) {
        items = items.filter(i => i.category === queryParams.condition.category)
      }
      if (queryParams.condition.status !== undefined && queryParams.condition.status !== null) {
        items = items.filter(i => i.status === queryParams.condition.status)
      }
      allAgendas.value = items
    }
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  loadData()
}

function handleReset() {
  queryParams.condition = {
    title: '',
    category: undefined,
    status: undefined
  }
  loadData()
}

// ---- 行操作 ----
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
  eventResize: async (info: {
    event: { extendedProps: Agenda; start: Date | null; end: Date | null; id: string }
    revert: () => void
  }) => {
    await handleEventChange(info)
  }
})

async function handleEventChange(info: {
  event: { extendedProps: Agenda; start: Date | null; end: Date | null; id: string }
  revert: () => void
}) {
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
        .filter(item => item.start_time)
        .map(item => {
          const isMultiDay =
            item.start_time &&
            item.end_time &&
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

watch(viewMode, val => {
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

      <div v-loading="loading" class="card-panel agenda-list-panel">
        <template v-if="groupedAgendas.length === 0">
          <el-empty description="暂无日程" />
        </template>
        <template v-else>
          <div v-for="group in groupedAgendas" :key="group.key" class="agenda-group">
            <div class="group-header">
              <span class="group-label">{{ group.label }}</span>
              <span class="group-count">{{ group.items.length }} 项</span>
            </div>
            <div
              v-for="item in group.items"
              :key="item.id"
              class="agenda-card"
              :class="{ 'is-done': item.status === 2 }"
            >
              <div class="card-left">
                <span
                  v-if="item.color"
                  class="color-dot"
                  :style="{ backgroundColor: item.color }"
                />
                <div class="card-info">
                  <div class="card-title" :class="{ 'text-done': item.status === 2 }">
                    {{ item.title }}
                    <span
                      v-if="item.recurrence && item.recurrence !== 'none'"
                      class="recurrence-badge"
                    >{{ recurrenceOptions.find(o => o.value === item.recurrence)?.label }}</span>
                  </div>
                  <div class="card-meta">
                    <span v-if="item.start_time" class="meta-time">
                      <template v-if="item.end_time && getDateOnly(item.start_time) !== getDateOnly(item.end_time)">
                        {{ formatDateLabel(item.start_time) }} {{ item.start_time.slice(11, 16) }} → {{ formatDateLabel(item.end_time) }} {{ item.end_time.slice(11, 16) }}
                      </template>
                      <template v-else-if="item.end_time">
                        <template v-if="group.key === 'today' || group.key === 'tomorrow'">
                          {{ item.start_time.slice(11, 16) }}-{{ item.end_time.slice(11, 16) }}
                        </template>
                        <template v-else>
                          {{ formatDateLabel(item.start_time) }} {{ item.start_time.slice(11, 16) }}-{{ item.end_time.slice(11, 16) }}
                        </template>
                      </template>
                      <template v-else>
                        <template v-if="group.key === 'today' || group.key === 'tomorrow'">
                          {{ item.start_time.slice(11, 16) }}
                        </template>
                        <template v-else>
                          {{ formatDateLabel(item.start_time) }} {{ item.start_time.slice(11, 16) }}
                        </template>
                      </template>
                    </span>
                    <span v-if="item.location" class="meta-location">{{ item.location }}</span>
                    <span v-if="item.remind_at" class="meta-remind">
                      提醒
                      <template v-if="getDateOnly(item.remind_at) !== getDateOnly(item.start_time)">
                        {{ formatDateLabel(item.remind_at) }} {{ item.remind_at.slice(11, 16) }}
                      </template>
                      <template v-else-if="group.key === 'today' || group.key === 'tomorrow'">
                        {{ item.remind_at.slice(11, 16) }}
                      </template>
                      <template v-else>
                        {{ formatDateLabel(item.remind_at) }} {{ item.remind_at.slice(11, 16) }}
                      </template>
                    </span>
                  </div>
                </div>
              </div>
              <div class="card-right">
                <div class="card-tags">
                  <el-tag
                    v-if="item.category"
                    :type="categoryMap[item.category]?.type"
                    size="small"
                  >
                    {{ categoryMap[item.category]?.text || item.category }}
                  </el-tag>
                  <el-tag
                    v-if="item.priority"
                    :type="priorityMap[item.priority]?.type"
                    size="small"
                    effect="plain"
                  >
                    {{ priorityMap[item.priority]?.text }}
                  </el-tag>
                  <el-tag
                    :type="statusMap[item.status ?? 0]?.type"
                    size="small"
                  >
                    {{ statusMap[item.status ?? 0]?.text }}
                  </el-tag>
                </div>
                <div class="card-actions">
                  <el-button
                    v-if="item.status !== 2"
                    text
                    size="small"
                    type="success"
                    :icon="Check"
                    @click="handleComplete(item)"
                  />
                  <el-button
                    text
                    size="small"
                    type="primary"
                    :icon="Edit"
                    @click="openEditDialog(item)"
                  />
                  <el-button
                    text
                    size="small"
                    type="danger"
                    :icon="Delete"
                    @click="handleDelete(item)"
                  />
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </template>

    <!-- 日历视图 -->
    <template v-else>
      <div v-loading="calendarLoading" class="card-panel calendar-panel">
        <FullCalendar :options="calendarOptions" />
      </div>
    </template>

    <!-- 新建/编辑 Dialog -->
    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="600px" destroy-on-close>
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
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width: 100%">
            <el-option
              v-for="opt in statusOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
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

.color-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
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

.calendar-panel :deep(.fc-day-sat),
.calendar-panel :deep(.fc-day-sun) {
  background-color: #fafafa;
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

/* ---- 日期分组卡片布局 ---- */
.agenda-list-panel {
  padding: 0;
  overflow-y: auto;
  max-height: calc(100vh - 200px);
}

.agenda-group {
  margin-bottom: 4px;
}

.agenda-group:last-child {
  margin-bottom: 0;
}

.group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px 8px;
  border-bottom: 1px solid var(--el-border-color-light);
  background: var(--el-fill-color-lighter);
  position: sticky;
  top: 0;
  z-index: 1;
}

.group-label {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.group-count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.agenda-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
  transition: background-color 0.15s;
  gap: 12px;
}

.agenda-card:hover {
  background-color: var(--el-fill-color-light);
}

.agenda-card.is-done {
  opacity: 0.6;
}

.agenda-card:last-child {
  border-bottom: none;
}

.card-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.card-info {
  flex: 1;
  min-width: 0;
}

.card-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 6px;
}

.recurrence-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 400;
  color: #0ea5e9;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  border-radius: 4px;
  padding: 0 6px;
  line-height: 18px;
  flex-shrink: 0;
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.meta-time {
  font-family: monospace;
}

.meta-location::before {
  content: '📍';
  margin-right: 2px;
}

.meta-remind {
  color: #f59e0b;
}

.meta-remind::before {
  content: '🔔';
  margin-right: 2px;
}

.card-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.card-tags {
  display: flex;
  align-items: center;
  gap: 4px;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}

.agenda-card:hover .card-actions {
  opacity: 1;
}

@media (max-width: 768px) {
  .card-right {
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
  }

  .card-actions {
    opacity: 1;
  }
}
</style>
