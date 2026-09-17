<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowRight, CopyDocument, RefreshLeft } from '@element-plus/icons-vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import KnowledgeCitationList from '@/components/common/KnowledgeCitationList.vue'
import TodoList from '@/components/common/TodoList.vue'
import ToolResultViewer from '@/components/common/ToolResultViewer.vue'
import type { Segment } from '@/types/segment'
import { useKnowledgeReferenceDrawer } from '@/composables/useKnowledgeReferenceDrawer'
import { getBlockExpandOverride, toggleBlockExpand } from '@/components/AgentChat/blockExpand'
import { formatToolArgs, formatToolArgsExpanded, hasStringifiedJson } from '@/utils/format'
import { AUTO_SCROLL_BOTTOM_THRESHOLD } from '@/constants/timing'

const props = withDefaults(
  defineProps<{
    segments: Segment[]
    showThinking?: boolean
    isStreaming?: boolean
    disableActions?: boolean
    /** 单段模式（聊天段级虚拟行）：segments 恒为单元素，跳过内部窗口与折叠 */
    singleSegment?: boolean
    /** 单段模式：本段是否为消息最后一个段（决定 thinking revert 显隐） */
    isMsgLastSegment?: boolean
    /** 单段模式：本段是否为消息最后一个 content 段（决定 content revert 显隐） */
    isMsgLastContent?: boolean
    /** 单段模式：thinking 进行中（流式中且本段之后无 content 段） */
    isMsgThinkingInProgress?: boolean
    /** 聊天折叠交互：传入后工具块头部可点击展开/收起，key 为虚拟行 key */
    expandKey?: string
  }>(),
  {
    showThinking: true,
    isStreaming: false,
    disableActions: false,
    singleSegment: false,
    isMsgLastSegment: true,
    isMsgLastContent: true,
    isMsgThinkingInProgress: false,
    expandKey: ''
  }
)

const emit = defineEmits<{
  (e: 'revert', dbMsgId: number): void
}>()

const MAX_VISIBLE_SEGMENTS = 35
/** 非流式（历史加载/回合结束）默认最多渲染的分段数，超出折叠，避免长回合全量挂载 */
const MAX_FINAL_SEGMENTS = 100
const expanded = ref(false)

const visibleSegments = computed(() => {
  if (props.singleSegment) return props.segments
  if (props.isStreaming) {
    return props.segments.length > MAX_VISIBLE_SEGMENTS
      ? props.segments.slice(-MAX_VISIBLE_SEGMENTS)
      : props.segments
  }
  if (expanded.value || props.segments.length <= MAX_FINAL_SEGMENTS) return props.segments
  return props.segments.slice(-MAX_FINAL_SEGMENTS)
})

/** 被折叠的更早分段数量（流式期间不提供展开入口） */
const hiddenSegmentCount = computed(() => {
  if (props.singleSegment || props.isStreaming || expanded.value) return 0
  return Math.max(0, props.segments.length - MAX_FINAL_SEGMENTS)
})

/** 单段模式的上下文标志由父组件传入；列表模式沿用内部推导 */
function isMsgRevertVisible(idx: number): boolean {
  return props.singleSegment ? !props.isMsgLastSegment : !isLastSegment(idx)
}

function isMsgContentRevertHidden(idx: number): boolean {
  return props.singleSegment ? props.isMsgLastContent : idx === lastContentIdx.value
}

function isMsgThinkingLoading(idx: number): boolean {
  return props.singleSegment ? props.isMsgThinkingInProgress : isThinkingInProgress(idx)
}

const lastContentIdx = computed(() => {
  for (let i = visibleSegments.value.length - 1; i >= 0; i--) {
    if (visibleSegments.value[i]?.type === 'content') return i
  }
  return -1
})

function isLastSegment(idx: number): boolean {
  return idx === visibleSegments.value.length - 1
}

function isThinkingInProgress(idx: number): boolean {
  if (!props.isStreaming) return false
  for (let i = idx + 1; i < visibleSegments.value.length; i++) {
    if (visibleSegments.value[i]?.type === 'content') return false
  }
  return true
}

function segmentKey(segment: Segment, idx: number): string {
  return segment.id || `idx-${idx}`
}

const expandedArgsSegments = ref(new Set<string>())
const { open: openKnowledgeReference } = useKnowledgeReferenceDrawer()

// ---- 工具块折叠交互（聊天段级模式：传入 expandKey 后启用） ----

/** 工具块头部可点击展开/收起；列表模式（无 expandKey）不可交互、始终展开 */
const isToolInteractive = computed(() => !!props.expandKey)

/** 工具块内容（入参/结果/错误/加载）显隐：
 * 聊天模式 = 手动操作覆盖 ?? 默认折叠（业界模式：工具默认状态行，点击回看）；
 * 列表模式（Flow 执行面板等）始终展开 */
const toolBodyVisible = computed(() => {
  if (props.expandKey) return getBlockExpandOverride(props.expandKey) ?? false
  return true
})

function toggleToolBody(): void {
  if (!props.expandKey) return
  toggleBlockExpand(props.expandKey, !toolBodyVisible.value)
}

function toggleArgsFormat(segment: Segment, idx: number): void {
  const key = segmentKey(segment, idx)
  if (expandedArgsSegments.value.has(key)) {
    expandedArgsSegments.value.delete(key)
  } else {
    expandedArgsSegments.value.add(key)
  }
}

/**
 * 工具结果统一取值：call_sub_agent_* 运行中且尚无最终结果时，将实时输出
 * （带状态标签前缀——既保留上下文，也保证字符串不是合法 JSON、稳定命中裸字符串
 * 路径）作为结果交给 ToolResultViewer 标准渲染：折叠态 = 单行摘要（行高恒定，
 * 输出到达不再撑高行），展开态 = 内容区（fallback pre，封顶内部滚动）；
 * 完成后 liveOutput 被删除，无痕切换真实结果。
 * 正在调用工具时状态行前置为「正在调用xxx工具中」，结束后回退「输出中」
 */
function toolDisplayResult(segment: Segment): unknown {
  if (segment.type !== 'tool' || !segment.tool) return undefined
  if (segment.tool.result !== undefined) return segment.tool.result
  const tool = segment.tool
  const hasOutput = typeof tool.liveOutput === 'string' && !!tool.liveOutput
  const hasToolActivity = typeof tool.liveTool === 'string' && !!tool.liveTool
  if (tool.status === 'running' && (hasOutput || hasToolActivity)) {
    const name = tool.liveAgentName || '子Agent'
    const action = hasToolActivity ? `正在调用 ${tool.liveTool} 工具中` : '输出中'
    return `子Agent「${name}」${action}：\n${tool.liveOutput || ''}`
  }
  return undefined
}

function isArgsExpanded(segment: Segment, idx: number): boolean {
  return expandedArgsSegments.value.has(segmentKey(segment, idx))
}

async function handleCopy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success({ message: '已复制', duration: 5000 })
  } catch {
    ElMessage.error({ message: '复制失败', duration: 5000 })
  }
}

// ---- thinking 块内部跟随（封顶后流式内容自动滚到块底部） ----
// 外层贴底由 useAutoScroll 的嵌套滚动归因（isNestedScrollTarget /
// nestedScrollConsumesGesture）处理，此处只负责块内部：不要加
// overscroll-behavior，否则内层到边界后手势不再穿透主容器，会被误判为主动上滚

/** thinking 块 wrap 元素（el-scrollbar 的滚动容器），供跟随滚动 */
const thinkingWraps = new Map<string, HTMLElement>()

/** 重锁抑制窗（与最外层 PIN_RELOCK_GRACE_MS 对称）：wheel 解除锁存与实际
 * scroll 事件之间有几帧延迟，期间 distance ≤ threshold 会立即误判「贴底」
 * 恢复跟随并被流式增长拉回底部；解除后 300ms 内即使触底也不恢复 */
const THINKING_RELOCK_GRACE_MS = 300

/** 用户在块内手动滚动（上滚/触摸/按压）后停用该块的内部跟随。
 * key 存在 = 脱离跟随中；value = 脱离时刻 performance.now()，单 Map 兼做
 * 「状态 + 时间戳」避免双结构 */
const thinkingUnlockAt = new Map<string, number>()

function setThinkingWrapRef(key: string, el: unknown): void {
  // el-scrollbar :ref 回调拿到 ScrollbarInstance，wrapRef 是真正的滚动 wrap
  const wrap =
    el && typeof el === 'object' && 'wrapRef' in el
      ? (el as { wrapRef?: unknown }).wrapRef
      : undefined
  if (wrap instanceof HTMLElement) thinkingWraps.set(key, wrap)
  else thinkingWraps.delete(key)
}

function stopThinkingFollow(key: string): void {
  thinkingUnlockAt.set(key, performance.now())
}

function onThinkingWheel(key: string, event: WheelEvent): void {
  // 仅上滚视为脱离跟随意图；下滚时本就贴底，保持跟随
  if (event.deltaY < 0) stopThinkingFollow(key)
}

/** 用户滚回块底部附近（与外层 useAutoScroll 同一距底阈值）→ 恢复内部跟随，
 * 与外层「上滚停止跟随、回到底部恢复」语义对齐。el-scrollbar @scroll 的
 * target 不一定指向 wrap（取决于事件冒泡路径），直接用 wrapRef 算距离。
 * 300ms 抑制窗内即使触底也不恢复，避免 wheel/scroll 交错窗口的误锁 */
function onThinkingScroll(key: string): void {
  const wrap = thinkingWraps.get(key)
  if (!wrap) return
  const distance = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight
  if (distance > AUTO_SCROLL_BOTTOM_THRESHOLD) return
  const unlockedAt = thinkingUnlockAt.get(key)
  if (unlockedAt === undefined) return
  if (performance.now() - unlockedAt > THINKING_RELOCK_GRACE_MS) {
    thinkingUnlockAt.delete(key)
  }
}

/** thinking 流式增长时让封顶块内部贴底，用户手动滚动后停用。
 * 滚动容器改为 el-scrollbar 的 wrap（封顶 + 内部滚动） */
watch(
  () => props.segments.map(s => s.thinking),
  () => {
    if (!props.isStreaming) return
    visibleSegments.value.forEach((segment, idx) => {
      if (segment.type !== 'thinking') return
      const key = segmentKey(segment, idx)
      if (!isMsgThinkingLoading(idx)) {
        thinkingUnlockAt.delete(key)
        return
      }
      if (thinkingUnlockAt.has(key)) return
      const wrap = thinkingWraps.get(key)
      if (wrap && wrap.scrollHeight > wrap.clientHeight) wrap.scrollTop = wrap.scrollHeight
    })
  },
  { flush: 'post' }
)
</script>

<template>
  <div v-if="hiddenSegmentCount > 0" class="expand-earlier" @click="expanded = true">
    展开更早的 {{ hiddenSegmentCount }} 个分段
  </div>
  <template v-for="(segment, idx) in visibleSegments" :key="segmentKey(segment, idx)">
    <div
      v-if="segment.type === 'thinking'"
      class="thinking-block"
      @wheel.passive="onThinkingWheel(segmentKey(segment, idx), $event)"
      @touchmove.passive="stopThinkingFollow(segmentKey(segment, idx))"
      @pointerdown="stopThinkingFollow(segmentKey(segment, idx))"
    >
      <div class="code-block-header">
        <div class="code-block-dots">
          <span class="dot-red"></span>
          <span class="dot-amber"></span>
          <span class="dot-green"></span>
        </div>
        <span class="code-block-label thinking-label">思考过程</span>
        <div class="code-block-actions">
          <span v-if="!showThinking && isMsgThinkingLoading(idx)" class="thinking-loading">
            思考中...
          </span>
          <el-tooltip
            v-if="!disableActions && segment.dbMsgId && isMsgRevertVisible(idx)"
            content="删除此条及之后的内容"
            placement="top"
          >
            <el-button
              :icon="RefreshLeft"
              link
              size="small"
              class="revert-btn"
              @click="emit('revert', segment.dbMsgId!)"
            />
          </el-tooltip>
        </div>
      </div>
      <el-scrollbar
        v-if="showThinking"
        :ref="el => setThinkingWrapRef(segmentKey(segment, idx), el)"
        max-height="400px"
        @scroll="onThinkingScroll(segmentKey(segment, idx))"
      >
        <pre class="thinking-content">{{ segment.thinking }}</pre>
      </el-scrollbar>
    </div>

    <div v-else-if="segment.type === 'tool' && segment.tool" class="tool-block">
      <div
        :class="[
          'tool-line',
          'tool-status-' + segment.tool.status,
          { 'tool-header-clickable': isToolInteractive }
        ]"
        @click="toggleToolBody"
      >
        <span v-if="segment.tool.status === 'running'" class="status-spinner"></span>
        <span v-else :class="['tool-line-dot', segment.tool.status]"></span>
        <span class="tool-line-name">{{ segment.tool.name }}</span>
        <!-- 折叠交互：箭头指向提示可点击，展开时旋转 90° -->
        <el-icon
          v-if="isToolInteractive"
          :class="['tool-expand-arrow', { 'is-expanded': toolBodyVisible }]"
        >
          <ArrowRight />
        </el-icon>
      </div>
      <!-- 入参 JSON：折叠态隐藏，点击头部展开后显示 -->
      <div
        v-if="toolBodyVisible && segment.tool.args && Object.keys(segment.tool.args).length > 0"
        class="tool-content-args-wrapper"
      >
        <pre class="tool-content tool-content-args">{{
          isArgsExpanded(segment, idx)
            ? formatToolArgsExpanded(segment.tool.args)
            : formatToolArgs(segment.tool.args)
        }}</pre>
        <el-button
          v-if="hasStringifiedJson(segment.tool.args)"
          link
          size="small"
          class="args-toggle-btn"
          @click="toggleArgsFormat(segment, idx)"
        >
          {{ isArgsExpanded(segment, idx) ? '显示原始' : '显示格式化' }}
        </el-button>
      </div>
      <!-- 结果（call_sub_agent_* 运行中为实时输出）：统一走 ToolResultViewer 标准管线，
           折叠态单行摘要 / 展开态内容区，完成后无痕切换真实结果 -->
      <div
        v-if="toolDisplayResult(segment) !== undefined"
      >
        <ToolResultViewer
          :tool-name="segment.tool.name"
          :result="toolDisplayResult(segment)"
          :status="segment.tool.status"
          :hide-plain-json="!toolBodyVisible"
          :collapsed="!!expandKey && !toolBodyVisible"
        />
      </div>
      <pre
        v-if="segment.tool.result === undefined && segment.tool.status === 'error'"
        class="tool-content tool-content-error"
      >
执行失败</pre>
    </div>

    <div v-else-if="segment.type === 'content'" class="message-content">
      <div class="content-actions">
        <!-- fixed 定位：absolute 浮层按文档坐标计算，虚拟滚动重排瞬间可能被定位到
             文档很下方，瞬态撑大 body 产生贯穿整窗的全局滚动条；fixed 相对视口定位无此问题 -->
        <el-tooltip
          v-if="!disableActions && segment.content"
          content="复制源文本"
          placement="top"
          :popper-options="{ strategy: 'fixed' }"
        >
          <el-button
            :icon="CopyDocument"
            link
            size="small"
            class="copy-btn"
            @click="handleCopy(segment.content || '')"
          />
        </el-tooltip>
        <el-tooltip
          v-if="!disableActions && segment.dbMsgId && !isMsgContentRevertHidden(idx)"
          content="删除此条及之后的内容"
          placement="top"
          :popper-options="{ strategy: 'fixed' }"
        >
          <el-button
            :icon="RefreshLeft"
            link
            size="small"
            class="content-revert-btn"
            @click="emit('revert', segment.dbMsgId!)"
          />
        </el-tooltip>
      </div>
      <!-- 仅流式中的最后一个分段走节流渲染路径，历史分段正常渲染 -->
      <MarkdownRenderer
        :content="segment.content || ''"
        :streaming="isStreaming && (singleSegment || isLastSegment(idx))"
        :citations="segment.knowledge_citations || []"
        @citation-click="openKnowledgeReference"
      />
      <KnowledgeCitationList
        v-if="segment.knowledge_citations?.length"
        :citations="segment.knowledge_citations"
        @select="openKnowledgeReference"
      />
    </div>

    <div v-else-if="segment.type === 'todo' && segment.todo" class="todo-block">
      <div class="todo-header">
        <span class="todo-badge">任务计划</span>
        <span class="todo-count">{{ segment.todo.length }} 项</span>
      </div>
      <TodoList :items="segment.todo" />
    </div>
  </template>
</template>

<style scoped>
.expand-earlier {
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
  padding: 8px 0;
  border: 1px dashed var(--paper-line-strong);
  border-radius: 8px;
  color: var(--paper-ink-3);
  font-size: 13px;
  cursor: pointer;
  user-select: none;
  transition: all 0.2s;
}

.expand-earlier:hover {
  color: var(--vermilion);
  border-color: var(--vermilion-line);
  background: var(--vermilion-soft);
}

.thinking-block {
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 12px;
  border: 1px solid var(--paper-line);
  background: var(--paper-warm);
  box-shadow: none;
}

/* 思考块头部（tool 块已改纯文本行，仅 thinking 仍使用此头部）。
   与正文同底色，仅一条浅内线分隔 */
.code-block-header {
  background: transparent;
  border-bottom: 1px solid rgba(60, 50, 35, 0.08);
  padding: 7px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.code-block-dots {
  display: flex;
  gap: 5px;
}

.code-block-dots .dot-red,
.code-block-dots .dot-amber,
.code-block-dots .dot-green {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.code-block-dots .dot-red {
  background: rgba(194, 65, 12, 0.4);
}

.code-block-dots .dot-amber {
  background: rgba(217, 119, 6, 0.35);
}

.code-block-dots .dot-green {
  background: rgba(22, 163, 74, 0.35);
}

/* 工具块：暖纸色圆角容器，把工具行与结果摘要包成一体 */
.tool-block {
  margin-bottom: 6px;
  background: var(--paper-warm);
  border-radius: 8px;
  overflow: hidden;
}

.tool-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  font-size: 12.5px;
  line-height: 1.5;
  transition: background 0.15s;
}

.tool-line.tool-header-clickable:hover {
  background: rgba(60, 50, 35, 0.05);
}

/* 失败行：不加底色，仅红圆点 + 红色工具名传达错误信号 */
.tool-line.tool-status-error .tool-line-name {
  color: #dc2626;
}

.tool-line-name {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  color: var(--paper-ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 状态圆点：工具名前置，完成=绿 / 失败=红（Error 行自带浅红底） */
.tool-line-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.tool-line-dot.success {
  background: #16a34a;
}

.tool-line-dot.error {
  background: #dc2626;
}

/* 展开状态箭头：折叠时朝右、展开时旋转 90° 朝下 */
.tool-expand-arrow {
  margin-left: auto;
  font-size: 12px;
  color: var(--paper-ink-4);
  transition: transform 0.2s;
}

.tool-expand-arrow.is-expanded {
  transform: rotate(90deg);
}

.tool-header-clickable {
  cursor: pointer;
  user-select: none;
}

.thinking-label {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--paper-ink-3);
  letter-spacing: 0.01em;
  text-transform: none;
}

.code-block-label {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--paper-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.code-block-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}

.thinking-loading {
  color: #fbbf24;
  font-size: 12px;
}

.status-spinner {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 2px solid rgba(194, 65, 12, 0.25);
  border-top-color: var(--vermilion);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.thinking-content {
  margin: 0;
  padding: 14px 16px;
  background: transparent;
  font-family: var(--font-ui);
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--paper-ink-2);
  white-space: pre-wrap;
  word-break: break-word;
  /* 封顶与滚动由外层 el-scrollbar 提供（max-height="400px"），此处不再设 */
}

.tool-content {
  margin: 0;
  padding: 8px 12px;
  background: var(--paper-warm);
  border: none;
  border-bottom: 1px solid rgba(60, 50, 35, 0.08);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--paper-ink-2);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 150px;
  overflow-y: auto;
}

.tool-content-args {
  /* 融入 .tool-block 暖纸容器：无独立边框，仅底部浅内线与结果区分隔 */
  margin-top: 0;
}

.tool-content-args-wrapper {
  position: relative;
}

.args-toggle-btn {
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 11px;
  color: var(--paper-ink-3);
  z-index: 1;
}

.args-toggle-btn:hover {
  color: var(--vermilion);
}

.tool-content-error {
  color: #dc2626;
}

.revert-btn {
  color: var(--paper-ink-4);
  font-size: 14px;
  transition: color 0.2s;
}

.revert-btn:hover {
  color: var(--vermilion);
}

.message-content {
  word-break: break-word;
  line-height: 1.7;
  background: transparent;
  padding: 2px 0;
  border-radius: 0;
  border: none;
  box-shadow: none;
  margin-bottom: 10px;
  position: relative;
  font-size: 14.5px;
}

.content-actions {
  position: absolute;
  top: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: 2px;
  /* 默认隐藏（半透明 + 不可交互），hover 正文块时浮现 */
  opacity: 0;
  transition: opacity 0.15s;
  pointer-events: none;
}

.message-content:hover .content-actions {
  opacity: 0.55;
  pointer-events: auto;
}

.message-content:hover .content-actions:hover {
  opacity: 1;
}

.copy-btn {
  color: var(--paper-ink-3);
  font-size: 14px;
  transition: color 0.2s;
}

.copy-btn:hover {
  color: var(--vermilion);
}

.content-revert-btn {
  color: var(--paper-ink-3);
  font-size: 14px;
  transition: color 0.2s;
}

.content-revert-btn:hover {
  color: var(--vermilion);
}

.todo-block {
  background: var(--paper-warm);
  padding: 20px;
  margin-bottom: 12px;
  border-radius: 16px;
  border: 1px solid var(--paper-line);
  box-shadow: 0 1px 3px rgba(60, 50, 35, 0.04);
}

/* 任务计划项数无上限，封顶后内部滚动，避免超长计划撑爆虚拟行高；
   :deep 穿透 TodoList 子组件根节点 */
.todo-block :deep(.todo-list) {
  max-height: 320px;
  overflow-y: auto;
}

.todo-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.todo-badge {
  padding: 2px 8px;
  background: var(--vermilion);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.todo-count {
  font-size: 14px;
  font-weight: 700;
  color: var(--paper-ink);
}
</style>
