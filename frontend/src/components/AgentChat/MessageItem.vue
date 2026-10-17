<script setup lang="ts">
import MessageBubble from '@/components/AgentChat/MessageBubble.vue'
import { ChatDotRound } from '@element-plus/icons-vue'
import { computed } from 'vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import type { ChatRow } from '@/components/AgentChat/chatRow'
import type { StreamingMessage } from '@/composables/useStreamingMessage'
import { useAgentStore } from '@/stores/agentOptimized'
import { formatDuration } from '@/utils/format'

/**
 * 虚拟行分发层：渲染 MessageBubble（消息气泡 / 上下文摘要卡）或
 * 空窗期 typing 行（流式中 AI 消息对象尚未创建时的头像 + 三点 + 计时）。
 * AI 消息创建后 typing 行消失、等待三点与计时由 MessageBubble 接管。
 */
defineProps<{
  /** 当前虚拟行（virtualizer 窗口外可能瞬时为空） */
  row: ChatRow | null
  showThinking: boolean
  showEndOutput: boolean
  isStreaming: boolean
}>()

const emit = defineEmits<{
  (e: 'delete', msg: StreamingMessage): void
  (e: 'preview', data: ImagePreviewData): void
}>()

const agentStore = useAgentStore()
/** 空窗期实时计时（与 MessageBubble 的 elapsed-row 同源同格式） */
const elapsedText = computed(() => formatDuration(agentStore.streamElapsedMs))
</script>

<template>
  <!-- 空窗期 typing 行：send 后 AI 消息对象未创建期间的头像 + 三点 + 计时（计时在三点下方） -->
  <div v-if="row?.kind === 'typing'" class="typing-row">
    <div class="typing-avatar avatar-ai">
      <el-icon :size="16"><ChatDotRound /></el-icon>
    </div>
    <div class="typing-body">
      <div class="typing-dots">
        <span class="dot"></span>
        <span class="dot"></span>
        <span class="dot"></span>
      </div>
      <span class="typing-elapsed">已运行 {{ elapsedText }}</span>
    </div>
  </div>
  <MessageBubble
    v-else-if="row?.msg"
    :msg="row.msg"
    :part="row.part"
    :segment="row.segment"
    :segment-index="row.segmentIndex ?? -1"
    :data-msg-id="row.msg.id"
    :show-thinking="showThinking"
    :show-end-output="showEndOutput"
    :is-streaming="isStreaming"
    :is-last="!!row.isLast"
    :expand-key="row.key"
    @delete="m => emit('delete', m)"
    @preview="data => emit('preview', data)"
  />
</template>

<style scoped>
/* 空窗期 typing 行（52px，与 chatRow.ts TYPING_ROW_HEIGHT 对齐）：
   头像样式与 MessageBubble .avatar/.avatar-ai 同口径 */
.typing-row {
  display: flex;
  align-items: flex-start;
  padding: 8px 0;
}
.typing-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-right: 5px;
  background: linear-gradient(to top right, #3a3730, #55504a);
  color: var(--paper-card);
  box-shadow: 0 2px 8px rgba(60, 50, 35, 0.18);
}
.typing-body {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  margin: 0px 0px 0px 10px;
  min-height: 36px;
}
.typing-dots {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 36px;
}
.typing-elapsed {
  font-size: 11px;
  color: var(--paper-ink-4);
  font-variant-numeric: tabular-nums;
}
.typing-body .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink-soft, #8a857c);
  animation: typing-bounce 1.4s infinite both;
}
.typing-body .dot:nth-child(2) {
  animation-delay: 0.2s;
}
.typing-body .dot:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes typing-bounce {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
}
</style>