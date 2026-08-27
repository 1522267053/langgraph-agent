<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute } from 'vue-router'
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
import { useIsMobile } from '@/composables/useIsMobile'

const { isMobile } = useIsMobile()
/** 弹窗宽度：手机端近全屏，避免表单被压缩 */
const dialogWidth = computed(() => (isMobile.value ? '94%' : '600px'))

const loading = ref(false)
const calendarLoading = ref(false)
const allAgendas = ref<Agenda[]>([])
const viewMode = ref<'list' | 'calendar'>('list')

// ---- Tab 角标计数（今日和未来 / 未完成，均仅统计未完成日程） ----
const tabCounts = ref({ upcoming: 0, incomplete: 0 })

const route = useRoute()
// 支持深链 ?tab=incomplete 直接落到对应 Tab
const validTabs = ['upcoming', 'incomplete', 'history'] as const
const initialTab = validTabs.includes(route.query.tab as (typeof validTabs)[number])
  ? (route.query.tab as 'upcoming' | 'incomplete' | 'history')
  : 'upcoming'
const listTab = ref<'upcoming' | 'incomplete' | 'history'>(initialTab)

// ---- 批量选择 ----
const selectedIds = ref<Set<number>>(new Set())

// ---- 滚动加载（游标分页，后端返回 next_cursor 跳过空白间隙） ----
const loadingMore = ref(false)
const hasMore = ref(true)
const sentinelEl = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | null = null

// 游标：下一次请求的起始日期
const cursorDate = ref('')

function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 按当前 tab 决定加载方向与状态过滤 */
function loadPage(cursor: string) {
  const direction = listTab.value === 'upcoming' ? 'forward' : 'backward'
  const statusFilter = listTab.value === 'incomplete' ? [0, 1] : undefined
  return agendaApi.loadMore(cursor, direction, statusFilter)
}

function clientFilter(items: Agenda[]): Agenda[] {
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
  return items
}

async function loadData() {
  selectedIds.value = new Set()
  cursorDate.value = fmtDate(new Date())
  hasMore.value = true
  loading.value = true
  try {
    const res = await loadPage(cursorDate.value)
    if (res.data.code === 1) {
      const { items, next_cursor } = res.data.data
      allAgendas.value = clientFilter(items)
      if (next_cursor) cursorDate.value = next_cursor
      else hasMore.value = false
    }
  } finally {
    loading.value = false
    // loadData 完成后，若 sentinel 仍可见（内容不足以撑满容器），
    // 重新 observe 以触发一次 loadMore 探测下一窗口
    await nextTick()
    if (sentinelEl.value && observer) {
      observer.unobserve(sentinelEl.value)
      observer.observe(sentinelEl.value)
    }
  }
}

async function loadMore() {
  if (loadingMore.value || !hasMore.value || loading.value) return
  loadingMore.value = true
  try {
    const res = await loadPage(cursorDate.value)
    if (res.data.code === 1) {
      const { items, next_cursor } = res.data.data
      const filtered = clientFilter(items)
      allAgendas.value = [...allAgendas.value, ...filtered]
      if (next_cursor) {
        cursorDate.value = next_cursor
        if (filtered.length === 0) {
          // 空窗口但还有数据：sentinel 位置不变，需手动 re-observe 触发下一轮
          await nextTick()
          if (sentinelEl.value && observer) {
            observer.unobserve(sentinelEl.value)
            observer.observe(sentinelEl.value)
          }
        }
      } else {
        hasMore.value = false
      }
    }
  } finally {
    loadingMore.value = false
  }
}

function setupObserver() {
  if (observer) observer.disconnect()
  observer = new IntersectionObserver(
    entries => {
      if (entries[0].isIntersecting) {
        loadMore()
      }
    },
    { rootMargin: '200px' }
  )
  if (sentinelEl.value) {
    observer.observe(sentinelEl.value)
  }
}

watch(
  () => sentinelEl.value,
  val => {
    if (val) {
      setupObserver()
    }
  }
)

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
    {
      key: 'this_week',
      label: `本周 (${formatDateLabel(thisWeek.start)}-${formatDateLabel(thisWeek.end)})`,
      items: []
    },
    {
      key: 'next_week',
      label: `下周 (${formatDateLabel(nextWeek.start)}-${formatDateLabel(nextWeek.end)})`,
      items: []
    },
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
    } else if (dateOnly < today) {
      groups[5].items.push(item)
    } else if (dateOnly >= thisWeek.start && dateOnly <= thisWeek.end) {
      groups[2].items.push(item)
    } else if (dateOnly >= nextWeek.start && dateOnly <= nextWeek.end) {
      groups[3].items.push(item)
    } else {
      groups[4].items.push(item)
    }
  }

  for (const g of groups) {
    if (g.key === 'earlier') {
      // 更早：倒序（最新的在最前面）
      g.items.sort((a, b) => {
        if (!a.start_time && !b.start_time) return 0
        if (!a.start_time) return 1
        if (!b.start_time) return -1
        return b.start_time.localeCompare(a.start_time)
      })
    } else {
      // 其他分组：正序
      g.items.sort((a, b) => {
        if (!a.start_time && !b.start_time) return 0
        if (!a.start_time) return 1
        if (!b.start_time) return -1
        return a.start_time.localeCompare(b.start_time)
      })
    }
  }

  return groups.filter(g => g.items.length > 0)
})

const filteredGroups = computed(() => {
  const groups = groupedAgendas.value
  if (listTab.value === 'upcoming') {
    // 今日和未来：显示所有"未结束"的日程；进行中的（start 已过、end 未到）单独置顶
    const now = new Date()
    const todayStr = getToday()
    const isNotEnded = (item: Agenda): boolean => {
      const refStr = item.end_time || item.start_time
      if (!refStr) return true
      return new Date(refStr.replace(' ', 'T')) >= now
    }
    const isOngoing = (item: Agenda): boolean => {
      if (!item.start_time) return false
      if (getDateOnly(item.start_time) >= todayStr) return false
      return isNotEnded(item)
    }

    const ongoing: Agenda[] = []
    const visible: { key: string; label: string; items: Agenda[] }[] = []
    for (const g of groups) {
      const live: Agenda[] = []
      for (const item of g.items) {
        if (!isNotEnded(item)) continue
        if (isOngoing(item)) ongoing.push(item)
        else live.push(item)
      }
      if (live.length > 0) visible.push({ ...g, items: live })
    }
    const result: { key: string; label: string; items: Agenda[] }[] = []
    if (ongoing.length > 0) {
      ongoing.sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''))
      result.push({ key: 'ongoing', label: '进行中', items: ongoing })
    }
    result.push(...visible)
    return result
  } else if (listTab.value === 'incomplete') {
    // 未完成：今日已过期（按当前时刻精确判断）+ earlier 分组
    // 有效结束时刻 = end_time ?? start_time（无 end_time 回退 start_time），
    // 使"会议进行中（start 已过、end 未过）"不被误判为已过期
    const now = new Date()
    const isOverdue = (item: Agenda): boolean => {
      const refStr = item.end_time || item.start_time
      if (!refStr) return false
      return new Date(refStr.replace(' ', 'T')) < now
    }
    const result: { key: string; label: string; items: Agenda[] }[] = []
    const todayGroup = groups.find(g => g.key === 'today')
    const overdueTodayItems = (todayGroup?.items || []).filter(isOverdue)
    if (overdueTodayItems.length > 0) {
      result.push({ key: 'overdue_today', label: '今日已过期', items: overdueTodayItems })
    }
    const earlier = groups.find(g => g.key === 'earlier')
    const earlierOverdueItems = (earlier?.items || []).filter(isOverdue)
    if (earlierOverdueItems.length > 0) {
      result.push({ key: 'earlier', label: '更早', items: earlierOverdueItems })
    }
    return result
  } else {
    // history：查看以前，同样按有效结束时刻判断（包含所有状态）
    const now = new Date()
    const isEnded = (item: Agenda): boolean => {
      const refStr = item.end_time || item.start_time
      if (!refStr) return false
      return new Date(refStr.replace(' ', 'T')) < now
    }
    const result: { key: string; label: string; items: Agenda[] }[] = []
    const todayGroup = groups.find(g => g.key === 'today')
    const endedTodayItems = (todayGroup?.items || []).filter(isEnded)
    if (endedTodayItems.length > 0) {
      result.push({ key: 'ended_today', label: '今日已结束', items: endedTodayItems })
    }
    const earlier = groups.find(g => g.key === 'earlier')
    const earlierEndedItems = (earlier?.items || []).filter(isEnded)
    if (earlierEndedItems.length > 0) {
      result.push({ key: 'earlier', label: '更早', items: earlierEndedItems })
    }
    return result
  }
})

// ---- 批量选择 ----
const flatItems = computed(() => filteredGroups.value.flatMap(g => g.items))
const selectableIds = computed(() => flatItems.value.filter(i => i.id != null).map(i => i.id!))
const allSelected = computed(
  () => selectableIds.value.length > 0 && selectableIds.value.every(id => selectedIds.value.has(id))
)
const someSelected = computed(() => selectableIds.value.some(id => selectedIds.value.has(id)))
const hasUncompletedSelected = computed(() =>
  flatItems.value.some(i => i.id != null && i.status !== 2 && selectedIds.value.has(i.id))
)

function toggleItem(item: Agenda) {
  if (item.id == null) return
  const s = new Set(selectedIds.value)
  if (s.has(item.id)) s.delete(item.id)
  else s.add(item.id)
  selectedIds.value = s
}

function toggleSelectAll(val: boolean) {
  selectedIds.value = val ? new Set(selectableIds.value) : new Set()
}

async function handleBatchComplete() {
  const ids = [...selectedIds.value]
  if (ids.length === 0) return
  try {
    await ElMessageBox.confirm(`确认将选中的 ${ids.length} 项标记为已完成？`, '批量完成', {
      type: 'info'
    })
    const res = await agendaApi.batchComplete(ids)
    if (res.data.code === 1) {
      ElMessage.success({ message: res.data.msg || '批量完成成功', duration: 5000 })
      selectedIds.value = new Set()
      refreshAfterChange()
    }
  } catch {
    // 用户取消
  }
}

async function handleBatchDelete() {
  const ids = [...selectedIds.value]
  if (ids.length === 0) return
  try {
    await ElMessageBox.confirm(`确认删除选中的 ${ids.length} 项日程？`, '批量删除', {
      type: 'warning'
    })
    const res = await agendaApi.batchDelete(ids)
    if (res.data.code === 1) {
      ElMessage.success({ message: '批量删除成功', duration: 5000 })
      selectedIds.value = new Set()
      refreshAfterChange()
    }
  } catch {
    // 用户取消
  }
}

function handleSearch() {
  listTab.value = 'upcoming'
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
      ElMessage.success({ message: '已完成', duration: 5000 })
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
      ElMessage.success({ message: '删除成功', duration: 5000 })
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
const dialogDateRange = ref<[Date, Date] | null>(null)
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
  dialogDateRange.value = null
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
  dialogDateRange.value = startD && endD ? [startD, endD] : null
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
    ElMessage.warning({ message: '请输入标题', duration: 5000 })
    return
  }
  form.remind_at = formatDatetime(remindDate.value)

  const payload = { ...form } as Record<string, unknown>
  // datetime 字段未填写时为空字符串，转为 null 避免后端校验失败
  for (const key of ['start_time', 'end_time', 'remind_at']) {
    if (payload[key] === '') payload[key] = null
  }

  try {
    if (isEdit.value && editId.value) {
      const res = await agendaApi.update({ id: editId.value, ...(payload as Partial<Agenda>) })
      if (res.data.code === 1) {
        ElMessage.success({ message: '更新成功', duration: 5000 })
        dialogVisible.value = false
        refreshAfterChange()
      }
    } else {
      const res = await agendaApi.create(payload as Partial<Agenda>)
      if (res.data.code === 1) {
        ElMessage.success({ message: '创建成功', duration: 5000 })
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

// 手机端默认周视图 + 更矮的高度，避免月视图格子过挤（setup 时一次性判断）
const isSmallScreen = window.matchMedia('(max-width: 767px)').matches

const calendarOptions = reactive<CalendarOptions>({
  plugins: [dayGridPlugin, timeGridPlugin, interactionPlugin],
  initialView: isSmallScreen ? 'timeGridWeek' : 'dayGridMonth',
  locale: zhCnLocale,
  headerToolbar: {
    left: 'prev,next today',
    center: 'title',
    right: 'dayGridMonth,timeGridWeek,timeGridDay'
  },
  height: isSmallScreen ? 520 : 700,
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
  loadTabCounts()
  if (viewMode.value === 'calendar') {
    refreshCalendar()
  }
}

/** 角标数字格式化：超过 99 显示 99+ */
function formatCount(n: number): string {
  return n > 99 ? '99+' : String(n)
}

/** 加载 Tab 角标计数（不跟随搜索条件，始终为各 Tab 未完成总数） */
async function loadTabCounts() {
  try {
    const res = await agendaApi.tabCounts()
    if (res.data.code === 1) {
      tabCounts.value = res.data.data as { upcoming: number; incomplete: number }
    }
  } catch {
    // 静默失败
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
    // datesSet 回调会在日历初始化时自动加载
  }
})

watch(listTab, () => {
  loadData()
})

onMounted(() => {
  loadData()
  loadTabCounts()
})

onBeforeUnmount(() => {
  if (observer) observer.disconnect()
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
        <div class="list-tabs">
          <div
            class="list-tab-item"
            :class="{ active: listTab === 'upcoming' }"
            @click="listTab = 'upcoming'"
          >
            <span class="tab-icon">📋</span>
            <span>今日和未来</span>
            <span v-if="tabCounts.upcoming > 0" class="tab-count-badge">
              {{ formatCount(tabCounts.upcoming) }}
            </span>
          </div>
          <div
            class="list-tab-item"
            :class="{ active: listTab === 'incomplete' }"
            @click="listTab = 'incomplete'"
          >
            <span class="tab-icon">⏳</span>
            <span>未完成</span>
            <span v-if="tabCounts.incomplete > 0" class="tab-count-badge">
              {{ formatCount(tabCounts.incomplete) }}
            </span>
          </div>
          <div
            class="list-tab-item"
            :class="{ active: listTab === 'history' }"
            @click="listTab = 'history'"
          >
            <span class="tab-icon">📂</span>
            <span>查看以前</span>
          </div>
        </div>
        <div v-if="flatItems.length > 0" class="batch-bar">
          <el-checkbox
            :model-value="allSelected"
            :indeterminate="someSelected && !allSelected"
            @change="toggleSelectAll"
          >
            全选
          </el-checkbox>
          <template v-if="selectedIds.size > 0">
            <span class="batch-count">已选 {{ selectedIds.size }} 项</span>
            <el-tooltip
              :disabled="hasUncompletedSelected"
              content="所选日程均已完成，无需重复操作"
              placement="top"
            >
              <el-button
                size="small"
                type="success"
                :icon="Check"
                :disabled="!hasUncompletedSelected"
                @click="handleBatchComplete"
              >
                批量完成
              </el-button>
            </el-tooltip>
            <el-button size="small" type="danger" :icon="Delete" @click="handleBatchDelete">
              批量删除
            </el-button>
          </template>
        </div>
        <template v-if="filteredGroups.length === 0">
          <el-empty description="暂无日程" />
        </template>
        <template v-else>
          <div v-for="group in filteredGroups" :key="group.key" class="agenda-group">
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
                <el-checkbox
                  class="card-check"
                  :model-value="selectedIds.has(item.id!)"
                  @change="toggleItem(item)"
                />
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
                    >
                      {{ recurrenceOptions.find(o => o.value === item.recurrence)?.label }}
                    </span>
                  </div>
                  <div class="card-meta">
                    <span v-if="item.start_time" class="meta-time">
                      <template
                        v-if="
                          item.end_time &&
                          getDateOnly(item.start_time) !== getDateOnly(item.end_time)
                        "
                      >
                        {{ formatDateLabel(item.start_time) }} {{ item.start_time.slice(11, 16) }} →
                        {{ formatDateLabel(item.end_time) }} {{ item.end_time.slice(11, 16) }}
                      </template>
                      <template v-else-if="item.end_time">
                        <template v-if="group.key === 'today' || group.key === 'tomorrow'">
                          {{ item.start_time.slice(11, 16) }}-{{ item.end_time.slice(11, 16) }}
                        </template>
                        <template v-else>
                          {{ formatDateLabel(item.start_time) }}
                          {{ item.start_time.slice(11, 16) }}-{{ item.end_time.slice(11, 16) }}
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
                  <el-tag :type="statusMap[item.status ?? 0]?.type" size="small">
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
        <div ref="sentinelEl" class="list-sentinel" :class="{ 'is-loading': loadingMore }">
          <span v-if="loadingMore">加载更多...</span>
          <span v-else-if="!hasMore && filteredGroups.length > 0">已加载全部</span>
        </div>
      </div>
    </template>

    <!-- 日历视图 -->
    <template v-else>
      <div v-loading="calendarLoading" class="card-panel calendar-panel">
        <FullCalendar :options="calendarOptions" />
      </div>
    </template>

    <!-- 新建/编辑 Dialog -->
    <el-dialog v-model="dialogVisible" :title="dialogTitle" :width="dialogWidth" destroy-on-close>
      <el-form label-width="90px" size="default">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="输入日程标题" />
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="dialogDateRange"
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

.list-tabs {
  display: flex;
  border-bottom: 1px solid var(--el-border-color-light);
  background: #fff;
  position: sticky;
  top: 0;
  z-index: 2;
}

.list-tab-item {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  transition: all 0.2s;
  border-bottom: 2px solid transparent;
  user-select: none;
}

.list-tab-item:hover {
  color: var(--el-text-color-primary);
  background: var(--el-fill-color-lighter);
}

.list-tab-item.active {
  color: var(--el-color-primary);
  border-bottom-color: var(--el-color-primary);
  background: transparent;
}

.tab-icon {
  font-size: 16px;
}

.tab-count-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  margin-left: 2px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  color: #fff;
  background: var(--el-color-danger);
  border-radius: 9px;
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

.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
  background-color: var(--el-fill-color-lighter);
}

.batch-count {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.card-check {
  flex-shrink: 0;
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
  /* 头部操作区允许换行 */
  .header-actions {
    width: 100%;
    flex-wrap: wrap;
    gap: 8px;
  }

  /* 搜索栏改为逐行堆叠，输入控件占满整行 */
  .search-bar {
    padding: 12px;
  }

  .search-bar :deep(.el-form-item) {
    display: flex;
    width: 100%;
    margin-right: 0;
    margin-bottom: 10px;
  }

  .search-bar :deep(.el-form-item:last-child) {
    margin-bottom: 0;
  }

  .search-bar :deep(.el-form-item__content),
  .search-bar :deep(.el-input),
  .search-bar :deep(.el-select) {
    width: 100%;
  }

  /* 列表面板取消内部滚动，改为随页面自然滚动（与全局 card-panel 移动端约定一致） */
  .agenda-list-panel {
    max-height: none;
    overflow: visible;
  }

  /* Tab 栏紧凑化：隐藏 emoji 图标、缩小内边距与字号 */
  .list-tab-item {
    padding: 10px 4px;
    font-size: 13px;
    gap: 3px;
  }

  .tab-icon {
    display: none;
  }

  /* 批量栏、分组头允许换行 */
  .batch-bar {
    flex-wrap: wrap;
    row-gap: 6px;
    padding: 8px 12px;
  }

  .group-header {
    flex-wrap: wrap;
    gap: 2px 8px;
    padding: 10px 12px 6px;
  }

  /* 卡片：标题放开单行截断、元信息自动折行，避免文字挤压 */
  .agenda-card {
    align-items: flex-start;
    padding: 10px 12px;
    gap: 8px;
  }

  .card-left {
    gap: 8px;
  }

  .card-title {
    white-space: normal;
    word-break: break-word;
    flex-wrap: wrap;
  }

  .card-meta {
    flex-wrap: wrap;
    gap: 2px 10px;
  }

  .meta-time {
    font-family: inherit;
    white-space: normal;
  }

  .card-right {
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
  }

  .card-actions {
    opacity: 1;
  }

  /* 日历视图紧凑化：工具栏纵向堆叠、缩小标题与按钮尺寸 */
  .calendar-panel {
    padding: 8px;
  }

  .calendar-panel :deep(.fc-toolbar.fc-header-toolbar) {
    flex-direction: column;
    gap: 6px;
  }

  .calendar-panel :deep(.fc-toolbar-title) {
    font-size: 1rem;
  }

  .calendar-panel :deep(.fc-button) {
    padding: 2px 8px;
    font-size: 12px;
  }

  .calendar-panel :deep(.fc-event) {
    font-size: 11px;
  }
}

.list-sentinel {
  text-align: center;
  padding: 16px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.list-sentinel.is-loading {
  color: var(--el-color-primary);
}
</style>
