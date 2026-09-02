<script setup lang="ts">
import { computed, ref } from 'vue'
import { RefreshLeft, Tickets } from '@element-plus/icons-vue'
import AIMessageContent from '@/components/common/AIMessageContent.vue'
import FilePreviewer from '@/components/common/FilePreviewer.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import { formatChatTime, formatTokenCount } from '@/utils/format'
import type { StreamingMessage } from '@/composables/useStreamingMessage'
import type { Segment } from '@/types/segment'
import type { ChatRowPart } from '@/components/AgentChat/chatRow'

/**
 * 消息气泡（段级虚拟行渲染单元）
 * 一个 AI 回合被拆成多行时，first 行带头部（头像/角色名/时间）、last 行带尾部
 * （token 统计/流式指示器），mid 行仅渲染段本身；single 行头尾同框
 */
const props = defineProps<{
  msg: StreamingMessage
  part: ChatRowPart
  /** ai 行渲染的段；human 行不传 */
  segment?: Segment
  /** 段在消息 segments 中的下标 */
  segmentIndex?: number
  showThinking: boolean
  /** 是否显示结束节点输出按钮（右上角"展示"下拉控制） */
  showEndOutput: boolean
  isStreaming: boolean
  /** 是否为列表最后一条消息（流式指示器定位） */
  isLast: boolean
  /** 工具块交互：本行是否为流式中的最新工具段（默认展开态判定） */
  isLatestTool: boolean
  /** 工具块交互：本行展开状态存取 key（虚拟行 key） */
  expandKey: string
}>()

const emit = defineEmits<{
  (e: 'delete', msg: StreamingMessage): void
  (e: 'revert', dbMsgId: number): void
  (e: 'preview', data: ImagePreviewData): void
}>()

const isHuman = computed(() => props.msg.role === 'human')
const showHeader = computed(() => props.part === 'first' || props.part === 'single')
const showFooter = computed(() => props.part === 'last' || props.part === 'single')

// ---- 结束节点输出（该轮 AI 消息携带，点击按钮查看） ----
const endOutputVisible = ref(false)
const endOutputText = computed(() =>
  props.msg.end_output ? JSON.stringify(props.msg.end_output, null, 2) : ''
)

// ---- 段级上下文标志（供 AIMessageContent 单段模式还原消息级判定） ----
const isMsgLastSegment = computed(() => {
  const segs = props.msg.segments
  return props.segmentIndex === segs.length - 1
})

const isMsgLastContent = computed(() => {
  const segs = props.msg.segments
  for (let i = segs.length - 1; i >= 0; i--) {
    if (segs[i]?.type === 'content') return props.segmentIndex === i
  }
  return isMsgLastSegment.value
})

const isMsgThinkingInProgress = computed(() => {
  if (!props.isStreaming || !props.isLast) return false
  const segs = props.msg.segments
  for (let i = (props.segmentIndex ?? 0) + 1; i < segs.length; i++) {
    if (segs[i]?.type === 'content') return false
  }
  return true
})

const streamingActive = computed(() => props.isStreaming && props.isLast)
/** 流式 markdown 仅在消息最后一个段上启用（父级传入的 isStreaming 已含 isLast 判定） */
const segmentStreaming = computed(() => streamingActive.value && isMsgLastSegment.value)
</script>

<template>
  <div :class="['message', msg.role, `part-${part}`, 'animate-fade-in']">
    <!-- 头像列：仅 first/single 行渲染头像，mid/last 行渲染等宽占位保持左对齐 -->
    <div class="message-avatar">
      <template v-if="showHeader">
        <div v-if="isHuman" class="avatar avatar-user">U</div>
        <div v-else class="avatar avatar-ai">
          <el-icon :size="16"><ChatDotRound /></el-icon>
        </div>
      </template>
    </div>
    <div class="message-body">
      <div v-if="showHeader" class="message-header">
        <span class="role-name">{{ isHuman ? '你' : 'AI' }}</span>
        <span class="message-time">{{ formatChatTime(msg.createdAt) }}</span>
        <el-tooltip v-if="isHuman && !isStreaming" content="回退到此消息" placement="top">
          <el-button
            :icon="RefreshLeft"
            link
            size="small"
            class="delete-msg-btn"
            @click="emit('delete', msg)"
          />
        </el-tooltip>
      </div>

      <template v-if="isHuman">
        <div class="message-content">
          {{ msg.content }}
        </div>
        <FilePreviewer
          v-if="msg.files && msg.files.length > 0"
          :files="msg.files"
          @preview="data => emit('preview', data)"
        />
      </template>
      <template v-else>
        <AIMessageContent
          v-if="segment"
          :segments="[segment]"
          single-segment
          :is-msg-last-segment="isMsgLastSegment"
          :is-msg-last-content="isMsgLastContent"
          :is-msg-thinking-in-progress="isMsgThinkingInProgress"
          :show-thinking="showThinking"
          :is-streaming="segmentStreaming"
          :disable-actions="isStreaming"
          :is-latest-tool="isLatestTool"
          :expand-key="expandKey"
          @revert="dbMsgId => emit('revert', dbMsgId)"
        />
        <div v-if="showFooter && msg.total_tokens && !streamingActive" class="token-info">
          <span>
            输入:
            <span class="token-value">{{ formatTokenCount(msg.prompt_tokens) }}</span>
            token
          </span>
          <span>
            输出:
            <span class="token-value">{{ formatTokenCount(msg.completion_tokens) }}</span>
            token
          </span>
          <span>
            总计:
            <span class="token-total">{{ formatTokenCount(msg.total_tokens) }}</span>
            token
          </span>
        </div>

        <!-- 结束节点输出：右上角"展示"下拉勾选后显示（该轮 AI 消息携带） -->
        <div
          v-if="showFooter && showEndOutput && msg.end_output && !streamingActive"
          class="end-output-row"
        >
          <el-button
            link
            size="small"
            type="primary"
            :icon="Tickets"
            @click="endOutputVisible = true"
          >
            结束输出
          </el-button>
        </div>
        <el-dialog
          v-model="endOutputVisible"
          title="结束节点输出"
          width="560px"
          append-to-body
          class="end-output-dialog"
        >
          <pre class="end-output-pre">{{ endOutputText }}</pre>
        </el-dialog>

        <!-- 流式输出指示器：复用 @keyframes typing，点更小更轻量 -->
        <div v-if="showFooter && streamingActive" class="streaming-indicator">
          <span class="dot"></span>
          <span class="dot"></span>
          <span class="dot"></span>
        </div>
      </template>
    </div>
  </div>
</template>

<script lang="ts">
import { ChatDotRound } from '@element-plus/icons-vue'
export default {
  components: { ChatDotRound }
}
</script>

<style scoped>
.message {
  display: flex;
}

/* 消息间距只挂在消息的最后一行，段行之间保持紧凑 */
.message.part-last,
.message.part-single {
  margin-bottom: 32px;
}

.message.human {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 36px;
  flex-shrink: 0;
  margin-right: 5px;
}

.message.human .message-avatar {
  margin-right: 0;
  margin-left: 5px;
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
}

.avatar-user {
  background: #2563eb;
  color: #fff;
}

.avatar-ai {
  background: linear-gradient(to top right, #1e293b, #475569);
  color: #fff;
  box-shadow: 0 2px 8px rgba(30, 41, 59, 0.2);
}

.message-body {
  flex: 1;
  min-width: 0;
}

.message.human .message-body {
  text-align: right;
}

.message.human .message-body :deep(.file-previewer) {
  justify-content: flex-end;
}

.message-header {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}

.message.human .message-header {
  justify-content: flex-end;
}

.role-name {
  font-weight: 600;
  font-size: 13px;
  margin: 0 8px;
  color: #334155;
}

.message-time {
  font-size: 11px;
  color: #94a3b8;
}

.delete-msg-btn {
  margin-left: 8px;
  color: #94a3b8;
  font-size: 14px;
}

.delete-msg-btn:hover {
  color: #ef4444;
}

.message.human .message-content {
  white-space: pre-wrap;
  background: #2563eb;
  color: #fff;
  padding: 12px 18px;
  border-radius: 16px 4px 16px 16px;
  display: inline-block;
  max-width: 100%;
  text-align: left;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  overflow-wrap: break-word;
}

.token-info {
  display: flex;
  gap: 16px;
  font-size: 10px;
  font-family: 'Courier New', monospace;
  color: #94a3b8;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #f1f5f9;
}

.token-value {
  color: #475569;
  font-variant-numeric: tabular-nums;
}

.token-total {
  color: #2563eb;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.streaming-indicator {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 6px 4px;
}

.streaming-indicator .dot {
  width: 6px;
  height: 6px;
  background: #94a3b8;
  border-radius: 50%;
  animation: typing 1.4s infinite both;
}

.streaming-indicator .dot:nth-child(2) {
  animation-delay: 0.2s;
}

.streaming-indicator .dot:nth-child(3) {
  animation-delay: 0.4s;
}

.end-output-row {
}

.end-output-pre {
  margin: 0;
  max-height: 60vh;
  overflow: auto;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #334155;
  background: #f8fafc;
  border-radius: 6px;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

@keyframes typing {
  0%,
  80%,
  100% {
    transform: scale(0.6);
    opacity: 0.5;
  }

  40% {
    transform: scale(1);
    opacity: 1;
  }
}
</style>
