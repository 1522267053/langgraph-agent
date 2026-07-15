import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { toolApi, type BackgroundTask } from '@/api/tool'
import { setToolOutputHandler } from '@/composables/useWebSocket'

export interface RunningTool {
  task_id: string
  command: string
  status: 'running' | 'completed' | 'failed' | 'timeout' | 'cancelled'
  stdout: string
  stderr: string
  return_code: number | null
  elapsed_seconds: number | null
  startTime: number
}

const POLL_INTERVAL = 1500

export const useToolOutputStore = defineStore('toolOutput', () => {
  const tools = ref<Map<string, RunningTool>>(new Map())
  const drawerVisible = ref(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let handlerRegistered = false

  const runningCount = computed(() => {
    let count = 0
    for (const t of tools.value.values()) {
      if (t.status === 'running') count++
    }
    return count
  })

  const toolList = computed(() => Array.from(tools.value.values()))

  const _reactivityTrigger = ref(0)
  function triggerReactivity() {
    _reactivityTrigger.value++
  }

  function addOrUpdateTask(task: BackgroundTask) {
    const existing = tools.value.get(task.task_id)
    tools.value.set(task.task_id, {
      task_id: task.task_id,
      command: task.command,
      status: task.status,
      stdout: task.stdout || '',
      stderr: task.stderr || '',
      return_code: task.return_code,
      elapsed_seconds: task.elapsed_seconds,
      startTime: existing?.startTime || Date.now()
    })
    triggerReactivity()
  }

  function endTask(
    taskId: string,
    status: string,
    returnCode: number | null,
    elapsed: number | null
  ) {
    const task = tools.value.get(taskId)
    if (task) {
      task.status = status as RunningTool['status']
      task.return_code = returnCode
      task.elapsed_seconds = elapsed
      triggerReactivity()
    }
    stopPollIfDone()
  }

  function startPolling() {
    if (pollTimer) return
    pollTimer = setInterval(pollOnce, POLL_INTERVAL)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function stopPollIfDone() {
    if (runningCount.value === 0) {
      stopPolling()
    }
  }

  async function pollOnce() {
    try {
      const res = await toolApi.getRunning()
      if (res.data.code === 1 && res.data.data) {
        for (const task of res.data.data) {
          addOrUpdateTask(task)
        }
        const serverTaskIds = new Set(res.data.data.map(t => t.task_id))
        for (const [localId, local] of tools.value) {
          if (!serverTaskIds.has(localId) && local.status !== 'running') {
            tools.value.delete(localId)
          }
        }
        triggerReactivity()
      }
      if (runningCount.value === 0) {
        stopPolling()
      }
    } catch {
      // ignore poll errors
    }
  }

  async function loadRunning() {
    try {
      const res = await toolApi.getRunning()
      if (res.data.code === 1 && res.data.data) {
        tools.value.clear()
        for (const task of res.data.data) {
          addOrUpdateTask(task)
        }
        if (runningCount.value > 0) {
          startPolling()
        }
      }
    } catch {
      // ignore
    }
  }

  async function cancelTask(taskId: string) {
    try {
      await toolApi.cancel(taskId)
      const task = tools.value.get(taskId)
      if (task) {
        task.status = 'cancelled'
        triggerReactivity()
      }
    } catch {
      // ignore
    }
  }

  function removeTask(taskId: string) {
    tools.value.delete(taskId)
    triggerReactivity()
  }

  /** 关闭抽屉时清理所有非 running 任务 */
  function closeDrawer() {
    drawerVisible.value = false
    for (const [id, task] of tools.value) {
      if (task.status !== 'running') {
        tools.value.delete(id)
      }
    }
    triggerReactivity()
  }

  function registerWsHandler() {
    if (handlerRegistered) return
    handlerRegistered = true
    setToolOutputHandler((type, data) => {
      const taskId = data.task_id as string
      if (type === 'tool_output_start') {
        addOrUpdateTask({
          task_id: taskId,
          command: (data.command as string) || '',
          status: 'running',
          stdout: (data.stdout as string) || '',
          stderr: (data.stderr as string) || '',
          return_code: null,
          elapsed_seconds: null
        })
        startPolling()
      } else if (type === 'tool_output_end') {
        endTask(
          taskId,
          (data.status as string) || 'completed',
          (data.return_code as number) ?? null,
          (data.elapsed_seconds as number) ?? null
        )
      }
    })
  }

  function unregisterWsHandler() {
    setToolOutputHandler(null)
    handlerRegistered = false
  }

  return {
    tools,
    toolList,
    runningCount,
    drawerVisible,
    _reactivityTrigger,
    loadRunning,
    cancelTask,
    removeTask,
    closeDrawer,
    startPolling,
    stopPolling,
    registerWsHandler,
    unregisterWsHandler
  }
})
