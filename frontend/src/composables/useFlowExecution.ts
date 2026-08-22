import { ref, computed, watch } from 'vue'
import { executionApi } from '@/api/execution'
import type { FlowExecution, NodeExecution } from '@/types/execution'
import type { SSEEvent } from '@/types/sse'
import type { Segment, TodoItem } from '@/types/segment'
import {
  appendThinking as appendThinkingToSegments,
  appendContent as appendContentToSegments,
  addTool as addToolToSegments,
  updateTool as updateToolInSegments,
  attachKnowledgeCitations,
  executionStepsToSegments
} from '@/composables/useSegmentBuilder'
import { createOnToolCallLimitHandler, createOnLlmRetryHandler } from '@/composables/useSSEHandlers'
import { useKnowledgeReferenceDrawer } from '@/composables/useKnowledgeReferenceDrawer'
import { ElMessage } from 'element-plus'

interface ConversationMessage {
  role: string
  content: string
  name?: string
  tool_calls?: Array<{ name: string; args: Record<string, unknown>; id?: string }>
}

interface UseFlowExecutionOptions {
  onFlowDone?: () => void
  onError?: () => void
}

export function useFlowExecution(options: UseFlowExecutionOptions = {}) {
  // ---- 执行状态 ----
  const currentExecution = ref<FlowExecution | null>(null)
  const nodeExecutions = ref<NodeExecution[]>([])
  const isStreamRunning = ref(false)
  const streamAbort = ref<(() => void) | null>(null)
  const streamingContent = ref<Record<string, { segments: Segment[] }>>({})
  let streamingSegmentOffsets: Record<string, number> = {}
  let executionRequestVersion = 0
  const flowTodos = ref<TodoItem[]>([])
  const { close: closeKnowledgeReferenceDrawer } = useKnowledgeReferenceDrawer()

  watch(
    () => currentExecution.value?.id,
    () => closeKnowledgeReferenceDrawer()
  )

  // ---- 人工输入状态 ----
  const showHumanInputDialog = ref(false)
  const humanInputQuestion = ref('')
  const humanInputContext = ref('')
  const humanInputLoading = ref(false)
  const humanInputMessages = ref<ConversationMessage[]>([])

  // ---- 计算属性 ----
  const isRunning = computed(() => currentExecution.value?.status === 1)
  const hasExecution = computed(() => !!currentExecution.value)
  const attachedFiles = computed(() => currentExecution.value?.files || [])

  // ---- SSE 事件处理器 ----
  function createStreamHandlers() {
    return {
      onFlowStart: (event: SSEEvent) => {
        isStreamRunning.value = true
        if (event.data.execution_id) {
          currentExecution.value = {
            ...currentExecution.value!,
            id: event.data.execution_id
          }
        }
      },
      onNodeStart: (event: SSEEvent) => {
        const nodeKey = event.data.node_key || ''
        if (!streamingContent.value[nodeKey]) {
          streamingContent.value = {
            ...streamingContent.value,
            [nodeKey]: { segments: [] }
          }
        }
        streamingSegmentOffsets[nodeKey] = streamingContent.value[nodeKey]?.segments.length || 0
        const existingIndex = nodeExecutions.value.findIndex(n => n.node_key === nodeKey)
        if (existingIndex === -1) {
          nodeExecutions.value = [
            ...nodeExecutions.value,
            {
              node_key: nodeKey,
              node_type: event.data.node_type || '',
              node_name: event.data.node_name || '',
              status: 1,
              start_time: new Date().toISOString(),
              input_data: event.data.input_data
            } as NodeExecution
          ]
        } else {
          nodeExecutions.value[existingIndex] = {
            ...nodeExecutions.value[existingIndex],
            status: 1,
            input_data: event.data.input_data
          }
          nodeExecutions.value = [...nodeExecutions.value]
        }
      },
      onNodeThinking: (event: SSEEvent) => {
        const nodeKey = event.data.node_key || ''
        const current = streamingContent.value[nodeKey]
        const content = event.data.content || ''
        const baseSegments = current?.segments ? [...current.segments] : []
        const offset = streamingSegmentOffsets[nodeKey] || 0
        const segments = [
          ...baseSegments.slice(0, offset),
          ...appendThinkingToSegments(baseSegments.slice(offset), content)
        ]
        streamingContent.value = { ...streamingContent.value, [nodeKey]: { segments } }
      },
      onNodeContent: (event: SSEEvent) => {
        const nodeKey = event.data.node_key || ''
        const current = streamingContent.value[nodeKey]
        const content = event.data.content || ''
        const baseSegments = current?.segments ? [...current.segments] : []
        const offset = streamingSegmentOffsets[nodeKey] || 0
        const segments = [
          ...baseSegments.slice(0, offset),
          ...appendContentToSegments(baseSegments.slice(offset), content)
        ]
        streamingContent.value = { ...streamingContent.value, [nodeKey]: { segments } }
      },
      onKnowledgeCitations: (event: SSEEvent) => {
        const nodeKey = event.data.node_key || ''
        const current = streamingContent.value[nodeKey]
        const citations = event.data.citations || []
        if (!current || citations.length === 0) return
        const segments = attachKnowledgeCitations([...current.segments], citations)
        streamingContent.value = { ...streamingContent.value, [nodeKey]: { segments } }
      },
      onNodeDone: (event: SSEEvent) => {
        const nodeKey = event.data.node_key || ''
        const existingIndex = nodeExecutions.value.findIndex(n => n.node_key === nodeKey)
        if (existingIndex !== -1) {
          const node = nodeExecutions.value[existingIndex]
          if (!node) return
          nodeExecutions.value[existingIndex] = {
            ...node,
            status: event.data.error ? 3 : 2,
            end_time: new Date().toISOString(),
            error_message: event.data.error || undefined,
            output_data: event.data.output_data || {}
          }
          nodeExecutions.value = [...nodeExecutions.value]
        }
      },
      onToolCallStart: (event: SSEEvent) => {
        const nodeKey = event.data.node_key || ''
        const toolName = event.data.tool_name || ''
        const toolArgs = event.data.tool_args || {}
        const toolCallId = event.data.tool_call_id
        const current = streamingContent.value[nodeKey]
        const baseSegments = current?.segments ? [...current.segments] : []
        const segments = addToolToSegments(
          baseSegments,
          toolName,
          toolArgs,
          'running',
          undefined,
          toolCallId
        )
        streamingContent.value = { ...streamingContent.value, [nodeKey]: { segments } }
      },
      onToolCallEnd: (event: SSEEvent) => {
        const nodeKey = event.data.node_key || ''
        const toolName = event.data.tool_name || ''
        const toolCallId = event.data.tool_call_id
        const result = event.data.result
        const status: 'success' | 'error' = event.data.status === 'error' ? 'error' : 'success'
        const current = streamingContent.value[nodeKey]
        if (current?.segments?.length) {
          const base = [...current.segments]
          const updated = updateToolInSegments(base, toolName, status, result, toolCallId)
          if (updated === base) {
            streamingContent.value = {
              ...streamingContent.value,
              [nodeKey]: {
                segments: addToolToSegments(base, toolName, undefined, status, result, toolCallId)
              }
            }
          } else {
            streamingContent.value = { ...streamingContent.value, [nodeKey]: { segments: updated } }
          }
        } else {
          const base = current?.segments ? [...current.segments] : []
          streamingContent.value = {
            ...streamingContent.value,
            [nodeKey]: {
              segments: addToolToSegments(base, toolName, undefined, status, result, toolCallId)
            }
          }
        }
      },
      onToolCallLimit: createOnToolCallLimitHandler(),
      onWaitingHuman: handleWaitingHuman,
      onTokenUsage: (event: SSEEvent) => {
        const nodeKey = event.data.node_key || ''
        const existingIndex = nodeExecutions.value.findIndex(n => n.node_key === nodeKey)
        if (existingIndex !== -1) {
          const node = nodeExecutions.value[existingIndex]
          if (node) {
            nodeExecutions.value[existingIndex] = {
              ...node,
              prompt_tokens: (node.prompt_tokens || 0) + (event.data.prompt_tokens || 0),
              completion_tokens:
                (node.completion_tokens || 0) + (event.data.completion_tokens || 0),
              total_tokens: (node.total_tokens || 0) + (event.data.total_tokens || 0)
            }
          }
          nodeExecutions.value = [...nodeExecutions.value]
        }
      },
      onTodoUpdate: (event: SSEEvent) => {
        flowTodos.value = (event.data.todos || []) as TodoItem[]
      },
      onFlowDone: (event: SSEEvent) => {
        isStreamRunning.value = false
        currentExecution.value = {
          ...currentExecution.value!,
          status: 2,
          output_data: event.data.output_data || {},
          end_time: new Date().toISOString()
        }
        if (event.data.execution_id) {
          currentExecution.value.id = event.data.execution_id
        }
        ElMessage.success('执行完成')
        options.onFlowDone?.()
      },
      onLlmRetry: createOnLlmRetryHandler(),
      onError: (event: SSEEvent) => {
        isStreamRunning.value = false
        currentExecution.value = {
          ...currentExecution.value!,
          status: 3,
          error_message: event.data.message || '执行失败',
          end_time: new Date().toISOString()
        }
        if (event.data.execution_id) {
          currentExecution.value.id = event.data.execution_id
        }
        ElMessage.error(event.data.message || '执行失败')
        const lastIndex = nodeExecutions.value.length - 1
        if (lastIndex >= 0) {
          const node = nodeExecutions.value[lastIndex]
          nodeExecutions.value[lastIndex] = {
            ...node,
            status: 3,
            end_time: new Date().toISOString(),
            error_message: event.data.message || undefined
          }
          nodeExecutions.value = [...nodeExecutions.value]
        }
        options.onError?.()
      }
    }
  }

  // ---- 人工输入 ----
  function handleWaitingHuman(event: SSEEvent): void {
    humanInputQuestion.value = event.data.question || '请提供输入'
    humanInputContext.value = event.data.context || ''
    humanInputMessages.value = []
    if (event.data.execution_id) {
      loadConversationHistory(event.data.execution_id, event.data.node_key, executionRequestVersion)
    }
    showHumanInputDialog.value = true
  }

  async function loadConversationHistory(
    executionId: number,
    nodeKey?: string,
    requestVersion = executionRequestVersion
  ): Promise<void> {
    try {
      const res = await executionApi.getConversationHistory(executionId, nodeKey)
      if (
        requestVersion === executionRequestVersion &&
        currentExecution.value?.id === executionId &&
        res.data.code === 1 &&
        res.data.data.messages
      ) {
        humanInputMessages.value = res.data.data.messages.map(msg => ({
          role: msg.role,
          content: msg.content,
          name: msg.name,
          tool_calls: msg.tool_calls
        }))
      }
    } catch (error) {
      console.error('[useFlowExecution] 加载对话历史失败:', error)
    }
  }

  function submitHumanInput(value: string): void {
    if (!value.trim()) {
      ElMessage.warning('请输入内容')
      return
    }
    if (!currentExecution.value?.id) {
      ElMessage.error('执行记录不存在')
      return
    }
    humanInputLoading.value = true
    try {
      executionRequestVersion++
      showHumanInputDialog.value = false
      isStreamRunning.value = true
      streamAbort.value = executionApi.resumeStream(
        currentExecution.value.id,
        value,
        createStreamHandlers()
      )
    } finally {
      humanInputLoading.value = false
    }
  }

  // ---- 执行控制 ----
  function startStream(
    flowId: number,
    input: Record<string, unknown>,
    files?: Array<{ id: number; original_name: string; mime_type: string }>
  ): void {
    executionRequestVersion++
    streamingContent.value = {}
    streamingSegmentOffsets = {}
    currentExecution.value = {
      flow_id: flowId,
      status: 1,
      input_data: input,
      files: files && files.length > 0 ? files : undefined
    } as FlowExecution
    nodeExecutions.value = []
    isStreamRunning.value = true
    streamAbort.value = executionApi.stream(
      flowId,
      { input_data: input, files: files && files.length > 0 ? files : undefined },
      createStreamHandlers()
    )
  }

  async function resumeFromHistory(exec: FlowExecution): Promise<void> {
    const requestVersion = ++executionRequestVersion
    if (streamAbort.value) {
      streamAbort.value()
      streamAbort.value = null
    }
    isStreamRunning.value = false
    streamingContent.value = {}
    streamingSegmentOffsets = {}
    showHumanInputDialog.value = false
    currentExecution.value = exec
    if (exec.id) {
      const nodesRes = await executionApi.getNodes(exec.id)
      if (requestVersion !== executionRequestVersion) return
      if (nodesRes.data.code === 1) {
        nodeExecutions.value = nodesRes.data.data
        const restored: Record<string, { segments: Segment[] }> = {}
        for (const node of nodeExecutions.value) {
          if (!node.node_key) continue
          const segments = executionStepsToSegments(node.execution_steps || [])
          if (segments.length === 0) continue
          restored[node.node_key] = { segments }
          streamingSegmentOffsets[node.node_key] = segments.length
        }
        streamingContent.value = restored
      }
      await loadConversationHistory(exec.id, undefined, requestVersion)
      if (requestVersion !== executionRequestVersion) return
      const waitRes = await executionApi.getWaitStatus(exec.id)
      if (requestVersion !== executionRequestVersion) return
      if (waitRes.data.code === 1 && waitRes.data.data.waiting) {
        humanInputQuestion.value = waitRes.data.data.prompt || '请提供输入'
        humanInputContext.value = waitRes.data.data.context || ''
        showHumanInputDialog.value = true
      }
    }
  }

  async function loadExecutionDetail(executionId: number): Promise<void> {
    const requestVersion = ++executionRequestVersion
    const [execRes, nodesRes] = await Promise.all([
      executionApi.get(executionId),
      executionApi.getNodes(executionId)
    ])
    if (requestVersion !== executionRequestVersion) return
    if (execRes.data.code === 1) {
      currentExecution.value = execRes.data.data
    }
    if (nodesRes.data.code === 1) {
      nodeExecutions.value = nodesRes.data.data
    }
  }

  async function stopExecution(): Promise<void> {
    const executionId = currentExecution.value?.id
    cancelStream()
    const stopVersion = executionRequestVersion
    if (!executionId) return

    try {
      await executionApi.cancel(executionId)
      if (stopVersion !== executionRequestVersion || currentExecution.value?.id !== executionId)
        return
      await loadExecutionDetail(executionId)
    } catch {
      // 前端连接已断开，后端取消失败由统一错误处理提示。
    }
  }

  function cancelStream(): void {
    executionRequestVersion++
    if (streamAbort.value) {
      streamAbort.value()
      streamAbort.value = null
    }
    isStreamRunning.value = false
    streamingContent.value = {}
    streamingSegmentOffsets = {}
    showHumanInputDialog.value = false
    if (currentExecution.value) {
      currentExecution.value = {
        ...currentExecution.value,
        status: 4,
        end_time: new Date().toISOString()
      }
    }
  }

  function resetState(): void {
    executionRequestVersion++
    if (streamAbort.value) {
      streamAbort.value()
      streamAbort.value = null
    }
    currentExecution.value = null
    nodeExecutions.value = []
    streamingContent.value = {}
    streamingSegmentOffsets = {}
    flowTodos.value = []
    isStreamRunning.value = false
    showHumanInputDialog.value = false
  }

  return {
    // 状态
    currentExecution,
    nodeExecutions,
    streamingContent,
    isStreamRunning,
    flowTodos,
    isRunning,
    hasExecution,
    attachedFiles,
    // 人工输入
    showHumanInputDialog,
    humanInputQuestion,
    humanInputContext,
    humanInputLoading,
    humanInputMessages,
    // 方法
    createStreamHandlers,
    startStream,
    resumeFromHistory,
    loadExecutionDetail,
    loadConversationHistory,
    submitHumanInput,
    stopExecution,
    cancelStream,
    resetState
  }
}
