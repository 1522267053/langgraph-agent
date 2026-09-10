<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { useAgentStore } from '@/stores'
import { ElMessage, ElMessageBox, ElImageViewer } from 'element-plus'
import type { ScrollbarDirection, ScrollbarInstance } from 'element-plus'
import {
  Operation,
  Bottom,
  Notebook,
  Warning,
  Document,
  MoreFilled,
  Loading
} from '@element-plus/icons-vue'
import { agentApi } from '@/api/agent'
import { flowApi } from '@/api/flow'
import { aiProviderApi } from '@/api/ai_provider'
import { providerConnectionApi } from '@/api/aiProviderConnection'
import type { FlowIOField } from '@/types/flow'
import type { AgentFileChangeInfo } from '@/types/agent'
import DisplayToggle from '@/components/AgentChat/DisplayToggle.vue'
import MemoryPanel from '@/components/AgentChat/MemoryPanel.vue'
import MessageItem from '@/components/AgentChat/MessageItem.vue'
import RunningToolBadge from '@/components/AgentChat/RunningToolBadge.vue'
import ToolOutputDrawer from '@/components/AgentChat/ToolOutputDrawer.vue'
import WelcomePage from '@/components/AgentChat/WelcomePage.vue'
import QuestionDialog from '@/components/AgentChat/QuestionDialog.vue'
import FileChangePanel from '@/components/AgentChat/FileChangePanel.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import DirectoryPickerDialog from '@/components/common/DirectoryPickerDialog.vue'
import ChatInput from '@/components/AgentChat/ChatInput.vue'
import FlowPreviewCard from '@/components/common/FlowPreviewCard.vue'
import {
  buildChatRows,
  estimateRowSize,
  rememberRowSize,
  clearRowSizeCache,
  type ChatRow
} from '@/components/AgentChat/chatRow'
import { clearBlockExpandOverrides } from '@/components/AgentChat/blockExpand'
import { useToolOutputStore } from '@/stores/toolOutput'
import { formatCountdown } from '@/utils/format'
import { loadWorkDirForAgent, saveWorkDirForAgent } from '@/utils/workdir'
import { loadPlanModeForAgent } from '@/utils/planmode'

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

// ---- 贴底跟随（TanStack Virtual end-anchored + 本地跟随强制器）----
// anchorTo:'end' 提供 prepend 历史视口稳定与基础钉底；但库内 wasAtEnd
// （虚拟距离门控）与 followOnAppend（元素距离门控）在估算先行入账/实测
// 滞后的交错窗口会互相毒化导致跟随中断，由 syncAtEnd 内的强制器兜底。
// 本地派生态：
// - isAtEnd：元素距离贴底（回底按钮显隐）
// - followPinned：跟随锁存（门控强制器与最新工具行展开），用户上滚手势
//   解除、真正贴底（≤2px）重新锁存——行高不反向耦合几何判定，杜绝振荡
const autoScroll = ref(true)
const isAtEnd = ref(true)
const followPinned = ref(true)
/** 贴底阈值：按钮派生与跟随强制器共用的离底判定口径 */
const SCROLL_END_THRESHOLD = 60

// 内容增长检测基线：跟随强制器只在内容真正变高时出手，用户键盘翻页等
// 无手势的滚动路径不会被误拉回底部
let lastKnownScrollHeight = 0

function syncAtEnd(): void {
  const v = rowVirtualizer.value
  const el = messagesContainer.value
  if (!el) return
  const grew = el.scrollHeight > lastKnownScrollHeight + 1
  lastKnownScrollHeight = el.scrollHeight
  // 贴底判定用元素距离（真实滚动空间口径）：跟随由下方强制器锚定在真实
  // 底部，稳态 elDist≈0；虚拟距离（totalSize 口径）受估算先行/塌缩级联
  // 双向污染，曾在 elDist 232px 时假报贴底、误重锁跟随锁存把上滚拽回
  const elDist = Math.max(el.scrollHeight - el.scrollTop - el.clientHeight, 0)
  isAtEnd.value = elDist <= SCROLL_END_THRESHOLD
  // 重锁存仅在真正贴底（≤2px）时发生：上滚第一格 elDist 即超过该阈值，
  // 杜绝滞后窗口中的假性重锁；用户手动滚回底部时正常重锁
  if (elDist <= 2) followPinned.value = true
  // [跟随强制器] 库内两套跟随门控在「估算先行入账、实测滞后补偿」的交错
  // 窗口会互相毒化：totalSize 先跳变推高虚拟距离 → wasAtEnd 放弃补偿；
  // 未测 DOM 抬高元素距离 → followOnAppend 拒绝触发，跟随就此中断。只要
  // 锁存未解除、内容确实变高且不在真实底部，直接强制回底兜底
  if (followPinned.value && autoScroll.value && grew && elDist > 4) {
    v.scrollToEnd()
  }
}

function scrollToLatest(): void {
  followPinned.value = true
  // 乐观置位：scrollToEnd 的目标位置经 scroll 事件异步入账，立即 sync 会读到
  // 滚动前的旧 scrollOffset 而误报离底
  isAtEnd.value = true
  rowVirtualizer.value.scrollToEnd()
}

/** 用户上滚手势：解除跟随锁存（真实输入才解除，程序化位移不影响） */
function onUserScrollUpIntent(): void {
  followPinned.value = false
}

function onWheel(event: WheelEvent): void {
  if (event.deltaY < 0) onUserScrollUpIntent()
}

function handleScrollbarPointerDown(event: PointerEvent): void {
  const root = scrollbarRef.value?.$el as Element | undefined
  const target = event.target
  if (
    root instanceof Element &&
    target instanceof Element &&
    target.closest('.el-scrollbar') === root &&
    target.closest('.el-scrollbar__bar')
  ) {
    onUserScrollUpIntent()
  }
}

// 切窗口：隐藏期渲染暂停（rAF/RO 延迟）而流式内容照常增长，恢复可见时若
// 隐藏前贴底则强制回底，随后的跟随强制器无缝接管
let wasAtEndOnHide = true
function handleVisibilityChange(): void {
  if (document.hidden) {
    wasAtEndOnHide = isAtEnd.value
    return
  }
  syncAtEnd()
  if (wasAtEndOnHide && autoScroll.value) scrollToLatest()
}
document.addEventListener('visibilitychange', handleVisibilityChange)
onUnmounted(() => {
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})

// ---- 虚拟滚动（@tanstack/vue-virtual 挂在 el-scrollbar 的原生滚动 wrap 上）----
// 行模型为「段级」：AI 回合每个 segment 独占一行（见 AgentChat/chatRow.ts），
// 消息级 UI（头像/头部/尾部）拆分到 first/last 行

// 流式中但最后一条不是 AI 消息（或列表为空）时，追加独立的输入指示器行
const showStandaloneTyping = computed(() => {
  if (!store.isStreaming) return false
  const last = store.chatMessages.at(-1)
  return !last || last.role !== 'ai' || last.displayType === 'context-summary'
})

// 跟随锁存（followPinned）门控最新工具行自动展开：上滚阅读时全部工具行折叠为
// 摘要行，流式期间的程序性展开/收起不再造成行高突变与虚拟滚动位置漂移
const chatRows = computed<ChatRow[]>(() =>
  buildChatRows(
    store.chatMessages,
    showStandaloneTyping.value,
    store.isStreaming,
    followPinned.value
  )
)

// 展示开关：声明须在 rowVirtualizer 之前（estimateSize 闭包在 setup 期间同步求值）
const showThinking = ref(true)
// 结束节点输出按钮：默认不展示，右上角"展示"下拉勾选后显示
const showEndOutput = ref(false)

// 消息滚动容器宽度：供 chatRow 内容感知估值按实际宽度折行（窄屏防低估）。
// 用 RO 观察覆盖窗口缩放与侧栏开合；变化时不调 measure()——已挂载行由
// virtualizer 的 RO 重测、未挂载行走实测缓存，ref 更新只改善后续首挂载行估值
const contentWidth = ref(0)
let widthObserver: ResizeObserver | null = null
if (typeof ResizeObserver !== 'undefined') {
  widthObserver = new ResizeObserver(entries => {
    contentWidth.value = entries[0]?.contentRect.width ?? 0
  })
  onUnmounted(() => {
    widthObserver?.disconnect()
    widthObserver = null
  })
}
watch(
  messagesContainer,
  el => {
    if (!widthObserver) return
    widthObserver.disconnect()
    if (el) {
      widthObserver.observe(el)
      contentWidth.value = el.clientWidth
    } else {
      contentWidth.value = 0
    }
  },
  { flush: 'post' }
)

const rowVirtualizer = useVirtualizer<HTMLDivElement, HTMLDivElement>({
  get count() {
    return chatRows.value.length
  },
  getScrollElement: () => messagesContainer.value as HTMLDivElement | null,
  estimateSize: (index: number) =>
    estimateRowSize(chatRows.value[index], {
      showThinking: showThinking.value,
      containerWidth: contentWidth.value
    }),
  overscan: 8,
  getItemKey: (index: number) => chatRows.value[index]?.key ?? String(index),
  // 官方聊天模式（virtual-core 3.17+）：列表末端为稳定锚——prepend 历史按
  // keyed item 自动保持视口、流式行增长自动钉底、followOnAppend 仅在贴底
  // 阈值内跟随新输出（autoScroll 偏好关闭时不跟随，getter 保持响应式）
  anchorTo: 'end',
  followOnAppend: autoScroll.value,
  scrollEndThreshold: SCROLL_END_THRESHOLD
})

// 贴底补偿回调：保留行高实测缓存写入（未挂载行重挂的估值兜底），补偿规则
// 与库内置默认一致——首测行顶越过滚动位即补偿；重测仅整体在滚动位上方且非
// 后向滚动时补偿（防 #1218 级联）。贴底钉住（anchorTo:'end' 的 wasAtEnd
// 分支）的尺寸补偿在库内独立处理，不经过本回调
rowVirtualizer.value.shouldAdjustScrollPositionOnItemSizeChange = (item, delta, instance) => {
  const first = !instance.itemSizeCache.has(item.key)
  if (delta !== 0) rememberRowSize(item.key, item.size + delta)
  const offset = (instance.scrollOffset ?? 0) + instance.scrollAdjustments
  return first
    ? item.start < offset
    : item.start + item.size <= offset && instance.scrollDirection !== 'backward'
}

const virtualRows = computed(() => rowVirtualizer.value.getVirtualItems())

// 贴底派生态随任意虚拟化变化（数据增删/测量更新/滚动）刷新
watch(virtualRows, () => syncAtEnd(), { immediate: true })

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
/** 文件变更 Diff 抽屉显隐 */
const showFileChanges = ref(false)
/** 每次打开抽屉立即拉取最新数据（store 侧 reqSeq 防竞态，快速开关不会旧响应覆盖新响应）；
 *  进入会话时 store 已预拉取，SSE file_changed 持续增量更新 */
async function openFileChangesPanel() {
  showFileChanges.value = true
  await store.fetchFileChanges()
}
/** 回退恢复信号：每次回退生成新对象，通知当前挂载的 ChatInput 恢复参数 */
const restoreParamsSignal = ref<Record<string, unknown> | null>(null)

// ---- 临时模型切换（跨供应商：选项 = LLM 节点自有供应商 + 已启用的供应商连接）----
interface ChatModelOption {
  /** 复合键 `${provider}::${model_id}`（模型 id 跨供应商可能重复） */
  value: string
  label: string
  multimodal: boolean
  provider: string
  providerLabel: string
}

/** 复合键分隔符：provider_id 不含冒号，安全 */
const MODEL_VALUE_SEP = '::'

function toModelValue(provider: string, modelId: string): string {
  return `${provider}${MODEL_VALUE_SEP}${modelId}`
}

function parseModelValue(value: string): { provider: string; model: string } | null {
  const idx = value.indexOf(MODEL_VALUE_SEP)
  if (idx <= 0) return null
  return { provider: value.slice(0, idx), model: value.slice(idx + MODEL_VALUE_SEP.length) }
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

/** 按供应商分组（ChatInput 用 el-option-group 展示） */
const modelGroups = computed(() => {
  const groups: { label: string; options: ChatModelOption[] }[] = []
  const byLabel = new Map<string, ChatModelOption[]>()
  for (const opt of modelOptions.value) {
    let list = byLabel.get(opt.providerLabel)
    if (!list) {
      list = []
      byLabel.set(opt.providerLabel, list)
      groups.push({ label: opt.providerLabel, options: list })
    }
    list.push(opt)
  }
  return groups
})

/** 当前选中的覆盖模型+供应商（未选返回 null，走 Agent 默认配置） */
function resolveSelectedModel(): { provider: string; model: string } | null {
  if (!selectedModel.value) return null
  return parseModelValue(selectedModel.value)
}

/**
 * 加载当前 Agent 可切换的模型列表：LLM 节点自有供应商 + 全部已启用的供应商连接；
 * 失败时静默降级：下拉框隐藏、发送走 Agent 默认模型
 */
async function loadModelSelection(id: number) {
  modelOptions.value = []
  defaultModelLabel.value = ''
  // 重置/恢复 selectedModel 都不触发持久化（watcher 为 pre-flush，nextTick 后才放行）
  restoringModelPref = true
  selectedModel.value = ''
  try {
    // 节点自有供应商模型 + 启用连接的模型分组，并行拉取
    const [flowRes, groupsRes] = await Promise.allSettled([
      flowApi.get(id),
      providerConnectionApi.modelGroups()
    ])

    const options: ChatModelOption[] = []
    const seen = new Set<string>()

    if (groupsRes.status === 'fulfilled') {
      for (const group of groupsRes.value.data.data || []) {
        for (const m of group.models) {
          const value = toModelValue(group.provider_id, m.model_id)
          if (seen.has(value)) continue
          seen.add(value)
          options.push({
            value,
            label: m.name,
            multimodal: (m.modalities?.input || []).some(t =>
              ['image', 'video', 'audio', 'pdf'].includes(t)
            ),
            provider: group.provider_id,
            providerLabel: group.provider_label
          })
        }
      }
    }

    let nodeProvider = ''
    if (flowRes.status === 'fulfilled') {
      const llmNode = (flowRes.value.data.data?.nodes || []).find(n => n.node_type === 'llm')
      if (llmNode?.base_config) {
        nodeProvider = String(llmNode.base_config.provider || '')
        defaultModelLabel.value = String(llmNode.base_config.model || '')
      }
    }
    // 节点自有供应商（连接分组里未覆盖时）追加为独立分组
    if (nodeProvider && !options.some(o => o.provider === nodeProvider)) {
      try {
        const modelsRes = await aiProviderApi.getModels(nodeProvider)
        for (const m of modelsRes.data.data || []) {
          const value = toModelValue(nodeProvider, m.model_id)
          if (seen.has(value)) continue
          seen.add(value)
          options.push({
            value,
            label: m.name,
            multimodal: (m.modalities?.input || []).some(t =>
              ['image', 'video', 'audio', 'pdf'].includes(t)
            ),
            provider: nodeProvider,
            providerLabel: nodeProvider
          })
        }
      } catch {
        // 节点供应商模型列表加载失败不阻塞
      }
    }

    modelOptions.value = options
    const stored = loadStoredModel(id)
    if (stored && options.some(o => o.value === stored)) {
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

// ---- Agent 级「记住的工作路径」：helper 已提取到 utils/workdir.ts（多入口复用）----

/** 弹窗定位优先级：当前会话已设置 > Agent 记忆值 > 空（盘符列表） */
const effectiveInitialPath = computed(
  () => currentWorkDir.value || loadWorkDirForAgent(agentId.value) || ''
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
        // 服务端权威值（标准化后的路径）写记忆；空串清 key 防脏数据
        saveWorkDirForAgent(agentId.value, res.data.data.work_dir || '')
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
  // 无会话阶段也写入记忆：首次创建会话后再次打开弹窗即可定位
  saveWorkDirForAgent(agentId.value, path)
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
    // 强制贴底并锁存跟随：防止收敛期 scroll 事件的几何重算把贴底判定翻成
    // false 残留，后续流式钉底由 end-anchored 的 wasAtEnd 分支接管
    followPinned.value = true
    rowVirtualizer.value.scrollToEnd()
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
      rowVirtualizer.value.scrollToEnd()
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
        rowVirtualizer.value.scrollToEnd()
        lastHeight = height
        lastChangeAt = now
      }
      if (now - lastChangeAt >= 400 || now >= followUntil) {
        rowVirtualizer.value.scrollToEnd()
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
    await nextTick()
    convergeScrollToBottom()
  }
)

watch(
  () => store.messageRefreshVersion,
  async () => {
    await nextTick()
    // 流结束的消息刷新/删除/回退后刷新贴底派生态；跟随语义由库内
    // followOnAppend 与锚定接管，此处仅同步按钮与锁存状态
    syncAtEnd()
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
  // 历史前插会推高 scrollHeight，显式解除跟随锁存，防止跟随强制器把视口
  // 拉回底部
  followPinned.value = false
  try {
    // anchorTo:'end' 下前插历史按 keyed item 自动保持视口位置，无需手工锚行恢复
    await store.loadMoreMessages(agentId.value)
    await nextTick()
    syncAtEnd()
  } finally {
    isLoadingMore.value = false
  }
}

async function handleChatSend(
  params: Record<string, unknown>,
  attachedFiles: Array<{ id: number; original_name: string; mime_type: string }>,
  message: string
) {
  // 解析前端可解析的全部路径值
  // 优先级 pendingWorkDir > Agent 记忆值（localStorage） > 空
  // pendingWorkDir 是当前 AgentChat 内已选过的最新值；
  // 记忆值是上次会话留下的偏好（按 Agent 隔离）——用户未点过按钮时兜底
  const workDirForNew = pendingWorkDir.value || loadWorkDirForAgent(agentId.value)
  // 计划模式同理：新建会话继承 Agent 维度记忆（欢迎页预开的开关已随 toggle 写入记忆）
  const planModeForNew = loadPlanModeForAgent(agentId.value)

  if (!store.currentSession) {
    // 场景 1：完全没有 session（如首次进入页面、刷新后无历史 session）
    const session = await store.createSession(
      agentId.value!,
      workDirForNew || undefined,
      planModeForNew || undefined
    )
    if (!session) return
    await store.selectSession(agentId.value!, session)
  } else if (!store.currentSession.work_dir && workDirForNew) {
    // 场景 2：已有 session（onMounted 自动选中 / 切换）但 work_dir 为空
    // 自动用前端偏好回填——避免历史 session 没有工作路径时每次都要手动重选
    try {
      const res = await agentApi.updateWorkDir(
        agentId.value!,
        store.currentSession.id,
        workDirForNew
      )
      if (res.data.code === 1 && res.data.data) {
        store.currentSession.work_dir = res.data.data.work_dir
      }
    } catch {
      // error handled by interceptor
    }
  }
  const override = resolveSelectedModel()
  store.sendMessage(message, params, attachedFiles, override?.model, override?.provider)
  await nextTick()
  scrollToLatest()
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

async function handleQuestionSubmit(answers: string[]) {
  await store.resolveQuestion(answers)
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

/** mobile 下拉菜单触发：转发到对应按钮的 handler */
async function onOverflowCommand(command: string) {
  if (command === 'files') {
    await openFileChangesPanel()
  } else if (command === 'compress') {
    await handleCompress()
  } else if (command === 'background') {
    toolOutputStore.drawerVisible = true
  }
}

/** 回退确认弹窗状态：展示将恢复的文件清单并提供三种回退方式 */
const revertDialog = reactive({
  visible: false,
  messageId: 0,
  loading: false,
  executing: false,
  files: [] as AgentFileChangeInfo[]
})

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
  openRevertDialog(msgId)
}

function handleRevertFrom(dbMsgId: number) {
  if (!store.currentSession || !store.currentAgent) return
  if (store.isStreaming) {
    ElMessage.warning({ message: '请等待回复完成', duration: 5000 })
    return
  }
  openRevertDialog(dbMsgId)
}

async function openRevertDialog(msgId: number) {
  revertDialog.messageId = msgId
  revertDialog.files = []
  revertDialog.visible = true
  revertDialog.loading = true
  try {
    const res = await agentApi.revertPreview(agentId.value, store.currentSession!.id, msgId)
    if (res.data.code === 1) {
      revertDialog.files = res.data.data.files
    }
  } catch {
    // error handled by interceptor；预览失败仍允许仅回退消息
  } finally {
    revertDialog.loading = false
  }
}

type RevertMode = 'message' | 'message_files' | 'files'

async function executeRevert(mode: RevertMode) {
  if (!store.currentSession || !store.currentAgent) return
  const msgId = revertDialog.messageId
  revertDialog.executing = true
  try {
    if (mode === 'files') {
      // 仅恢复文件：对话保持不动
      const res = await agentApi.restoreFilesOnly(agentId.value, store.currentSession.id, msgId)
      if (res.data.code === 1) {
        notifyFileRestore(res.data.data.reverted_files, res.data.data.ok_count)
        // 批量回退在后端标记 is_reverted，重拉对齐 badge 与面板
        void store.fetchFileChanges()
        revertDialog.visible = false
      }
      return
    }
    const deleted = await store.deleteMessagesFrom(msgId, mode === 'message_files')
    if (deleted) {
      inputMessage.value = deleted.content
      restoreInputParams(deleted)
      // 回退后列表缩短，强制贴底（不受跟随锁存与用户位置限制）
      scrollToLatest()
      notifyFileRestore(deleted.reverted_files ?? [])
      // 批量回退在后端标记 is_reverted，重拉对齐 badge 与面板
      void store.fetchFileChanges()
      ElMessage.success({ message: '已回退，可重新发送', duration: 5000 })
    }
    revertDialog.visible = false
  } finally {
    revertDialog.executing = false
  }
}

/** 提示文件恢复结果：成功数 + 失败项警告 */
function notifyFileRestore(files: AgentFileChangeInfo[], okCount?: number) {
  if (!files.length) return
  const ok = okCount ?? files.filter(f => f.status === 'ok').length
  ElMessage.success({ message: `已恢复 ${ok} 个文件`, duration: 5000 })
  const failed = files.filter(f => f.status && f.status !== 'ok')
  if (failed.length > 0) {
    ElMessage.warning({
      message: `${failed.length} 个文件恢复失败（备份缺失或文件不可访问）`,
      duration: 5000
    })
  }
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
        <div class="header-overflow-wrapper">
          <el-tooltip content="文件变更" placement="bottom">
            <el-badge
              :value="store.fileChangesCount"
              :max="9"
              :hidden="store.fileChangesCount === 0"
              :offset="[-4, 4]"
            >
              <button
                class="header-action-btn"
                :class="{ active: showFileChanges }"
                @click="openFileChangesPanel"
              >
                <el-icon :size="18">
                  <Document />
                </el-icon>
                <span>文件</span>
              </button>
            </el-badge>
          </el-tooltip>
        </div>
        <div class="header-overflow-wrapper">
          <el-tooltip content="压缩" placement="bottom">
            <button class="header-action-btn" @click="handleCompress">
              <el-icon :size="18" :class="{ 'is-loading': store.isCompressing }">
                <Operation />
              </el-icon>
              <span>压缩</span>
            </button>
          </el-tooltip>
        </div>
        <div class="header-overflow-wrapper">
          <RunningToolBadge />
        </div>

        <el-dropdown
          class="header-overflow-trigger"
          trigger="click"
          placement="bottom-end"
          @command="onOverflowCommand"
        >
          <button class="header-action-btn" aria-label="更多操作">
            <el-icon :size="18">
              <MoreFilled />
            </el-icon>
            <span>更多</span>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="files">
                <el-icon class="overflow-item-icon"><Document /></el-icon>
                文件变更
                <span v-if="store.fileChangesCount > 0" class="overflow-item-badge">
                  {{ store.fileChangesCount > 9 ? '9+' : store.fileChangesCount }}
                </span>
              </el-dropdown-item>
              <el-dropdown-item command="compress" :disabled="store.isCompressing">
                <el-icon class="overflow-item-icon" :class="{ 'is-loading': store.isCompressing }">
                  <Operation />
                </el-icon>
                {{ store.isCompressing ? '正在压缩…' : '压缩' }}
              </el-dropdown-item>
              <el-dropdown-item command="background">
                <el-icon class="overflow-item-icon"><Loading /></el-icon>
                后台
                <span v-if="toolOutputStore.runningCount > 0" class="overflow-item-badge">
                  {{ toolOutputStore.runningCount }}
                </span>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <el-scrollbar
      ref="scrollbarRef"
      v-loading="store.messagesLoading || !messagesRevealed"
      element-loading-text="加载中..."
      class="messages-scrollbar"
      :distance="50"
      wrap-style="overflow-anchor: none"
      @scroll="syncAtEnd"
      @end-reached="onEndReached"
      @wheel="onWheel"
      @touchmove.passive="onUserScrollUpIntent"
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
      <!-- 跟随进行中（锁存+自动滚动）即使瞬时离底（估算窗口）也不显示 -->
      <div v-show="!isAtEnd && !(followPinned && autoScroll)" class="scroll-to-bottom-wrap">
        <div class="scroll-to-bottom" aria-label="回到底部" @click="scrollToLatest">
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
            子Agent「{{ store.subAgentApproval.agentName }}」
          </span>
          <span v-if="store.approvalProgress.total > 1">
            工具 {{ store.approvalProgress.current }}/{{ store.approvalProgress.total }}
          </span>
          <span v-else>请求执行工具</span>
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
            <el-button type="danger" @click="handleRejectTools">拒绝</el-button>
            <el-button type="primary" @click="handleApproveTools">批准</el-button>
          </div>
        </template>
      </el-card>
    </div>

    <div v-if="store.flowPreview" class="flow-preview-wrapper">
      <FlowPreviewCard
        :flow-id="store.flowPreview.flow_id"
        :flow-name="store.flowPreview.flow_name"
        :flow-type="store.flowPreview.flow_type"
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
        :model-groups="modelGroups"
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
    <FileChangePanel v-model:visible="showFileChanges" />
    <ToolOutputDrawer />
    <QuestionDialog
      :question="store.pendingQuestion"
      :sub-agent-name="store.subAgentQuestion?.isSubAgent ? store.subAgentQuestion.agentName : ''"
      @submit="handleQuestionSubmit"
      @expire="store.dismissExpiredQuestion"
    />
    <DirectoryPickerDialog
      v-model="workDirPickerVisible"
      :initial-path="effectiveInitialPath"
      @confirm="handleWorkDirConfirm"
    />

    <el-dialog
      v-model="revertDialog.visible"
      title="回退到此消息"
      width="560px"
      :close-on-click-modal="false"
    >
      <div v-loading="revertDialog.loading" class="revert-body">
        <p class="revert-tip">将删除此消息及之后的所有对话，原始输入会恢复到输入框。</p>
        <template v-if="revertDialog.files.length > 0">
          <p class="revert-subtitle">检测到以下被 AI 更改的文件，可选择一并恢复：</p>
          <ul class="revert-files">
            <li v-for="file in revertDialog.files" :key="file.file_path" class="revert-file-item">
              <span class="revert-file-path" :title="file.file_path">{{ file.file_path }}</span>
              <el-tag size="small" :type="file.change_type === 'create' ? 'danger' : 'warning'">
                {{ file.change_type === 'create' ? '将删除' : '将还原' }}
              </el-tag>
            </li>
          </ul>
        </template>
        <p v-else-if="!revertDialog.loading" class="revert-empty">
          未追踪到可自动恢复的文件变更（shell 命令产生的文件变更无法恢复）
        </p>
      </div>
      <template #footer>
        <el-button @click="revertDialog.visible = false">取消</el-button>
        <el-button
          v-if="revertDialog.files.length > 0"
          :disabled="revertDialog.executing"
          @click="executeRevert('files')"
        >
          仅恢复文件
        </el-button>
        <el-button
          v-if="revertDialog.files.length > 0"
          type="primary"
          :loading="revertDialog.executing"
          @click="executeRevert('message_files')"
        >
          回退消息并恢复文件
        </el-button>
        <el-button
          v-else
          type="primary"
          :loading="revertDialog.executing"
          @click="executeRevert('message')"
        >
          回退消息
        </el-button>
      </template>
    </el-dialog>

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
  width: 40px;
  height: 40px;
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

.header-action-btn.active {
  color: #2563eb;
  background: #eff6ff;
}

/* 下拉菜单条目：图标 + 文字 + 角标水平对齐 */
.overflow-item-icon {
  margin-right: 6px;
  vertical-align: middle;
  font-size: 14px;
}

.overflow-item-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  margin-left: 6px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  color: #fff;
  background: var(--el-color-danger);
  border-radius: 9px;
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

/* 移动端：右侧工具栏收纳进「···」下拉菜单，避免横向溢出被裁切 */
.header-right {
  flex-shrink: 0;
}

/* overflow-wrapper: desktop 默认透明（display:contents 让 wrapper 不占 box，子元素直接参与父 flex） */
.header-overflow-wrapper {
  display: contents;
}

/* desktop 默认隐藏「···」触发器（mobile 媒体查询内再覆盖） */
.header-overflow-trigger {
  display: none;
}

/* mobile：隐藏 wrapper（整组 tooltip+badge+button 都不渲染）；触发器显示 */
@media (max-width: 768px) {
  .header-overflow-wrapper {
    display: none;
  }

  .header-overflow-trigger {
    display: flex;
  }

  /* mobile 视口小、按钮少，把按钮间距拉大一点（desktop 默认 gap:2px 偏挤） */
  .header-right {
    gap: 4px;
  }
}

/* 回退确认弹窗 */
.revert-body {
  min-height: 60px;
}

.revert-tip {
  margin: 0 0 8px;
  color: var(--el-text-color-primary);
  font-size: 14px;
}

.revert-subtitle {
  margin: 0 0 8px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.revert-files {
  margin: 0;
  padding: 8px 12px;
  max-height: 220px;
  overflow-y: auto;
  list-style: none;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.revert-file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 3px 0;
  font-size: 12px;
}

.revert-file-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl;
  text-align: left;
  color: var(--el-text-color-regular);
  font-family: monospace;
}

.revert-empty {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
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
