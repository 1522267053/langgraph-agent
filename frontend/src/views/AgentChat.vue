<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { useAgentStore } from '@/stores'
import { ElMessage, ElMessageBox, ElImageViewer } from 'element-plus'
import type { ScrollbarDirection, ScrollbarInstance } from 'element-plus'
import { Operation, Bottom, Notebook, Warning } from '@element-plus/icons-vue'
import { agentApi } from '@/api/agent'
import { flowApi } from '@/api/flow'
import { aiProviderApi, type ModelInfo } from '@/api/ai_provider'
import type { FlowIOField } from '@/types/flow'
import DisplayToggle from '@/components/AgentChat/DisplayToggle.vue'
import MemoryPanel from '@/components/AgentChat/MemoryPanel.vue'
import MessageItem from '@/components/AgentChat/MessageItem.vue'
import RunningToolBadge from '@/components/AgentChat/RunningToolBadge.vue'
import ToolOutputDrawer from '@/components/AgentChat/ToolOutputDrawer.vue'
import WelcomePage from '@/components/AgentChat/WelcomePage.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import DirectoryPickerDialog from '@/components/common/DirectoryPickerDialog.vue'
import ChatInput from '@/components/AgentChat/ChatInput.vue'
import FlowPreviewCard from '@/components/common/FlowPreviewCard.vue'
import { buildChatRows, estimateRowSize, rememberRowSize, clearRowSizeCache, type ChatRow } from '@/components/AgentChat/chatRow'
import { clearBlockExpandOverrides } from '@/components/AgentChat/blockExpand'
import { useToolOutputStore } from '@/stores/toolOutput'
import { useAutoScroll } from '@/composables/useAutoScroll'

import 'highlight.js/styles/vs2015.css'

const route = useRoute()
const store = useAgentStore()
const toolOutputStore = useToolOutputStore()

const scrollbarRef = ref<ScrollbarInstance>()
const messagesContentRef = ref<HTMLElement | null>(null)
const messagesContainer = computed<HTMLElement | null>(
  () => (scrollbarRef.value?.wrapRef as HTMLElement | undefined) ?? null
)
// 首次加载测量收敛期间内容以 visibility:hidden 占位（布局保留、virtualizer 可测），
// 收敛并定位到底部后才显示——显示即已在底部，避免「顶部闪现 + 跳底」的抖动
const messagesRevealed = ref(false)
// 收敛循环代际号：新一轮加载开始后旧循环自动失效，防止过早 reveal
let convergeGeneration = 0

// SSE 状态早于 Markdown DOM 更新，底部跟随以 useAutoScroll 内部 ResizeObserver 的
// 真实高度变化为准（容器 + 内容子元素自动观测）。
/** RO 贴底跟随宽限截止时间：流式结束后短暂保留跟随，吸收代码高亮/KaTeX 等
 *  晚到渲染的撑高；此后用户手动展开/收起块撑高内容不再被贴底拉走视口 */
let roFollowGraceUntil = 0

const {
  autoScroll,
  isAtBottom,
  scrollToBottom,
  handleScroll,
  onUserScrollIntent,
  resetAutoScrollState
} = useAutoScroll(messagesContainer, [], {
  enabled: () => store.isStreaming || Date.now() < roFollowGraceUntil
})

watch(
  () => store.isStreaming,
  streaming => {
    if (!streaming) roFollowGraceUntil = Date.now() + 800
  }
)

function handleScrollbarPointerDown(event: PointerEvent): void {
  const root = scrollbarRef.value?.$el as Element | undefined
  const target = event.target
  if (
    root instanceof Element &&
    target instanceof Element &&
    target.closest('.el-scrollbar') === root &&
    target.closest('.el-scrollbar__bar')
  ) {
    onUserScrollIntent(event)
  }
}

// ---- 虚拟滚动（@tanstack/vue-virtual 挂在 el-scrollbar 的原生滚动 wrap 上）----
// 行模型为「段级」：AI 回合每个 segment 独占一行（见 AgentChat/chatRow.ts），
// 消息级 UI（头像/头部/尾部）拆分到 first/last 行

// 流式中但最后一条不是 AI 消息（或列表为空）时，追加独立的输入指示器行
const showStandaloneTyping = computed(() => {
  if (!store.isStreaming) return false
  const last = store.chatMessages.at(-1)
  return !last || last.role !== 'ai' || last.displayType === 'context-summary'
})

const chatRows = computed<ChatRow[]>(() =>
  buildChatRows(store.chatMessages, showStandaloneTyping.value, store.isStreaming)
)

// 展示开关：声明须在 rowVirtualizer 之前（estimateSize 闭包在 setup 期间同步求值）
const showThinking = ref(true)
// 结束节点输出按钮：默认不展示，右上角"展示"下拉勾选后显示
const showEndOutput = ref(false)

const rowVirtualizer = useVirtualizer<HTMLDivElement, HTMLDivElement>({
  get count() {
    return chatRows.value.length
  },
  getScrollElement: () => messagesContainer.value as HTMLDivElement | null,
  estimateSize: (index: number) =>
    estimateRowSize(chatRows.value[index], {
      showThinking: showThinking.value
    }),
  overscan: 8,
  getItemKey: (index: number) => chatRows.value[index]?.key ?? String(index)
})

// [修复] 向上滚动历史时，已过视口的 Markdown 行会因代码高亮/KaTeX 异步渲染继续撑高，
// virtual-core 默认在 backward 滚动时跳过重测滚动补偿（防 #1218 级联），导致视口下方
// 内容被整体推移（滚动跳动）。此处复刻库默认规则但去掉 backward 跳过：
// - 首测（估算→实测）：行顶在滚动位上方即补偿
// - 重测：行整体在滚动位上方才补偿（横跨视口的行生长发生在锚点下方，不补偿以防拖动视口）
// 注意：回调在 itemSizeCache.set 之前调用，可用 has() 判定是否首测；
// item.size 为变更前旧值，新实测 = item.size + delta，同步写入行高实测缓存——
// 展示开关切换 measure() 清缓存后，估算回落到上次实测而非固定粗估，避免
// 未挂载行重挂时产生巨量 delta（估算 268px vs 实测 4000px+）引发滚动跳变
rowVirtualizer.value.shouldAdjustScrollPositionOnItemSizeChange = (item, delta, instance) => {
  if (delta !== 0) rememberRowSize(item.key, item.size + delta)
  const offset = (instance.scrollOffset ?? 0) + instance.scrollAdjustments
  return !instance.itemSizeCache.has(item.key)
    ? item.start < offset
    : item.start + item.size <= offset
}

const virtualRows = computed(() => rowVirtualizer.value.getVirtualItems())

// 展示开关改变行内内容高度：整体失效 virtualizer 尺寸缓存（行 key 不变，未挂载行
// 的旧实测尺寸会残留导致滚动错位）；已挂载行由 ResizeObserver 重测，未挂载行回落
// 到行高实测缓存（开关切换前的上次实测，非固定粗估），重测 delta 即为开关切换
// 的真实增量，滚动补偿量微小不跳变
watch([showThinking, showEndOutput], async () => {
  await nextTick()
  rowVirtualizer.value.measure()
})

const humanInputValue = ref('')

const imagePreviewVisible = ref(false)
const imagePreviewUrl = ref('')
const imagePreviewUrls = ref<string[]>([])
const imagePreviewIndex = ref(0)

function handleImagePreview(data: ImagePreviewData) {
  imagePreviewUrl.value = data.url
  imagePreviewUrls.value = data.urls
  imagePreviewIndex.value = data.index
  imagePreviewVisible.value = true
}

function closeImagePreview() {
  imagePreviewVisible.value = false
}

function handlePreviewSwitch(index: number) {
  imagePreviewIndex.value = index
  imagePreviewUrl.value = imagePreviewUrls.value[index]
}

const agentId = ref<number | null>(null)

const isWelcomeMode = computed(() => !store.messagesLoading && store.chatMessages.length === 0)

// 欢迎页无消息列表，无需收敛贴底：直接显示
// （空会话/无会话时 selectSession 不被调用，messagesLoading 从不翻转，
//  messagesRevealed 须在此解除，否则 loading 遮罩永不消失）
watch(
  isWelcomeMode,
  welcome => {
    if (welcome) messagesRevealed.value = true
  },
  { immediate: true }
)

function handleSuggestedPrompt(prompt: string) {
  inputMessage.value = prompt
  handleChatSend({}, [], prompt)
  inputMessage.value = ''
  chatInputRef.value?.resetParams()
}

const STORAGE_KEY = 'agent-chat-display'

function loadDisplayPrefs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const prefs = JSON.parse(raw)
      if (typeof prefs.autoScroll === 'boolean') autoScroll.value = prefs.autoScroll
      if (typeof prefs.showThinking === 'boolean') showThinking.value = prefs.showThinking
      if (typeof prefs.showEndOutput === 'boolean') showEndOutput.value = prefs.showEndOutput
    }
  } catch {
    // ignore
  }
}

function saveDisplayPrefs() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      autoScroll: autoScroll.value,
      showThinking: showThinking.value,
      showEndOutput: showEndOutput.value
    })
  )
}

loadDisplayPrefs()

watch([autoScroll, showThinking, showEndOutput], saveDisplayPrefs)

watch(
  () => route.params.id,
  async newId => {
    const id = newId ? parseInt(newId as string) : null
    if (id === agentId.value) return

    clearBlockExpandOverrides()
    clearRowSizeCache()
    let targetId = id
    if (!targetId) {
      // 优先级：上次使用(需验证仍存在) > localStorage 默认 > 内置 Agent
      let resolved = false
      if (store.lastUsedAgentId) {
        if (store.agents.length === 0) await store.loadAgents()
        const exists = store.agents.some(a => a.id === store.lastUsedAgentId)
        if (exists) {
          targetId = store.lastUsedAgentId
          resolved = true
        } else {
          store.lastUsedAgentId = null
        }
      }
      if (!resolved) {
        const storedDefault = localStorage.getItem('default_agent_id')
        if (storedDefault) {
          targetId = parseInt(storedDefault)
        } else {
          if (store.agents.length === 0) await store.loadAgents()
          const builtin = store.agents.find((a: { is_builtin?: number }) => a.is_builtin === 1)
          targetId = builtin?.id ?? null
        }
      }
      if (!targetId) {
        agentId.value = null
        store.chatMessages = []
        store.currentSession = null
        return
      }
    }

    agentId.value = targetId
    store.cancelStream()
    store.sessionsLoading = true
    loadModelSelection(targetId)
    try {
      await store.loadAgent(targetId)
      store.lastUsedAgentId = targetId
      await store.loadSessions(targetId)
      if (store.sessions.length > 0) {
        await store.selectSession(targetId, store.sessions[0])
      } else {
        store.chatMessages = []
        store.currentSession = null
      }
    } finally {
      if (store.sessionsLoading) store.sessionsLoading = false
    }
  }
)

const isLoadingMore = ref(false)

const dynamicFields = computed<FlowIOField[]>(() => {
  const fields = store.currentAgent?.input_schema?.fields || []
  return fields.filter(f => f.name && f.name !== 'message')
})

const inputMessage = ref('')
const chatInputRef = ref<InstanceType<typeof ChatInput>>()
const showMemory = ref(false)
/** 回退恢复信号：每次回退生成新对象，通知当前挂载的 ChatInput 恢复参数 */
const restoreParamsSignal = ref<Record<string, unknown> | null>(null)

// ---- 临时模型切换（仅同供应商内，capabilities 等由后端按 ai_model 元数据联动）----
interface ChatModelOption {
  value: string
  label: string
  multimodal: boolean
}

const MODEL_PREF_KEY = 'agent-chat-model'

const modelOptions = ref<ChatModelOption[]>([])
const defaultModelLabel = ref('')
const selectedModel = ref('')
/** 恢复 localStorage 偏好期间挂起持久化 watcher，防止切 Agent 时误删新 Agent 的偏好 */
let restoringModelPref = false

function loadStoredModel(id: number): string {
  try {
    const raw = localStorage.getItem(MODEL_PREF_KEY)
    if (!raw) return ''
    const map = JSON.parse(raw) as Record<string, string>
    return typeof map[id] === 'string' ? map[id] : ''
  } catch {
    return ''
  }
}

watch(selectedModel, model => {
  if (restoringModelPref || !agentId.value) return
  try {
    const raw = localStorage.getItem(MODEL_PREF_KEY)
    const map = raw ? (JSON.parse(raw) as Record<string, string>) : {}
    if (model) map[agentId.value] = model
    else delete map[agentId.value]
    localStorage.setItem(MODEL_PREF_KEY, JSON.stringify(map))
  } catch {
    // ignore
  }
})

/**
 * 加载当前 Agent 可切换的模型列表（取 LLM 节点配置的供应商）并恢复上次选择；
 * 失败时静默降级：下拉框隐藏、发送走 Agent 默认模型
 */
async function loadModelSelection(id: number) {
  modelOptions.value = []
  defaultModelLabel.value = ''
  // 重置/恢复 selectedModel 都不触发持久化（watcher 为 pre-flush，nextTick 后才放行）
  restoringModelPref = true
  selectedModel.value = ''
  try {
    const res = await flowApi.get(id)
    const llmNode = (res.data.data?.nodes || []).find(n => n.node_type === 'llm')
    if (!llmNode?.base_config) return
    const provider = String(llmNode.base_config.provider || '')
    defaultModelLabel.value = String(llmNode.base_config.model || '')
    if (!provider) return
    const modelsRes = await aiProviderApi.getModels(provider)
    modelOptions.value = (modelsRes.data.data || []).map((m: ModelInfo) => ({
      value: m.model_id,
      label: m.name,
      multimodal: (m.modalities?.input || []).some(t =>
        ['image', 'video', 'audio', 'pdf'].includes(t)
      )
    }))
    const stored = loadStoredModel(id)
    if (stored && modelOptions.value.some(o => o.value === stored)) {
      selectedModel.value = stored
    }
  } catch {
    // 模型列表加载失败不阻塞聊天
  } finally {
    await nextTick()
    restoringModelPref = false
  }
}

// ---- 会话级项目工作路径（对标 opencode session.directory）----
// 有会话时以 currentSession.work_dir 为准，无会话时暂存到 pendingWorkDir，
// 首次发消息创建会话时随 createSession 传入
const workDirPickerVisible = ref(false)
const pendingWorkDir = ref('')

const currentWorkDir = computed(() =>
  store.currentSession ? store.currentSession.work_dir || '' : pendingWorkDir.value
)

function handleSelectWorkDir(): void {
  if (store.isStreaming) {
    ElMessage.warning({ message: '请等待回复完成', duration: 5000 })
    return
  }
  workDirPickerVisible.value = true
}

async function handleWorkDirConfirm(path: string): Promise<void> {
  const session = store.currentSession
  if (session) {
    try {
      const res = await agentApi.updateWorkDir(agentId.value!, session.id, path || null)
      if (res.data.code === 1 && res.data.data) {
        session.work_dir = res.data.data.work_dir
        ElMessage.success({
          message: path ? '工作目录已切换' : '已清除，回退默认目录',
          duration: 5000
        })
      }
    } catch {
      // error handled by interceptor
    }
    return
  }
  pendingWorkDir.value = path
}

onMounted(async () => {
  toolOutputStore.registerWsHandler()
  toolOutputStore.loadRunning()
  const id = route.params.id as string
  const sessionId = route.query.sessionId as string
  try {
    if (id) {
      agentId.value = parseInt(id)
    } else {
      // 优先级：上次使用(需验证仍存在) > localStorage 默认 > 内置 Agent
      let resolved = false
      if (store.lastUsedAgentId) {
        if (store.agents.length === 0) await store.loadAgents()
        const exists = store.agents.some(a => a.id === store.lastUsedAgentId)
        if (exists) {
          agentId.value = store.lastUsedAgentId
          resolved = true
        } else {
          // 上次使用的 Agent 已被删除，清除记忆
          store.lastUsedAgentId = null
        }
      }
      if (!resolved) {
        const storedDefault = localStorage.getItem('default_agent_id')
        if (storedDefault) {
          agentId.value = parseInt(storedDefault)
        } else {
          const res = await agentApi.list()
          const agents = res.data.data?.list || []
          const builtin = agents.find((a: { is_builtin?: number }) => a.is_builtin === 1)
          if (!builtin) {
            ElMessage.error({ message: '内置 Agent 不存在', duration: 5000 })
            return
          }
          agentId.value = builtin.id
        }
      }
    }
    store.sessionsLoading = true
    await store.loadAgent(agentId.value)
    store.lastUsedAgentId = agentId.value
    loadModelSelection(agentId.value)
    await store.loadSessions(agentId.value)
    if (sessionId) {
      const target = store.sessions.find(s => s.id === parseInt(sessionId))
      if (target) {
        await store.selectSession(agentId.value, target)
      } else {
        await store.selectSession(agentId.value, {
          id: parseInt(sessionId)
        } as (typeof store.sessions)[0])
      }
    } else if (store.sessions.length > 0) {
      const session = store.sessions[0]
      if (session) await store.selectSession(agentId.value, session)
    }
  } catch {
    // error handled by interceptor
  } finally {
    if (store.sessionsLoading) store.sessionsLoading = false
    await nextTick()
  }
})

onUnmounted(() => {
  store.cancelStream()
  store.resetState()
  store.stopCompressPolling()
  store.stopSavePolling()
  store.stopRunningPolling()
  toolOutputStore.stopPolling()
  toolOutputStore.unregisterWsHandler()
})

/**
 * 首次加载后的贴底，分两阶段（判据一律用真实时间 ms，帧数在高刷屏上不可靠）：
 * - converge（隐藏期）：visibility:hidden 下逐帧跟随底部，持续至少 600ms 且
 *   高度连续 250ms 不变（测量收敛）后定位到底部并 reveal——显示即已在底部
 * - follow（显示后）：reveal 瞬间仍可能有结构性增量（如 visibility 翻转引发的
 *   少量重排），继续跟随 scrollHeight 变化约 1s 吸收干净；用户滚动输入立即退出
 */
function convergeScrollToBottom(): void {
  const wrap = messagesContainer.value
  if (!wrap) {
    messagesRevealed.value = true
    return
  }
  const generation = ++convergeGeneration
  let phase: 'converge' | 'follow' = 'converge'
  let lastHeight = -1
  let lastChangeAt = performance.now()
  const startAt = performance.now()
  let followUntil = 0
  let aborted = false
  const onUserInput = () => {
    aborted = true
  }
  const startFollow = () => {
    phase = 'follow'
    lastChangeAt = performance.now()
    followUntil = lastChangeAt + 1000
    wrap.addEventListener('wheel', onUserInput, { capture: true, once: true })
    wrap.addEventListener('touchstart', onUserInput, { capture: true, once: true })
    wrap.addEventListener('pointerdown', onUserInput, { capture: true, once: true })
  }
  const cleanup = () => {
    wrap.removeEventListener('wheel', onUserInput, { capture: true })
    wrap.removeEventListener('touchstart', onUserInput, { capture: true })
    wrap.removeEventListener('pointerdown', onUserInput, { capture: true })
  }
  const finish = () => {
    wrap.scrollTop = wrap.scrollHeight
    messagesRevealed.value = true
  }
  const tick = () => {
    if (aborted || generation !== convergeGeneration || !wrap.isConnected) {
      cleanup()
      return
    }
    const now = performance.now()
    const height = wrap.scrollHeight
    if (phase === 'converge') {
      if (height !== lastHeight) {
        lastHeight = height
        lastChangeAt = now
      }
      wrap.scrollTop = height
      // 收敛批次间存在短暂平台期（刷新冷启动时更明显）：至少骑 600ms + 高度
      // 连续 250ms 不变才认定收敛并显示
      const elapsed = now - startAt
      const sinceChange = now - lastChangeAt
      if ((sinceChange < 250 || elapsed < 600) && elapsed < 1500) {
        requestAnimationFrame(tick)
        return
      }
      finish()
      startFollow()
    } else {
      // follow：高度变化（reveal 引发的重排/晚到内容）才跟随，静止 400ms 或
      // 跟随满 1s 后退出
      if (height !== lastHeight) {
        wrap.scrollTop = height
        lastHeight = height
        lastChangeAt = now
      }
      if (now - lastChangeAt >= 400 || now >= followUntil) {
        cleanup()
        return
      }
    }
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
}

watch(
  () => store.messagesLoading,
  async (loading, wasLoading) => {
    if (loading) {
      // 新一轮加载：隐藏内容并使进行中的收敛循环失效
      messagesRevealed.value = false
      convergeGeneration += 1
      return
    }
    if (!wasLoading) return
    resetAutoScrollState()
    await nextTick()
    convergeScrollToBottom()
  }
)

watch(
  () => store.messageRefreshVersion,
  async () => {
    await nextTick()
    // 删除/回退消息后强制贴底：maybeScrollToBottom 受 RO 跟随开关限制，
    // 非流式期间会被拦截
    scrollToBottom()
  }
)

// 上次加载更多的时间戳：加载完成后恢复视口会产生向下的滚动事件，
// 复位 el-scrollbar 的方向闩锁；惯性滚动/估算高度纠偏可能立刻再次满足
// end-reached 条件，需冷却窗口防重复请求
let lastLoadMoreAt = 0

function onEndReached(direction: ScrollbarDirection) {
  if (direction !== 'top') return
  if (isLoadingMore.value || !store.hasMoreMessages || store.messagesLoading) return
  if (Date.now() - lastLoadMoreAt < 1000) return
  lastLoadMoreAt = Date.now()
  handleLoadMore()
}

async function handleLoadMore() {
  if (!agentId.value || isLoadingMore.value) return
  isLoadingMore.value = true
  const wrap = messagesContainer.value
  try {
    // 前插历史后按「锚行坐标差值 + 逐帧收敛」恢复视口：
    // - 锚点优先按「行 key」定位：段级行模式下，新页尾部回合会被合并吸收进锚行，
    //   吸收产生的段行插在锚点之前，「前插行数 = 锚点新下标」不再恒等；但段的
    //   确定性 id 按 DB 行派生，吸收只改变下标不改变 key，findIndex 即精确新下标
    // - key 找不到（锚行被重建丢弃等极端情况）时回退「行数差」估算：无吸收的
    //   纯前插场景下两者等价
    // - 行高仍可能很大（单个超大 content 段不切分），一次性的 scrollToIndex/
    //   scrollTop 跳转在测量收敛前必然失准；因此恢复后进入 rAF 校正循环：每帧
    //   以锚行实时 start/size 重算目标位置并施加，直到连续多帧稳定（测量收敛）
    //   或超时；前 5 帧校正未落地时忽略手势中断（load-more 由连续上滚触发，
    //   注册监听时手势通常仍在持续，立即让位会停在校正前的位置），之后用户
    //   滚动输入才立即放弃校正
    // - 目标差值含锚行自身 size 差：视口随锚行高度变化前移，语义仍正确
    const first = rowVirtualizer.value.getVirtualItems()[0]
    const prevFirstStart = first?.start ?? 0
    const prevFirstSize = first?.size ?? 0
    const prevScrollTop = wrap?.scrollTop ?? 0
    const prevRowCount = chatRows.value.length
    const prevFirstKey = first?.key ?? null
    await store.loadMoreMessages(agentId.value)
    await nextTick()
    if (!wrap) return
    let anchorIndex = prevFirstKey ? chatRows.value.findIndex(row => row.key === prevFirstKey) : -1
    if (anchorIndex === -1) {
      anchorIndex = chatRows.value.length - prevRowCount
    }
    const anchorKey = chatRows.value[anchorIndex]?.key ?? null
    if (!anchorKey) return

    let aborted = false
    let totalFrames = 0
    const abort = () => {
      // 首批校正未落地前忽略手势：load-more 由连续上滚触发，注册监听时手势通常
      // 仍在持续，立即中止会让视口停在未校正位置（前插内容整体下移造成视觉跳变，
      // 表现为「滚动位置被偷走」）。校正满 5 帧后恢复「用户输入立即让位」语义
      if (totalFrames < 5) return
      aborted = true
    }
    wrap.addEventListener('wheel', abort, { capture: true })
    wrap.addEventListener('touchstart', abort, { capture: true })
    wrap.addEventListener('pointerdown', abort, { capture: true })
    const removeAbortListeners = () => {
      wrap.removeEventListener('wheel', abort, { capture: true })
      wrap.removeEventListener('touchstart', abort, { capture: true })
      wrap.removeEventListener('pointerdown', abort, { capture: true })
    }

    let stableFrames = 0
    const tick = () => {
      if (aborted || !wrap.isConnected) {
        removeAbortListeners()
        return
      }
      const m = rowVirtualizer.value.getMeasurements()[anchorIndex]
      if (!m) {
        removeAbortListeners()
        return
      }
      const desired = Math.max(
        0,
        prevScrollTop + (m.start - prevFirstStart) + (m.size - prevFirstSize)
      )
      totalFrames += 1
      if (Math.abs(wrap.scrollTop - desired) > 1) {
        wrap.scrollTop = desired
        stableFrames = 0
      } else {
        stableFrames += 1
      }
      if (stableFrames < 3 && totalFrames < 90) {
        requestAnimationFrame(tick)
      } else {
        removeAbortListeners()
      }
    }
    requestAnimationFrame(tick)
  } finally {
    isLoadingMore.value = false
  }
}

async function handleChatSend(
  params: Record<string, unknown>,
  attachedFiles: Array<{ id: number; original_name: string; mime_type: string }>,
  message: string
) {
  if (!store.currentSession) {
    const session = await store.createSession(agentId.value!, currentWorkDir.value || undefined)
    if (!session) return
    await store.selectSession(agentId.value!, session)
  }
  store.sendMessage(message, params, attachedFiles, selectedModel.value || undefined)
  await nextTick()
  scrollToBottom()
}

function handleStop() {
  store.interruptExecution()
}

function handleHumanInputSubmit() {
  if (!humanInputValue.value.trim()) {
    ElMessage.warning({ message: '请输入内容', duration: 5000 })
    return
  }
  store.resumeWithInput(humanInputValue.value.trim())
  humanInputValue.value = ''
}

async function handleCompress() {
  if (!agentId.value || !store.currentSession) return
  if (store.isStreaming) {
    ElMessage.warning({ message: '请等待回复完成', duration: 5000 })
    return
  }
  if (store.chatMessages.length === 0) {
    ElMessage.warning({ message: '暂无对话记录', duration: 5000 })
    return
  }
  let compressPrompt = ''
  try {
    const { value } = await ElMessageBox.prompt(
      '将总结全部对话历史为摘要。此操作不可撤销。',
      '压缩上下文',
      {
        type: 'warning',
        inputType: 'textarea',
        inputPlaceholder: '自定义压缩要求（可留空，使用默认提示词）',
        inputValue: ''
      }
    )
    compressPrompt = value.trim()
  } catch {
    return
  }
  try {
    const started = await store.compressSession(
      agentId.value,
      store.currentSession.id,
      compressPrompt
    )
    if (started) {
      ElMessage.info({ message: '正在压缩上下文...', duration: 5000 })
    }
  } catch {
    // store.compressSession handles error internally
  }
}

function handleDeleteMessage(msg: (typeof store.chatMessages)[0]) {
  if (!store.currentSession || !store.currentAgent) return
  if (store.isStreaming) {
    ElMessage.warning({ message: '请等待回复完成', duration: 5000 })
    return
  }
  const match = msg.id.match(/msg-(\d+)/)
  // 消息 id 可能保留流式临时前缀（streaming-/user-），优先取 dbMsgId
  const msgId = msg.dbMsgId ?? (match ? parseInt(match[1]) : null)
  if (msgId == null) {
    ElMessage.warning({ message: '该消息不支持删除', duration: 5000 })
    return
  }

  ElMessageBox.confirm('删除此消息及之后的对话？', '确定', { type: 'warning' })
    .then(async () => {
      const deleted = await store.deleteMessagesFrom(msgId)
      if (deleted) {
        inputMessage.value = deleted.content
        restoreInputParams(deleted)
      }
      ElMessage.success({ message: '已删除，可重新发送', duration: 5000 })
    })
    .catch(() => {})
}

function handleRevertFrom(dbMsgId: number) {
  if (store.isStreaming) {
    ElMessage.warning({ message: '请等待回复完成', duration: 5000 })
    return
  }
  ElMessageBox.confirm('将删除此条及之后的所有内容，确定继续？', '确定', { type: 'warning' })
    .then(async () => {
      const deleted = await store.deleteMessagesFrom(dbMsgId)
      if (deleted) {
        inputMessage.value = deleted.content
        restoreInputParams(deleted)
      }
      ElMessage.success({ message: '已删除', duration: 5000 })
    })
    .catch(() => {})
}

/**
 * 回退恢复：将删除消息时携带的文件与其他输入参数恢复到输入框参数表单
 */
function restoreInputParams(deleted: {
  content: string
  files?: Array<{
    id: number
    original_name: string
    mime_type: string
    file_path?: string
    file_type?: string
    file_size?: number
    preview_url?: string
  }>
  input_data?: Record<string, unknown>
}) {
  const params: Record<string, unknown> = deleted.input_data ? { ...deleted.input_data } : {}

  // 历史 input_data 只保存文件 ID 和基础元信息，优先用撤回接口补全的文件信息。
  if (deleted.files && deleted.files.length > 0) {
    const filesById = new Map(deleted.files.map(file => [file.id, file]))
    for (const field of dynamicFields.value) {
      if (field.type !== 'file_list' || !Array.isArray(params[field.name])) continue
      params[field.name] = (params[field.name] as Array<Record<string, unknown>>).map(file => {
        const fileId = typeof file?.id === 'number' ? file.id : null
        const enriched = fileId == null ? undefined : filesById.get(fileId)
        return enriched ? { ...file, ...enriched } : file
      })
    }
  }

  // 旧消息无 input_data 时回退：把附件放入第一个 file_list 字段
  if (deleted.files && deleted.files.length > 0) {
    const firstFileField = dynamicFields.value.find(f => f.type === 'file_list')
    if (firstFileField && !params[firstFileField.name]) {
      params[firstFileField.name] = deleted.files
    }
  }
  if (Object.keys(params).length > 0) {
    restoreParamsSignal.value = params
  } else {
    // 无参数可恢复时清空信号，避免残留旧值污染后续输入框
    restoreParamsSignal.value = null
  }
}

function formatToolApprovalArgs(args?: Record<string, unknown>): string {
  if (!args) return ''
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return ''
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}:${s.toString().padStart(2, '0')}` : `${s}s`
}

function handleApproveTools() {
  store.approveToolCalls()
}

function handleRejectTools() {
  store.rejectToolCalls()
}
</script>

<template>
  <div class="chat-content" :class="{ 'welcome-mode': isWelcomeMode }">
    <header class="chat-header glass-blur">
      <div class="header-center">
        <div class="status-dot"></div>
        <div class="header-title">
          <div class="agent-name-row">
            <h1>{{ store.currentAgent?.name || 'AI 助手' }}</h1>
            <el-tag v-if="store.planMode" size="small" type="warning" effect="light" round>
              计划模式
            </el-tag>
          </div>
          <span
            v-if="store.currentSession"
            class="session-name"
            :title="store.currentSession.title"
          >
            {{ store.currentSession.title || '新会话' }}
          </span>
        </div>
      </div>
      <div class="header-right">
        <DisplayToggle
          v-model:auto-scroll="autoScroll"
          v-model:show-thinking="showThinking"
          v-model:show-end-output="showEndOutput"
        />
        <el-tooltip content="记忆" placement="bottom">
          <button class="header-action-btn" @click="showMemory = true">
            <el-icon :size="18">
              <Notebook />
            </el-icon>
            <span>记忆</span>
          </button>
        </el-tooltip>
        <el-tooltip content="压缩" placement="bottom">
          <button class="header-action-btn" @click="handleCompress">
            <el-icon :size="18" :class="{ 'is-loading': store.isCompressing }">
              <Operation />
            </el-icon>
            <span>压缩</span>
          </button>
        </el-tooltip>
        <RunningToolBadge />
      </div>
    </header>

    <el-scrollbar
      ref="scrollbarRef"
      v-loading="store.messagesLoading || !messagesRevealed"
      element-loading-text="加载中..."
      class="messages-scrollbar"
      :distance="50"
      wrap-style="overflow-anchor: none"
      @scroll="handleScroll"
      @end-reached="onEndReached"
      @wheel="onUserScrollIntent"
      @touchmove="onUserScrollIntent"
      @pointerdown.capture="handleScrollbarPointerDown"
    >
      <div v-if="isWelcomeMode" class="welcome-wrapper">
        <WelcomePage
          :agent-name="store.currentAgent?.name || 'AI 助手'"
          :agent-description="store.currentAgent?.description"
          :suggested-prompts="store.currentAgent?.suggested_prompts || []"
          @select-prompt="handleSuggestedPrompt"
        />
      </div>
      <div
        v-else
        ref="messagesContentRef"
        class="messages-container"
        :style="{ visibility: messagesRevealed ? 'visible' : 'hidden' }"
      >
        <div v-if="store.hasMoreMessages" class="load-more-sentinel">
          <div v-show="isLoadingMore" class="load-more-dots">
            <span></span>
            <span></span>
            <span></span>
          </div>
        </div>
        <div class="messages-virtual" :style="{ height: `${rowVirtualizer.getTotalSize()}px` }">
          <div
            v-for="row of virtualRows"
            :key="row.key"
            :ref="rowVirtualizer.measureElement"
            :data-index="row.index"
            class="virtual-row"
            :style="{ transform: `translateY(${row.start}px)` }"
          >
            <MessageItem
              :row="chatRows[row.index] ?? null"
              :show-thinking="showThinking"
              :show-end-output="showEndOutput"
              :is-streaming="store.isStreaming"
              @delete="handleDeleteMessage"
              @revert="handleRevertFrom"
              @preview="handleImagePreview"
            />
          </div>
        </div>
      </div>
    </el-scrollbar>

    <Transition v-if="!isWelcomeMode" name="jump-fade">
      <div v-show="!isAtBottom" class="scroll-to-bottom-wrap">
        <div class="scroll-to-bottom" aria-label="回到底部" @click="scrollToBottom">
          <el-icon :size="16">
            <Bottom />
          </el-icon>
        </div>
      </div>
    </Transition>

    <div v-if="store.isCompressing" class="compress-overlay">
      <div class="compress-overlay-card">
        <el-icon :size="24" class="is-loading">
          <Operation />
        </el-icon>
        <span>正在压缩上下文...</span>
      </div>
    </div>

    <div v-if="store.isWaitingHuman" class="human-input-overlay">
      <el-card class="human-input-card">
        <div class="human-input-question">
          <el-icon style="color: #e6a23c; margin-right: 8px">
            <ChatDotRound />
          </el-icon>
          {{ store.currentWaitData?.question || '请提供输入' }}
        </div>
        <div v-if="store.currentWaitData?.context" class="human-input-context">
          {{ store.currentWaitData.context }}
        </div>
        <el-input
          v-model="humanInputValue"
          type="textarea"
          :rows="3"
          placeholder="请输入您的回答..."
          @keydown.enter.ctrl="handleHumanInputSubmit"
        />
        <template #footer>
          <div style="display: flex; justify-content: space-between; width: 100%">
            <el-button :disabled="store.isStopping" @click="handleStop">取消执行</el-button>
            <el-button type="primary" @click="handleHumanInputSubmit">提交并继续</el-button>
          </div>
        </template>
      </el-card>
    </div>

    <div v-if="store.isWaitingToolApproval" class="tool-approval-overlay">
      <el-card class="tool-approval-card">
        <div class="approval-header">
          <el-icon style="color: #e6a23c; margin-right: 8px">
            <Warning />
          </el-icon>
          <span v-if="store.subAgentApproval?.isSubAgent">
            子Agent「{{ store.subAgentApproval.agentName }}」请求执行以下工具：
          </span>
          <span v-else>请求执行以下工具：</span>
          <span class="approval-countdown">{{ formatCountdown(store.approvalCountdown) }}</span>
        </div>
        <div class="approval-tools">
          <div
            v-for="tc in store.pendingToolCalls"
            :key="tc.id || tc.name"
            class="approval-tool-item"
          >
            <div class="approval-tool-name">
              <el-tag
                :type="store.pendingApprovalNeeded.includes(tc.name) ? 'danger' : 'info'"
                size="small"
                style="margin-right: 6px"
              >
                {{ store.pendingApprovalNeeded.includes(tc.name) ? '需确认' : '普通' }}
              </el-tag>
              {{ tc.name }}
            </div>
            <pre class="approval-tool-args">{{ formatToolApprovalArgs(tc.args) }}</pre>
          </div>
        </div>
        <template #footer>
          <div style="display: flex; justify-content: space-between; width: 100%">
            <el-button type="danger" @click="handleRejectTools">拒绝并停止</el-button>
            <el-button type="primary" @click="handleApproveTools">批准执行</el-button>
          </div>
        </template>
      </el-card>
    </div>

    <div v-if="store.flowPreview" class="flow-preview-wrapper">
      <FlowPreviewCard
        :flow-id="store.flowPreview.flow_id"
        :flow-name="store.flowPreview.flow_name"
        :nodes="store.flowPreview.nodes"
        :edges="store.flowPreview.edges"
        :deleted="store.flowPreview.deleted"
        @close="store.flowPreview = null"
      />
    </div>

    <div class="input-wrapper">
      <ChatInput
        ref="chatInputRef"
        v-model:input-message="inputMessage"
        v-model:selected-model="selectedModel"
        :fields="dynamicFields"
        :is-streaming="store.isStreaming"
        :is-stopping="store.isStopping"
        :is-waiting-human="store.isWaitingHuman || store.isWaitingToolApproval"
        :total-tokens="store.totalSessionTokens"
        :latest-prompt-tokens="store.latestPromptTokens"
        :plan-mode="store.planMode"
        :restore-params="restoreParamsSignal"
        :model-options="modelOptions"
        :default-model-label="defaultModelLabel"
        :work-dir="currentWorkDir"
        @send="handleChatSend"
        @stop="handleStop"
        @toggle-plan-mode="store.togglePlanMode"
        @restore-consumed="restoreParamsSignal = null"
        @select-workdir="handleSelectWorkDir"
        @clear-workdir="handleWorkDirConfirm('')"
      />
    </div>

    <MemoryPanel v-model:visible="showMemory" :agent-id="agentId" />
    <ToolOutputDrawer />
    <DirectoryPickerDialog
      v-model="workDirPickerVisible"
      :initial-path="currentWorkDir"
      @confirm="handleWorkDirConfirm"
    />

    <Teleport to="body">
      <el-image-viewer
        v-if="imagePreviewVisible"
        :url-list="imagePreviewUrls"
        :initial-index="imagePreviewIndex"
        @close="closeImagePreview"
        @switch="handlePreviewSwitch"
      />
    </Teleport>
  </div>
</template>

<script lang="ts">
import { ChatDotRound } from '@element-plus/icons-vue'
export default {
  components: { ChatDotRound }
}
</script>

<style scoped>
.chat-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  position: relative;
  background: #fff;
}

.chat-content.welcome-mode {
  background: #fafbfc;
}

.welcome-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding-bottom: 24px;
}

.chat-header {
  height: 56px;
  flex-shrink: 0;
  padding: 0 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  z-index: 50;
}

.header-center {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-title {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 1px;
}

.agent-name-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.header-center h1 {
  margin: 0;
  min-width: 0;
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-name {
  min-width: 0;
  max-width: min(32vw, 320px);
  overflow: hidden;
  color: #64748b;
  font-size: 12px;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10b981;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.5;
  }
}

.header-right {
  display: flex;
  align-items: center;
  gap: 2px;
}

.header-action-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  background: none;
  border: none;
  color: #64748b;
  cursor: pointer;
  border-radius: 8px;
  transition: all 0.2s;
  font-size: 10px;
  gap: 2px;
}

.header-action-btn:hover {
  color: #2563eb;
  background: #f8fafc;
}

.messages-scrollbar {
  flex: 1;
}

.messages-scrollbar :deep(.el-scrollbar__view) {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

.messages-container {
  flex-shrink: 0;
  padding: 32px 24px;
}

.empty-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
}

.scroll-to-bottom-wrap {
  position: absolute;
  bottom: 175px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  z-index: 50;
  pointer-events: none;
}

.scroll-to-bottom-wrap .scroll-to-bottom {
  pointer-events: auto;
}

.scroll-to-bottom:hover {
  background: #eff6ff;
  color: #2563eb;
}

.load-more-sentinel {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 32px;
  padding: 8px 0;
}

/* 虚拟滚动容器：高度由 virtualizer totalSize 驱动，行绝对定位 */
.messages-virtual {
  position: relative;
  max-width: 896px;
  margin: 0 auto;
}

.virtual-row {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
}

.load-more-dots {
  display: flex;
  gap: 6px;
}

.load-more-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #cbd5e1;
  animation: load-more-pulse 1.2s ease-in-out infinite;
}

.load-more-dots span:nth-child(2) {
  animation-delay: 0.2s;
}

.load-more-dots span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes load-more-pulse {
  0%,
  80%,
  100% {
    opacity: 0.3;
    transform: scale(0.8);
  }

  40% {
    opacity: 1;
    transform: scale(1.2);
  }
}

.compress-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255, 255, 255, 0.8);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 99;
}

.compress-overlay-card {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  color: #334155;
  background: #fff;
  padding: 16px 28px;
  border-radius: 12px;
  box-shadow:
    0 2px 15px -3px rgba(0, 0, 0, 0.07),
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
}

.human-input-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 100;
}

.human-input-card {
  width: 500px;
  max-width: 90%;
}

.human-input-card :deep(.el-card__body) {
  padding-bottom: 0;
}

.human-input-question {
  font-size: 16px;
  font-weight: 500;
  margin-bottom: 16px;
  display: flex;
  align-items: flex-start;
}

.human-input-context {
  background: #f8fafc;
  padding: 12px;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 14px;
  white-space: pre-wrap;
  color: #475569;
  max-height: 200px;
  overflow-y: auto;
}

.human-input-card :deep(.el-card__footer) {
  padding-top: 16px;
  text-align: right;
}

.input-wrapper {
  flex-shrink: 0;
  border-top: 1px solid #f1f5f9;
  background: rgba(255, 255, 255, 0.5);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  padding: 14px 24px 16px;
  position: relative;
  z-index: 40;
}

.input-wrapper::before {
  content: '';
  position: absolute;
  bottom: -10px;
  left: 50%;
  transform: translateX(-50%);
  width: 75%;
  height: 40px;
  background: rgba(37, 99, 235, 0.06);
  filter: blur(40px);
  border-radius: 9999px;
  pointer-events: none;
}

.tool-approval-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 100;
}

.tool-approval-card {
  width: 560px;
  max-width: 90%;
  max-height: 80vh;
}

.tool-approval-card :deep(.el-card__body) {
  padding-bottom: 0;
  overflow-y: auto;
  max-height: calc(80vh - 120px);
}

.approval-header {
  font-size: 16px;
  font-weight: 500;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
}

.approval-countdown {
  margin-left: auto;
  font-size: 13px;
  color: #94a3b8;
  font-weight: 400;
}

.approval-tools {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.approval-tool-item {
  background: #f8fafc;
  border-radius: 8px;
  padding: 12px;
}

.approval-tool-name {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
}

.approval-tool-args {
  font-size: 12px;
  color: #475569;
  background: #1e293b;
  color: #e2e8f0;
  padding: 10px 12px;
  border-radius: 6px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}

.tool-approval-card :deep(.el-card__footer) {
  padding-top: 16px;
  text-align: right;
}

.flow-preview-wrapper {
  flex-shrink: 0;
  padding: 5px 24px;
  max-height: 250px;
  overflow: hidden;
}

@media (max-width: 768px) {
  .messages-container {
    padding: 20px 16px;
  }

  .input-wrapper {
    padding: 14px;
  }

  .header-center h1 {
    font-size: 14px;
  }

  .session-name {
    max-width: 35vw;
    font-size: 11px;
  }
}
</style>

<style>
/* 搜索结果跳转高亮（作用于子组件根元素，需非 scoped 样式） */
.message.msg-highlight {
  animation: msg-highlight-flash 2s ease-out;
  border-radius: 12px;
}

@keyframes msg-highlight-flash {
  0% {
    background-color: rgba(64, 158, 255, 0.25);
    box-shadow: 0 0 0 3px rgba(64, 158, 255, 0.35);
  }

  100% {
    background-color: transparent;
    box-shadow: 0 0 0 3px transparent;
  }
}
</style>
