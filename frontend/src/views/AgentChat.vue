<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useVirtualizer } from '@tanstack/vue-virtual'
// 库内 elementScroll 拦截入口（实测口径贴底守卫，见下方 guardedElementScroll）
import { elementScroll as libElementScroll } from '@tanstack/virtual-core'
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
import { aiProviderApi, type ReasoningMeta, parseReasoningOptions } from '@/api/ai_provider'
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

// ---- 贴底跟随（TanStack Virtual end-anchored + 库内 followOnAppend 接管）----
// 库内 anchorTo:'end' + followOnAppend:true 统一接管贴底跟随与 prepend 视口
// 稳定；本地仅维护派生态：
// - isAtEnd：元素距离贴底（回底按钮显隐）
// - followPinned：跟随锁存（用户上滚手势解除、真正贴底 ≤2px 重锁——带
//   PIN_RELOCK_GRACE_MS 抑制窗），喂给库内 scrollEndThreshold 联动
// - rescue：流式中真实离底超阈值时主动 scrollToEnd 补救（见 virtualRows watch）
const autoScroll = ref(true)
const isAtEnd = ref(true)
const followPinned = ref(true)
/** 贴底阈值（统一为 80px）：库内 scrollEndThreshold:80（useVirtualizer 配置）
 * 与本地 isAtEnd / followPinned 重锁共用同一常数，消除"两套阈值不同步"导致的
 * 跟随中断 / 回底按钮过早消失 / 重锁抑制窗错位。与库内 wasAtEnd 判定窗口同口径。
 * 调整此项：同时改库内 scrollEndThreshold 配置（当前值见 useVirtualizer 调用点） */
const SCROLL_END_PX = 80

/** 重锁抑制窗：wheel 解除锁存与视口实际位移之间有几帧延迟，期间 elDist 仍
 * ≤2px，立即重锁会与库内钉底互相强化形成「滚不动」循环（用户需连滚两次），
 * 解除后 300ms 内禁止重锁 */
const PIN_RELOCK_GRACE_MS = 300
let pinUnlockAt = 0

/**
 * 状态刷新器（替代旧版强制器）。
 *
 * 库内 anchorTo:'end' + followOnAppend:true 已统一接管：
 *   - 贴底跟随（流式增长）
 *   - prepend 视口稳定（keyed item + scrollAdjustments）
 *   - 跟随强制（wasAtEnd 门控 + followOnAppend 5s 追底 rAF）
 *
 * 本函数仅负责本地派生 UI 状态：
 *   - isAtEnd：贴底判定（用于回到底部按钮显隐）
 *   - followPinned：跟随锁存（用户上滚解除 → 库内 followOnAppend 停跟）
 *
 * 不写 scrollTop。库内 handler 会处理。
 */
function syncAtEnd(): void {
  const el = messagesContainer.value
  if (!el) return
  // 贴底判定用元素距离（真实滚动空间口径）：跟随由库内 followOnAppend 锚定在
  // 真实底部，稳态 elDist≈0；虚拟距离（totalSize 口径）受估算先行/塌缩级联
  // 双向污染，曾在 elDist 232px 时假报贴底、误重锁跟随锁存把上滚拽回
  const elDist = Math.max(el.scrollHeight - el.scrollTop - el.clientHeight, 0)
  isAtEnd.value = elDist <= SCROLL_END_PX
  // 重锁仅在真正贴底（≤2px）且不在手势解除抑制窗内发生：上滚第一格 elDist
  // 即超过该阈值，杜绝滞后窗口中的假性重锁；用户手动滚回底部时正常重锁
  if (elDist <= 2 && performance.now() - pinUnlockAt > PIN_RELOCK_GRACE_MS) {
    followPinned.value = true
  }
}

function scrollToLatest(): void {
  followPinned.value = true
  isAtEnd.value = true
  // 用库内 scrollToEnd：库内有 5s 追底 reconcile rAF 与 isScrolling 状态机协调；
  // 直写 scrollTop 会绕过 scrollEndThreshold 判定，触发"重锁抑制窗"竞态
  rowVirtualizer.value.scrollToEnd()
  // SSE 结束路径（用户主动回底 / 发送后定位）复用 rescue 收敛循环：末行真实
  // 高度由 ResizeObserver 实测落地晚于本次调用，单发 scrollToEnd 锚定的是
  // 估算高度，实测落地后 dist 残余（实测常 >80px，恰好裁掉 footer/计划卡片
  // 底部）无人追赶——「回到底部点了但没到底」即此缺口。循环有界（宽限
  // RESCUE_GRACE_MS、贴底即退、用户上滚让位），覆盖该收敛窗口
  if (autoScroll.value) startRescueLoop()
}

/** 用户上滚手势：解除跟随锁存（真实输入才解除，程序化位移不影响）。
 * 不做嵌套边界穿透：聊天语境下用户在 thinking 块等嵌套容器内的 wheel
 * 同样表达「滚聊天」的意图，外层 followPinned 应同步解除；thinking 块内
 * 的自动贴底由 AIMessageContent 自己的 thinkingFollowOff 控制，与外层
 * 独立——如果 short-circuit 外层，流式增长时外层 followPinned 仍为 1，
 * 会把视口拽回底部（用户感知「往上滚不会定住」） */
function onUserScrollUpIntent(): void {
  followPinned.value = false
  pinUnlockAt = performance.now()
}

/** onWheel 仅读 event.deltaY，永不调 preventDefault()，因此模板上必须用
 * @wheel.passive 修饰符（与 @touchmove.passive 同口径），否则浏览器在滚动
 * 事件回调同步返回前不能滚动，Chrome DevTools 报 [Violation]
 * "Added non-passive event listener to a scroll-blocking ... event" */
function onWheel(event: WheelEvent): void {
  if (event.deltaY < 0) onUserScrollUpIntent()
}

function handleScrollbarPointerDown(event: PointerEvent): void {
  const root = scrollbarRef.value?.$el as Element | undefined
  const target = event.target
  const hitScrollbar =
    root instanceof Element &&
    target instanceof Element &&
    target.closest('.el-scrollbar') === root &&
    !!target.closest('.el-scrollbar__bar')
  if (hitScrollbar) {
    // 滚动条拖动只在外层 .el-scrollbar__bar 上触发，目标已在外层，
    // 不会被误判为嵌套块内滚动
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

// 空窗期独立 typing 行：send 后 isStreaming=true 但 AI 消息对象尚未创建
// （onFlowStart 的 startStreaming() 不带 forceNewMessage，首个 chunk flush 时
// getOrCreateStreamingMessage() 才 push），此窗口 chatMessages 末条是 human，
// 无行承载头像/三点 → 视口底部空白。typing 行（52px）补位该窗口，AI 消息
// 创建后消失、由 MessageBubble 内 waiting-dots 接管。
// 此前因「typing 行同步入表干扰 scrollToLatest 时序」被禁用；现 rescue loop
// 持续钉真实底部（el.scrollTop = scrollHeight），52px 增删当帧追平，禁用理由消失
const showStandaloneTyping = computed(
  () =>
    store.isStreaming &&
    (store.chatMessages.length === 0 ||
      store.chatMessages[store.chatMessages.length - 1]?.role !== 'ai')
)

// 工具行恒为折叠状态行、点击头部展开回看（业界模式）——chatRows 不依赖
// followPinned 与流式状态，行高在流式期间保持稳定
const chatRows = computed<ChatRow[]>(() =>
  buildChatRows(store.chatMessages, showStandaloneTyping.value)
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

// ---- 库内滚动写入守卫（实测口径贴底判定）----
// 必须先于 useVirtualizer 声明：options.scrollToFn 引用本常量，置后会因
// TDZ 崩溃（Cannot access before initialization）。
//
// 根因（SINK 日志实锤，2026-09-18）：TanStack Virtual resizeItem 的 wasAtEnd
// 用【估算口径】（totalSize - clientHeight - scrollOffset）判定贴底，流式期间
// 估算恒滞后实测（totalSize=1996 vs scrollH=2032）。用户上滚 100px 后：本地
// 实测 elDist=100（回底按钮显示），库内 vDist≈62≤80 仍判「贴底」→ 每次行高
// 增长执行 scrollTop += adjustments 补偿 → 视口被往下拽（表现为「内容整体
// 上移、滚动条不动、回底按钮常驻」）。
//
// 守卫：followPinned=false（用户上滚）时，拦截 adjustments>0 的补偿写入，
// 改为 adjustments=undefined 透传（拒绝位移）。库内账面（scrollOffset/
// scrollAdjustments）经透传触发的原生 scroll 事件与真实 DOM 自动同步，
// 无积累性漂移。pinned=true 的贴底跟随 / scrollToEnd / rescue 不受影响。
const guardedElementScroll = (
  offset: number,
  {
    adjustments,
    behavior
  }: { adjustments?: number | undefined; behavior?: string | undefined },
  instance: never
) => {
  // 用户已上滚且这是「往下拽」的补偿写入：拒绝位移
  if (!followPinned.value && (adjustments ?? 0) > 0) {
    return libElementScroll(offset, { adjustments: undefined, behavior }, instance)
  }
  return libElementScroll(offset, { adjustments, behavior }, instance)
}

const rowVirtualizer = useVirtualizer<HTMLDivElement, HTMLDivElement>({
  get count() {
    return chatRows.value.length
  },
  getScrollElement: () => messagesContainer.value as HTMLDivElement | null,
  estimateSize: (index: number) =>
    estimateRowSize(chatRows.value[index], {
      showThinking: showThinking.value,
      showEndOutput: showEndOutput.value,
      containerWidth: contentWidth.value
    }),
  overscan: 8,
  getItemKey: (index: number) => chatRows.value[index]?.key ?? String(index),
  // 官方 chat 模式（virtual-core 3.13+）：anchorTo:'end' + followOnAppend: true
  // 由库内统一接管 prepend 视口稳定 + 流式贴底。手写补偿 + 自定义
  // shouldAdjustScrollPositionOnItemSizeChange 与库内两套门控互相毒化（曾经
  // 在「估算先行入账 / 实测滞后」交错窗口出现 prepend 后漂 12558px），已删除
  // 实测口径贴底守卫：pinned=false 时拦截库内补偿写入（见 guardedElementScroll）
  scrollToFn: guardedElementScroll as never,
  anchorTo: 'end',
  followOnAppend: true,
  // 文档推荐静态值 80：scrollEndThreshold 是库内 wasAtEnd 判定窗口，
  // 流式段级增长时 rowCount 不变，followOnAppend 不出手；resizeItem 只
  // 保持视口位置稳定（不会主动滚到底）。用户上滚解锁 followPinned 后，
  // 本地 isAtEnd + UI 层回底按钮显隐由 syncAtEnd 控制，不干预库内阈值。
  // 实测：getter 返回 20-40px 太小，用户在距底 40-80px 区间被库内判为
  // 「不在底部」，流式增长时不跟随 → 红框滚条停在中间
  scrollEndThreshold: SCROLL_END_PX
})

const virtualRows = computed(() => rowVirtualizer.value.getVirtualItems())

// ---- 流式收敛追底循环（rescue loop）----
// 职责：新行真实渲染落地（实测高度替换估算）引起的 scrollHeight 增量，在
// followPinned 期间持续 scrollToEnd 追平，消除「半截工具卡片」空窗期。
// 退出条件（任一满足即停）：
//   - followPinned=false——用户上滚手势，立即让位（与库内语义一致）
//   - document.hidden——后台标签页不滚动，恢复可见由 visibilitychange 处理
//   - 流结束后：高度连续 RESCUE_STABLE_MS 不变且贴底（收敛完成），
//     或超过 RESCUE_GRACE_MS 宽限期
//   - messagesContainer 卸载（onUnmounted 兜底清理）
// 流式期间【不】因「高度稳定」退出：SSE chunk 间歇常 >150ms，若间歇期退出，
// 随后 tool 卡片挂载/实测落地（RO 回调晚于 chunk）无人追赶 → 半截块复现。
// 流式中每帧只读 scrollHeight（静止帧零写入，不触发额外 reflow），常驻成本
// 与首屏 converge 循环同级；写入频率仍被高度变化节拍天然限制。
const RESCUE_STABLE_MS = 150
const RESCUE_GRACE_MS = 2500
const RESCUE_AT_END_PX = 8
let rescueRafId = 0
let rescueStableSince = 0
let rescueDeadline = 0

function stopRescueLoop(): void {
  if (rescueRafId) {
    cancelAnimationFrame(rescueRafId)
    rescueRafId = 0
  }
}

function startRescueLoop(): void {
  // 幂等：循环已在跑则仅续期宽限窗口（新一轮内容仍在到达）
  if (rescueRafId) {
    rescueDeadline = performance.now() + RESCUE_GRACE_MS
    return
  }
  const startAt = performance.now()
  rescueDeadline = startAt + RESCUE_GRACE_MS
  rescueStableSince = startAt
  const tick = () => {
    rescueRafId = 0
    const el = messagesContainer.value
    if (!el || !el.isConnected) {
      return
    }
    const now = performance.now()
    // 用户上滚 / 页面隐藏：立即退出，不写 scrollTop
    if (!followPinned.value || document.hidden) {
      return
    }
    const height = el.scrollHeight
    const dist = Math.max(height - el.scrollTop - el.clientHeight, 0)
    if (dist > RESCUE_AT_END_PX) {
      // 关键：直写真实底部，不调用 rowVirtualizer.scrollToEnd——
      // 日志实证：末行未实测时（measurementsCache 无此项）getOffsetForIndex
      // 返回 undefined，scrollToEnd 回退到 getTotalSize()（虚拟估算总高），
      // 估算短于真实 scrollHeight 时 scrollTop 落在虚拟底（dist 残余 ~85px
      // 停滞到下一次行变化）→ 头像+三点被裁在视口外。
      // 直写 el.scrollTop 触发原生 scroll 事件 → 库内 observeElementOffset
      // 同步 scrollOffset，与首屏 convergeScrollToBottom 同一写入路径。
      el.scrollTop = el.scrollHeight
      rescueStableSince = now
    } else if (
      !store.isStreaming &&
      now - rescueStableSince >= RESCUE_STABLE_MS
    ) {
      // 流已结束、已贴底（dist ≤ 8）且高度稳定：收敛完成
      return
    }
    // 流已结束：仅宽限期内继续追赶（最后一块渲染落地），超窗停
    if (!store.isStreaming && now > rescueDeadline) {
      return
    }
    rescueRafId = requestAnimationFrame(tick)
  }
  rescueRafId = requestAnimationFrame(tick)
}

// ---- 微任务级钉底（MutationObserver，消除 rAF 一帧滞后的下沉感）----
// 帧序取证（rAF 逐帧轨迹日志）证实：贴底模式下几乎每个内容增长帧都呈「h 先涨、
// top 下一帧才追」形态——RO 回调驱动的高度更新发生在帧内 rAF 之后（帧序：
// rAF → RO → layout → paint），rescue 的 rAF 追赶永远晚一帧，中间态（h 已涨、
// top 未追）被绘制 → 每个增长帧可见一次下沉，SSE 结束最后一块落地时幅度最大。
// MO 回调在 DOM 变更后的微任务同步执行，【早于】本帧 layout/paint——在中间态
// 被绘制之前就把 scrollTop 钉到真实底部，下沉在结构上不可见。
// 门控：followPinned（用户上滚让位）+ 真实离底 > 2px（静止时零写入）。
// childList+subtree+attributes 覆盖行增删与行内容尺寸变化；仅读 scrollHeight/
// 写 scrollTop，无 Vue 响应式介入，常驻成本可忽略。
function pinToBottomIfPinned(): void {
  const el = messagesContainer.value
  if (!el || !el.isConnected) return
  if (!followPinned.value || document.hidden) return
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight
  if (dist > 2) el.scrollTop = el.scrollHeight
}

const pinObserver =
  typeof MutationObserver !== 'undefined' ? new MutationObserver(pinToBottomIfPinned) : null

// messagesContentRef 容器在 welcome 模式下是 v-else 条件渲染——首屏 onMounted 时
// 尚未挂载（此前 MO 的 observe 因此早退从未执行）。独立 rAF 轮询直到就绪：
// 不能寄生在拦截器的 poll 里——wrapRef（滚动元素）通常先于 messagesContentRef
// 就绪，拦截器直接安装后 poll 不再运行，MO 的重试就被跳过（已实证的漏洞）
let moInstalled = false
let moPollRaf = 0
function tryInstallPinObserver(): void {
  if (moInstalled || !pinObserver) return
  const root = messagesContentRef.value
  if (!root) {
    if (moPollRaf) return
    const deadline = performance.now() + 10000
    const poll = () => {
      moPollRaf = 0
      if (moInstalled) return
      const el = messagesContentRef.value
      if (!el && performance.now() <= deadline) {
        moPollRaf = requestAnimationFrame(poll)
        return
      }
      tryInstallPinObserver()
    }
    moPollRaf = requestAnimationFrame(poll)
    return
  }
  moInstalled = true
  // messagesContentRef 是虚拟行挂载容器（.messages-container，el-scrollbar view 层），
  // 观察其子树即覆盖全部行 DOM 增删与内容变化
  pinObserver.observe(root, {
    childList: true,
    subtree: true,
    attributes: true,
    // characterData 必须开启：流式 chunk 更新 markdown 走文本节点变更，
    // false 时 MO 全程静默（逐帧轨迹日志实证 dh/dtop 分离一帧的中间态被 paint）。
    // MO 回调是微任务，先于本帧 paint——文本一变就同帧钉底，下沉不可见
    characterData: true
  })
}
onMounted(() => {
  tryInstallPinObserver()
})
onUnmounted(() => {
  if (moPollRaf) cancelAnimationFrame(moPollRaf)
  pinObserver?.disconnect()
})

// 贴底派生态随任意虚拟化变化（数据增删/测量更新/滚动）刷新——库内 followOnAppend
// 已处理贴底，本地 syncAtEnd 仅刷新 isAtEnd / followPinned 状态，不再写 scrollTop
// 流式期间跟随中断补救：持续收敛追底循环（见 startRescueLoop），watcher 仅负责点火

watch(
  virtualRows,
  () => {
    syncAtEnd()

    // 流式期间跟随中断补救：库内 resizeItem (virtual-core line 916-917) 取
    // prevTotalSize 时已含新尺寸，导致 applyScrollAdjustment(getTotalSize - prevTotalSize)
    // 的 delta=0、不补偿 scrollTop → scrollTop 落后 libMaxScroll → elDist 累积
    // → 库内 isAtEnd 翻 false → 后续跟随中断。
    //
    // 单发 rAF rescue 的缺口：新行（尤其 tool 卡片）按估算高度（30/58px 固定值）
    // 首次锚定后，真实渲染落地（markdown/摘要常 >80px）在一帧内把 elDist 推超
    // SCROLL_END_PX，而单发 rAF 早已跑完（当时实测未落地，distNow ≤ 8 提前退
    // 出）——空窗期内无人接管，用户看到半截工具卡片（下一个 chunk 到来才自愈）。
    //
    // 改为持续收敛追底循环：跟随 scrollHeight 变化持续 scrollToEnd，直到高度
    // 稳定且贴底 / 用户解锁 / 页面隐藏 / 宽限期截止。复用 convergeScrollToBottom
    // 的「骑高度收敛」思路，但走库内 scrollToEnd 路径（与 observer 同步兼容），
    // 且仅在高度变化帧出手，频率天然被渲染节拍限制。
    const el = messagesContainer.value
    // 判据用真实元素距离，不用 rowVirtualizer.isAtEnd（虚拟距离口径）：
    // 第一段输出期间估算先行让 totalSize 偏小 → vDist 假小 → 库内假报贴底，
    // scrollToEnd 单次锚定留下的基线偏差（~45px，恰好裁掉 footer 三点），
    // 钉底补偿只跟随增量不回填基线，libAtEnd=1 让 rescue 全程静默直到
    // 第二段出现 keyed 重锚才自愈
    const realDist = el
      ? Math.max(el.scrollHeight - el.scrollTop - el.clientHeight, 0)
      : 0
    if (store.isStreaming && followPinned.value && realDist > 8) {
      startRescueLoop()
    }
  },
  { immediate: true }
)

// ---- SSE 结束后的末程收敛（fix：流结束滚动条停在离底 ~85px）----
// 根因：结束路径 onFlowDone → refreshStreamMessages 替换 chatMessages（含最后
// 一块「实施计划」等卡片）→ stopStreaming() 翻 isStreaming=false；末行真实高度
// 由 ResizeObserver 实测落地【晚于】isStreaming 翻 false，此时：
//   - virtualRows watcher 的点火门控含 isStreaming，已不再触发 rescue
//   - messageRefreshVersion watcher 仅 syncAtEnd() 刷新按钮态，不写 scrollTop
// → 末行实测撑大 scrollHeight 后无人追赶，滚动条停在中途、最后一块卡片被裁。
// 补救：isStreaming true→false 沿触发一次带宽限的收敛循环，追赶实测落地的
// 高度增量；有界（宽限 RESCUE_GRACE_MS、贴底即退、用户上滚/页面隐藏让位），
// 与流式中的 rescue 循环幂等复用同一实例
watch(
  () => store.isStreaming,
  (streaming, wasStreaming) => {
    if (wasStreaming && !streaming && autoScroll.value && followPinned.value) {
      startRescueLoop()
    }
  }
)

// 结束刷新（refreshStreamMessages 在 onFlowDone/onError 中把 DB 权威行写入
// chatMessages 并 bump messageRefreshVersion）可能整体改写行高（占位气泡 →
// 完整渲染卡片），实测收敛同样晚于本 watcher；若仍贴底则点火有界收敛，
// followPinned=false（用户上滚查看历史）时不打扰
watch(
  () => store.messageRefreshVersion,
  async () => {
    await nextTick()
    const el = messagesContainer.value
    const realDist = el
      ? Math.max(el.scrollHeight - el.scrollTop - el.clientHeight, 0)
      : 0
    if (realDist > 8 && autoScroll.value && followPinned.value) {
      startRescueLoop()
    }
  }
)

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

// 欢迎页结构上已脱离 el-scrollbar（模板 v-if 分流），无 loading 遮罩依赖；
// chatMessages 出现时 isWelcomeMode 翻 false → el-scrollbar 才挂载，
// 此时 messagesRevealed 的初始 false 配合 converge 正常走收敛贴底流程

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
      if (await store.restoreLastSession(targetId)) {
        // 已恢复该 Agent 上次会话位置（localStorage 记录的页码 + 会话 id）
      } else if (store.sessions.length > 0) {
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
// 持久化双写（对标计划模式）：会话字段 DB 权威 + Agent 维度 localStorage 记忆
// （无会话阶段暂存 / 新建会话继承）；有会话时选择即落库，切会话按字段恢复
interface ChatModelOption {
  /** 复合键 `${provider}::${model_id}`（模型 id 跨供应商可能重复） */
  value: string
  label: string
  multimodal: boolean
  provider: string
  providerLabel: string
  /** 归一化推理深度档位（null=无元数据，UI 隐藏深度选择） */
  reasoningOptions?: ReasoningMeta | null
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
const REASONING_PREF_KEY = 'agent-chat-reasoning'

const modelOptions = ref<ChatModelOption[]>([])
const defaultModelLabel = ref('')
const selectedModel = ref('')
/** 会话级推理深度覆盖（空=跟随节点配置；对标 selectedModel 的双写持久化） */
const selectedReasoning = ref('')
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
  // Agent 维度记忆：新建会话继承最近偏好（无会话阶段也持续记录）
  try {
    const raw = localStorage.getItem(MODEL_PREF_KEY)
    const map = raw ? (JSON.parse(raw) as Record<string, string>) : {}
    if (model) map[agentId.value] = model
    else delete map[agentId.value]
    localStorage.setItem(MODEL_PREF_KEY, JSON.stringify(map))
  } catch {
    // ignore
  }
  // 会话级落库（对标计划模式）：复合键拆回 provider/model，空串表示清除回退默认；
  // chat_model 只存裸模型 ID（压缩链路直接读库当模型名，带前缀会被供应商判为不存在）
  const parsed = model ? parseModelValue(model) : null
  // 切模型后深度随选择联动落库：清模型时 reasoning 一并清（后端同语义）
  void store.updateSessionChatModel(
    parsed?.model ?? model,
    parsed?.provider,
    selectedReasoning.value || null
  )
})

// ---- 推理深度选项（当前选中模型的档位；无元数据/未选模型 → null 隐藏控件）----
const currentReasoningOptions = computed<ReasoningMeta | null>(() => {
  if (!selectedModel.value) return null
  const opt = modelOptions.value.find(o => o.value === selectedModel.value)
  return opt?.reasoningOptions ?? null
})

function loadStoredReasoning(id: number): string {
  try {
    const raw = localStorage.getItem(REASONING_PREF_KEY)
    if (!raw) return ''
    const map = JSON.parse(raw) as Record<string, string>
    return typeof map[id] === 'string' ? map[id] : ''
  } catch {
    return ''
  }
}

/**
 * 恢复推理深度（切会话/切模型/选项加载后调用）：
 * 会话 chat_reasoning 优先（有会话时字段权威），空则回退 Agent 维度记忆值；
 * 恢复值不在当前模型档位内时清空（档位失效防御，与 watch 校验同语义）
 */
function syncSelectedReasoning() {
  const session = store.currentSession
  let target = ''
  if (session?.chat_reasoning) {
    target = session.chat_reasoning
  } else if (agentId.value) {
    target = loadStoredReasoning(agentId.value)
  }
  const meta = currentReasoningOptions.value
  if (target && meta && !meta.values.includes(target)) target = ''
  if (target && !meta) target = ''
  restoringModelPref = true
  selectedReasoning.value = target
  void nextTick(() => {
    restoringModelPref = false
  })
}

// Agent 维度记忆 + 会话级落库（空串=清除，跟随节点配置）
watch(selectedReasoning, reasoning => {
  if (restoringModelPref || !agentId.value) return
  try {
    const raw = localStorage.getItem(REASONING_PREF_KEY)
    const map = raw ? (JSON.parse(raw) as Record<string, string>) : {}
    if (reasoning) map[agentId.value] = reasoning
    else delete map[agentId.value]
    localStorage.setItem(REASONING_PREF_KEY, JSON.stringify(map))
  } catch {
    // ignore
  }
  // 仅在有临时模型时随模型一起落库；无模型覆盖时深度只做 Agent 记忆，
  // 发送时经 handleChatSend 透传（避免仅深度变化触发 chat-model 端点误清模型）
  if (selectedModel.value) {
    const parsed = parseModelValue(selectedModel.value)
    void store.updateSessionChatModel(
      parsed?.model ?? selectedModel.value,
      parsed?.provider,
      reasoning || null
    )
  }
})

// 切模型后校验深度档位：不在新模型支持列表内自动清空（前端双保险，后端规则②兜底）
watch(currentReasoningOptions, meta => {
  if (restoringModelPref || !selectedReasoning.value) return
  if (!meta || !meta.values.includes(selectedReasoning.value)) {
    selectedReasoning.value = ''
  }
})

/**
 * 从会话字段恢复选中模型（切会话/换 Agent/选项加载后调用）：
 * chat_model 有效时优先；为空或模型已失效时回退 Agent 维度记忆值
 * （对标 work_dir 偏好回退，历史会话未落库时保持记忆体验，发送时才静默回填）
 */
function syncSelectedModelFromSession() {
  const session = store.currentSession
  let target = ''
  if (session?.chat_model) {
    const composite = session.chat_provider
      ? toModelValue(session.chat_provider, session.chat_model)
      : session.chat_model
    if (modelOptions.value.some(o => o.value === composite)) target = composite
  }
  if (!target) {
    const stored = agentId.value ? loadStoredModel(agentId.value) : ''
    if (stored && modelOptions.value.some(o => o.value === stored)) target = stored
  }
  // 恢复属于纯 UI 同步，挂起持久化 watcher 防止误写记忆/会话
  restoringModelPref = true
  selectedModel.value = target
  void nextTick(() => {
    restoringModelPref = false
  })
  // 模型确定后联动恢复深度（会话 chat_reasoning 优先，记忆回退，档位失效清空）
  syncSelectedReasoning()
}

// 切换会话时按会话字段恢复临时模型（有会话时字段为权威，空则回退记忆值）
watch(
  () => store.currentSession?.id,
  () => syncSelectedModelFromSession()
)

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
            providerLabel: group.provider_label,
            reasoningOptions: parseReasoningOptions(m.reasoning_options)
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
            providerLabel: nodeProvider,
            reasoningOptions: parseReasoningOptions(m.reasoning_options)
          })
        }
      } catch {
        // 节点供应商模型列表加载失败不阻塞
      }
    }

    modelOptions.value = options
    // 恢复选中：会话字段优先，空/失效回退 Agent 记忆值
    // （restoringModelPref 仍为 true，恢复不触发持久化；finally 中放行）
    syncSelectedModelFromSession()
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
    if (sessionId) {
      await store.loadSessions(agentId.value)
      const target = store.sessions.find(s => s.id === parseInt(sessionId))
      if (target) {
        await store.selectSession(agentId.value, target)
      } else {
        await store.selectSession(agentId.value, {
          id: parseInt(sessionId)
        } as (typeof store.sessions)[0])
      }
    } else if (await store.restoreLastSession(agentId.value)) {
      // 已恢复上次会话位置（localStorage 记录的页码 + 会话 id）
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
  stopRescueLoop()
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
    // 同步 scrollOffset 流程：
    // 1. reveal：触发元素可见 → 库内 _willUpdate 在下一微任务挂 observer
    // 2. nextTick 后调 rowVirtualizer.scrollToEnd()：走 elementScroll → 触发
    //    scroll 事件 → observeElementOffset 把 scrollOffset 同步到当前 scrollTop
    //
    // 关键时序：隐藏期逐帧直写的 scrollTop 不会被库内 scroll 事件感知
    // （observer 在 reveal 后才挂上），若 finish 时仍直写 scrollTop，
    // scrollOffset 会永远停在初始 0，isAtEnd 永远 false，followOnAppend
    // 永不触发 → SSE 流式不自动滚动。库内 scrollToEnd 走相同 elementScroll
    // 路径但保证 observer 同步 scrollOffset
    followPinned.value = true
    messagesRevealed.value = true
    nextTick(() => {
      rowVirtualizer.value.scrollToEnd()
    })
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
      wrap.scrollTop = wrap.scrollHeight
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
        wrap.scrollTop = wrap.scrollHeight
        lastHeight = height
        lastChangeAt = now
      }
      if (now - lastChangeAt >= 400 || now >= followUntil) {
        wrap.scrollTop = wrap.scrollHeight
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
    // 流结束的消息刷新/删除/回退后刷新贴底派生态；跟随语义由强制器与
    // 锚定接管，此处仅同步按钮与锁存状态
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
  // 历史前插：库内 anchorTo:'end' + keyed item 自动锚定原首行同一像素位置；
  // 我们仅解除跟随锁存（库内 followOnAppend 根据 isAtEnd 判断是否出手，跟随
  // 锁存对应 UI 层 followPinned，控制回底按钮显隐）
  let anchorKey: string | null = null
  const elEarly = messagesContainer.value
  const beforeTop = elEarly?.scrollTop ?? 0
  const beforeHeight = elEarly?.scrollHeight ?? 0
  anchorKey = rowVirtualizer.value?.getVirtualItems()[0]?.key ?? null
  followPinned.value = false
  try {
    const loadedCount = await store.loadMoreMessages(agentId.value)
    // [FIX-IMMEDIATE] 立即用 scrollHeight delta 补偿 scrollTop，避免库内 anchorTo
    // 在 AFTER-NXT 阶段把视口拉到 prepend 新历史位置造成"一闪而过"。store 内部
    // await nextTick 已结束，DOM 已同步更新，scrollHeight 已反映新高度。
    const elAfter = messagesContainer.value
    if (elAfter && loadedCount > 0) {
      const afterHeight = elAfter.scrollHeight
      const delta = afterHeight - beforeHeight
      // 立即把 scrollTop 加上 delta，保持原首行像素位置
      elAfter.scrollTop = beforeTop + delta
    }
    await nextTick()
    // 等两帧 rAF：让新增行的 ResizeObserver 首测完成、虚拟列表重新排版。
    // 不再写 scrollTop——库内 anchorTo:'end' 已按 keyed item 自动维持视口
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    syncAtEnd()
    // [FIX] prepend 视口漂移兜底：方向 A 的 scrollToOffset 修复保留作为兜底，
    // 防止库内 anchorTo 在 rAF 期间再次拉走视口；同时校正因"估值先行入账/实测
    // 收缩"造成的 sub-pixel drift。
    if (anchorKey && rowVirtualizer.value && messagesContainer.value) {
      const el = messagesContainer.value
      const anchorIdx = chatRows.value.findIndex(r => r.key === anchorKey)
      if (anchorIdx >= 0) {
        const postItems = rowVirtualizer.value.getVirtualItems()
        const anchorVirt = postItems.find(v => v.key === anchorKey)
        const targetStart = anchorVirt?.start
        if (targetStart != null) {
          rowVirtualizer.value.scrollToOffset(targetStart, { align: 'start' })
        } else {
          rowVirtualizer.value.scrollToIndex(anchorIdx, { align: 'start' })
        }
      }
    }
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
  const override = resolveSelectedModel()
  // 推理深度仅在选中临时模型时随请求透传（未选模型=跟随节点配置，
  // 后端 override 也只对切换后的模型生效）；无会话新建时随 createSession 落库
  const reasoningOverride = override && selectedReasoning.value ? selectedReasoning.value : undefined

  if (!store.currentSession) {
    // 场景 1：完全没有 session（如首次进入页面、刷新后无历史 session）
    // 临时模型随会话创建落库（对标计划模式随 createSession 传入）
    const session = await store.createSession(
      agentId.value!,
      workDirForNew || undefined,
      planModeForNew || undefined,
      override?.model,
      override?.provider,
      reasoningOverride
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
  // 场景 3：已有会话 chat_model 为空但当前选中了临时模型——发送前静默回填落库
  // （历史会话未落库时恢复阶段回退记忆值，此处让展示与 DB 保持一致）
  if (store.currentSession && !store.currentSession.chat_model && override) {
    await store.updateSessionChatModel(
      override.model,
      override.provider,
      reasoningOverride || null
    )
  }
  store.sendMessage(
    message,
    params,
    attachedFiles,
    override?.model,
    override?.provider,
    reasoningOverride
  )
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
      // 行表骤减后 virtualizer 尺寸缓存（measuredSizes/内部 measurementsCache）
      // 仍持旧行高，视口计算可能落在空区间 → 页面空白；nextTick 后强制整体
      // 重测（行 key 不变，已挂载行由 RO 重测收敛），再贴底
      await nextTick()
      rowVirtualizer.value.measure()
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
            <el-tag v-if="store.planMode" size="small" class="plan-mode-tag" effect="light" round>
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

    <!-- 欢迎页完全在 el-scrollbar 之外：滚动容器/加载遮罩的挂载与重测
         均不波及 welcome 内容，从结构上消除进入时的「一下一上」抖动
         （此前 welcome 在 el-scrollbar 内，loading 遮罩移除触发内部重测，
         welcome-wrapper 自然高度与视口钳制高度两帧不一致 → 抖动） -->
    <div v-if="isWelcomeMode" class="welcome-wrapper">
      <WelcomePage
        :agent-name="store.currentAgent?.name || 'AI 助手'"
        :agent-description="store.currentAgent?.description"
        :suggested-prompts="store.currentAgent?.suggested_prompts || []"
        @select-prompt="handleSuggestedPrompt"
      />
    </div>

    <el-scrollbar
      v-else
      ref="scrollbarRef"
      v-loading="store.messagesLoading || !messagesRevealed"
      element-loading-text="加载中..."
      class="messages-scrollbar"
      :distance="SCROLL_END_PX"
      wrap-style="overflow-anchor: none"
      @scroll="syncAtEnd"
      @end-reached="onEndReached"
      @wheel.passive="onWheel"
      @touchmove.passive="onUserScrollUpIntent"
      @pointerdown.capture="handleScrollbarPointerDown"
    >
      <div
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
        v-model:selected-reasoning="selectedReasoning"
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
        :reasoning-options="currentReasoningOptions"
        :work-dir="currentWorkDir"
        @send="handleChatSend"
        @stop="handleStop"
        @toggle-plan-mode="store.togglePlanMode"
        @restore-consumed="restoreParamsSignal = null"
        @select-workdir="handleSelectWorkDir"
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
  background: var(--paper);
}

.chat-content.welcome-mode {
  background: var(--paper);
}

.welcome-wrapper {
  /* 欢迎页已脱离 el-scrollbar（模板 v-if 分流），作为 chat-content 的直接
     子元素与 header 平级：flex:1 占满 header 以下空间，内部自身居中。
     此前在 el-scrollbar 内时曾因「自然高度+padding 超出视口 14px +
     loading 遮罩移除触发重测」产生「一下一上」，结构性消除 */
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
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
  font-family: var(--font-serif);
  font-size: 16px;
  font-weight: 600;
  color: var(--paper-ink);
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 计划模式 tag：替换 Element warning 淡黄，用朱砂实底强化模式信号 */
.plan-mode-tag {
  --el-tag-bg-color: var(--vermilion);
  --el-tag-border-color: var(--vermilion);
  --el-tag-text-color: #fff;
  font-weight: 600;
  letter-spacing: 0.02em;
  flex-shrink: 0;
}

.session-name {
  min-width: 0;
  max-width: min(32vw, 320px);
  overflow: hidden;
  color: var(--paper-ink-4);
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
  color: var(--paper-ink-3);
  cursor: pointer;
  border-radius: 8px;
  transition: all 0.2s;
  font-size: 10px;
  gap: 2px;
}

.header-action-btn:hover {
  color: var(--paper-ink);
  background: var(--paper-warm);
}

.header-action-btn.active {
  color: var(--vermilion);
  background: var(--vermilion-soft);
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
  padding: 36px 24px 0px 24px;
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
  background: var(--vermilion-soft);
  color: var(--vermilion);
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
  background: rgba(250, 249, 246, 0.8);
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
  color: var(--paper-ink-2);
  background: var(--paper-card);
  padding: 16px 28px;
  border-radius: 12px;
  border: 1px solid var(--paper-line);
  box-shadow:
    0 2px 15px -3px rgba(60, 50, 35, 0.08),
    0 4px 6px -2px rgba(60, 50, 35, 0.05);
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
  background: var(--paper-warm);
  padding: 12px;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 14px;
  white-space: pre-wrap;
  color: var(--paper-ink-2);
  max-height: 200px;
  overflow-y: auto;
}

.human-input-card :deep(.el-card__footer) {
  padding-top: 16px;
  text-align: right;
}

.input-wrapper {
  flex-shrink: 0;
  border-top: 1px solid var(--paper-line-soft);
  background: transparent;
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
  background: rgba(194, 65, 12, 0.04);
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
  background: var(--paper-warm);
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
  background: var(--ink-island);
  color: #e8e3d8;
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
    padding: 20px 16px 0px 16px;
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
