import { defineStore } from 'pinia'
import { ref, computed, reactive } from 'vue'
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
  // 用 reactive 包装 Map 实例（不是 ref(new Map())），确保 set/delete/clear 触发响应式
  const tools = reactive(new Map<string, RunningTool>())
  const drawerVisible = ref(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let handlerRegistered = false

  const runningCount = computed(() => {
    let count = 0
    for (const t of tools.values()) {
      if (t.status === 'running') count++
    }
    return count
  })

  const toolList = computed(() => Array.from(tools.values()))

  function addOrUpdateTask(task: BackgroundTask) {
    const existing = tools.get(task.task_id)
    tools.set(task.task_id, {
      task_id: task.task_id,
      command: task.command,
      status: task.status,
      stdout: task.stdout || '',
      stderr: task.stderr || '',
      return_code: task.return_code,
      elapsed_seconds: task.elapsed_seconds,
      startTime: existing?.startTime || Date.now()
    })
  }

  function endTask(
    taskId: string,
    status: string,
    returnCode: number | null,
    elapsed: number | null
  ) {
    const task = tools.get(taskId)
    if (task) {
      task.status = status as RunningTool['status']
      task.return_code = returnCode
      task.elapsed_seconds = elapsed
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
        for (const [localId, local] of tools) {
          if (serverTaskIds.has(localId)) continue
          if (local.status !== 'running') {
            // 服务端已过期清理的非 running 任务，本地同步删除
            tools.delete(localId)
          } else {
            // 本地 running 但服务端列表缺失：查单个任务状态兜底，
            // 仍查不到（服务重启/过期清理）则标记结束，避免 UI 永远显示运行中
            await finalizeVanishedTask(localId)
          }
        }
      }
      if (runningCount.value === 0) {
        stopPolling()
      }
    } catch {
      // ignore poll errors
    }
  }

  /**
   * 结束服务端已不存在的本地 running 任务：
   * 优先查询单任务状态拿真实终态（completed/failed/timeout），
   * 查询失败说明任务已彻底消失（服务重启或过期清理），标记为 cancelled
   */
  async function finalizeVanishedTask(taskId: string) {
    try {
      const res = await toolApi.getStatus(taskId)
      if (res.data.code === 1 && res.data.data) {
        const t = res.data.data
        endTask(taskId, t.status, t.return_code, t.elapsed_seconds)
        return
      }
    } catch {
      // 任务不存在（ApiResponse.error 被 axios 拦截器 reject），走下方兜底
    }
    endTask(taskId, 'cancelled', null, null)
  }

  async function loadRunning() {
    try {
      const res = await toolApi.getRunning()
      if (res.data.code === 1 && res.data.data) {
        tools.clear()
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
    } catch {
      // 取消失败（如任务已结束/不存在）：后端返回 code=0 被 axios 拦截器 reject，
      // 任务在服务端已不存在，本地同样落地为 cancelled，避免停止按钮点击无效
    }
    const task = tools.get(taskId)
    if (task && task.status === 'running') {
      task.status = 'cancelled'
    }
    stopPollIfDone()
  }

  function removeTask(taskId: string) {
    tools.delete(taskId)
  }

  /** 关闭抽屉时清理所有非 running 任务 */
  function closeDrawer() {
    drawerVisible.value = false
    for (const [id, task] of tools) {
      if (task.status !== 'running') {
        tools.delete(id)
      }
    }
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
