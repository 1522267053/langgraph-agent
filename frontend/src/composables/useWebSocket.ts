import { ref, h } from 'vue'
import { ElNotification, ElButton } from 'element-plus'
import { agendaApi } from '@/api/agenda'

interface ExecutionDoneData {
  execution_id: number | null
  flow_id: number | null
  flow_name: string
  status: 'success' | 'failed' | 'cancelled'
  source: 'flow' | 'agent'
  error_message?: string | null
  duration_ms?: number | null
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
    const { flow_name, status, source, error_message, duration_ms, execution_id } = data

    // 跳过用户正在通过 SSE 观看的执行
    if (
      execution_id !== null &&
      watchingExecutionId !== null &&
      execution_id === watchingExecutionId
    ) {
      return
    }

    const typeLabel = source === 'agent' ? '对话' : '流程'
    const durationStr = duration_ms ? `${(duration_ms / 1000).toFixed(1)} 秒` : ''

    if (status === 'success') {
      ElNotification({
        type: 'success',
        title: `${typeLabel}完成`,
        message: `「${flow_name}」执行完成${durationStr ? `，耗时 ${durationStr}` : ''}`,
        duration: 5000,
        position: 'top-right'
      })
    } else if (status === 'failed') {
      ElNotification({
        type: 'error',
        title: `${typeLabel}失败`,
        message: `「${flow_name}」: ${error_message || '执行失败'}`,
        duration: 0,
        position: 'top-right'
      })
    }
  } else if (msg.type === 'agenda_reminder') {
    const data = msg.data as AgendaReminderData
    const parts: string[] = []
    if (data.start_time) parts.push(`时间: ${data.start_time}`)
    if (data.location) parts.push(`地点: ${data.location}`)
    if (data.description) parts.push(data.description)

    ElNotification({
      type: 'warning',
      title: `日程提醒: ${data.title}`,
      message: h('div', [
        h('p', { style: 'margin: 4px 0 12px' }, parts.join(' | ') || '该日程开始了'),
        h('div', { style: 'display: flex; gap: 8px' }, [
          h(ElButton, {
            type: 'primary',
            size: 'small',
            onClick: async () => {
              try {
                await agendaApi.complete(data.agenda_id)
                ElNotification.closeAll()
              } catch {
                // ignore
              }
            }
          }, () => '完成'),
          h(ElButton, {
            size: 'small',
            onClick: async () => {
              try {
                await agendaApi.postpone(data.agenda_id)
                ElNotification.closeAll()
              } catch {
                // ignore
              }
            }
          }, () => '延后15分钟')
        ])
      ]),
      duration: 0,
      position: 'top-right',
      onClick: () => {
        window.location.hash = '#/agenda'
      }
    })
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
