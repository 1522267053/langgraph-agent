import { ref, h } from 'vue'
import { ElNotification, ElButton, ElMessage } from 'element-plus'
import { agendaApi } from '@/api/agenda'
import { notify, isDenied } from '@/composables/useBrowserNotification'

interface ExecutionDoneData {
  execution_id: number | null
  flow_id: number | null
  flow_name: string
  status: 'success' | 'failed' | 'cancelled'
  source: 'flow' | 'agent'
  error_message?: string | null
  duration_ms?: number | null
  last_user_message?: string | null
}

interface AgendaReminderData {
  agenda_id: number
  title: string
  description?: string | null
  start_time?: string | null
  location?: string | null
}

interface WSMessage {
  type: 'execution_done' | 'agenda_reminder'
  browser_notify?: boolean
  data: ExecutionDoneData | AgendaReminderData
}

const ws = ref<WebSocket | null>(null)
const connected = ref(false)
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
let reconnectAttempts = 0
const MAX_RECONNECT_ATTEMPTS = 10
const MAX_RECONNECT_DELAY = 30000

/** 当前正在通过 SSE 观看的 execution_id（用于跳过重复通知） */
let watchingExecutionId: number | null = null
let deniedNotified = false

export function setWatchingExecution(id: number | null) {
  watchingExecutionId = id
}

function buildWsUrl(): string {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${location.host}/ws/notifications`
}

function handleNotification(msg: WSMessage) {
  if (msg.type === 'execution_done') {
    const data = msg.data as ExecutionDoneData
    const { flow_name, status, source, execution_id, last_user_message } = data

    // 跳过用户正在通过 SSE 观看的执行（避免重复弹窗）
    const isWatching =
      execution_id !== null &&
      watchingExecutionId !== null &&
      execution_id === watchingExecutionId

    const typeLabel = source === 'agent' ? '对话' : '流程'

    function truncate(str: string, max = 50): string {
      return str.length > max ? str.slice(0, max) + '...' : str
    }

    // 浏览器通知权限被拒时提示一次
    if (msg.browser_notify !== false && isDenied() && !deniedNotified) {
      deniedNotified = true
      ElMessage.warning('浏览器通知权限已被拒绝，请在浏览器设置中允许通知以接收桌面通知')
    }

    if (status === 'success') {
      const msgPreview = last_user_message
        ? `「${flow_name}：${truncate(last_user_message)}」执行完成`
        : `「${flow_name}」执行完成`
      if (!isWatching) {
        ElNotification({
          type: 'success',
          title: `${typeLabel}完成`,
          message: msgPreview,
          duration: 5000,
          position: 'top-right'
        })
      }
      if (msg.browser_notify !== false) {
        notify(`${typeLabel}完成`, { body: msgPreview, icon: '/logo.ico' })
      }
    } else if (status === 'failed') {
      const msgPreview = last_user_message
        ? `「${flow_name}：${truncate(last_user_message)}」执行失败`
        : `「${flow_name}」执行失败`
      if (!isWatching) {
        ElNotification({
          type: 'error',
          title: `${typeLabel}失败`,
          message: msgPreview,
          duration: 0,
          position: 'top-right'
        })
      }
      if (msg.browser_notify !== false) {
        notify(`${typeLabel}失败`, { body: msgPreview, icon: '/logo.ico' })
      }
    }
  } else if (msg.type === 'agenda_reminder') {
    const data = msg.data as AgendaReminderData
    const parts: string[] = []
    if (data.start_time) parts.push(`时间: ${data.start_time}`)
    if (data.location) parts.push(`地点: ${data.location}`)
    if (data.description) parts.push(data.description)

    const noti = ElNotification({
      type: 'warning',
      title: `日程提醒: ${data.title}`,
      message: h('div', [
        h('p', { style: 'margin: 4px 0 12px' }, parts.join(' | ') || '该日程开始了'),
        h('div', { style: 'display: flex; gap: 8px' }, [
          h(
            ElButton,
            {
              type: 'primary',
              size: 'small',
              onClick: async () => {
                try {
                  await agendaApi.complete(data.agenda_id)
                  noti.close()
                } catch {
                  // ignore
                }
              }
            },
            () => '完成'
          ),
          h(
            ElButton,
            {
              size: 'small',
              onClick: async () => {
                try {
                  await agendaApi.postpone(data.agenda_id)
                  noti.close()
                } catch {
                  // ignore
                }
              }
            },
            () => '延后15分钟'
          )
        ])
      ]),
      duration: 0,
      position: 'top-right'
    })
    if (msg.browser_notify !== false) {
      notify(`日程提醒: ${data.title}`, {
        body: parts.join(' | ') || '该日程开始了',
        icon: '/logo.ico'
      })
    }
  }
}

function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (ws.value?.readyState === WebSocket.OPEN) {
      try {
        ws.value.send('ping')
      } catch {
        // ignore
      }
    }
  }, 30000)
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

function connect() {
  if (ws.value?.readyState === WebSocket.OPEN || ws.value?.readyState === WebSocket.CONNECTING) {
    return
  }

  try {
    const socket = new WebSocket(buildWsUrl())

    socket.onopen = () => {
      connected.value = true
      reconnectAttempts = 0
      startHeartbeat()
    }

    socket.onmessage = event => {
      try {
        const msg = JSON.parse(event.data) as WSMessage
        handleNotification(msg)
      } catch {
        // ignore non-JSON messages (heartbeat responses)
      }
    }

    socket.onclose = () => {
      connected.value = false
      stopHeartbeat()
      scheduleReconnect()
    }

    socket.onerror = () => {
      connected.value = false
    }

    ws.value = socket
  } catch {
    scheduleReconnect()
  }
}

function scheduleReconnect() {
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) return
  if (reconnectTimer) clearTimeout(reconnectTimer)
  const delay = Math.min(1000 * 2 ** reconnectAttempts, MAX_RECONNECT_DELAY)
  reconnectAttempts++
  reconnectTimer = setTimeout(() => connect(), delay)
}

function disconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  reconnectAttempts = 0
  stopHeartbeat()
  if (ws.value) {
    ws.value.onclose = null
    ws.value.close()
    ws.value = null
  }
  connected.value = false
}

export function useWebSocket() {
  return { connected, connect, disconnect }
}

export { connect as connectWebSocket, disconnect as disconnectWebSocket }
