<script setup lang="ts">
import { ChatDotRound, Operation } from '@element-plus/icons-vue'
import MessageBubble from '@/components/AgentChat/MessageBubble.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import type { ChatRow } from '@/components/AgentChat/chatRow'
import type { StreamingMessage } from '@/composables/useStreamingMessage'

/**
 * 虚拟行分发器：按行类型渲染 typing 指示器 / 上下文摘要 / 消息气泡
 * 行结构由 AgentChat.buildChatRows 决定（AI 回合每段一行）
 */
defineProps<{
  /** 当前虚拟行（virtualizer 窗口外可能瞬时为空） */
  row: ChatRow | null
  showThinking: boolean
  showToolCalls: boolean
  showEndOutput: boolean
  isStreaming: boolean
}>()

const emit = defineEmits<{
  (e: 'delete', msg: StreamingMessage): void
  (e: 'revert', dbMsgId: number): void
  (e: 'preview', data: ImagePreviewData): void
}>()
</script>

<template>
  <div v-if="row?.kind === 'typing'" class="message assistant animate-fade-in">
    <div class="message-avatar">
      <div class="avatar avatar-ai">
        <el-icon :size="16"><ChatDotRound /></el-icon>
      </div>
    </div>
    <div class="message-body">
      <div class="message-header">
        <span class="role-name">AI</span>
      </div>
      <div class="message-content typing">
        <span class="dot"></span>
        <span class="dot"></span>
        <span class="dot"></span>
      </div>
    </div>
  </div>
  <div v-else-if="row?.kind === 'summary' && row.msg" class="compress-summary">
    <div class="compress-summary-label">
      <el-icon :size="14"><Operation /></el-icon>
      <span>上下文摘要</span>
      <span v-if="row.msg.removedCount">已压缩 {{ row.msg.removedCount }} 条历史消息</span>
    </div>
    <MarkdownRenderer class="compress-summary-content" :content="row.msg.content" />
  </div>
  <MessageBubble
    v-else-if="row?.msg"
    :msg="row.msg"
    :part="row.part"
    :segment="row.segment"
    :segment-index="row.segmentIndex ?? -1"
    :data-msg-id="row.msg.id"
    :show-thinking="showThinking"
    :show-tool-calls="showToolCalls"
    :show-end-output="showEndOutput"
    :is-streaming="isStreaming"
    :is-last="!!row.isLast"
    @delete="m => emit('delete', m)"
    @revert="dbMsgId => emit('revert', dbMsgId)"
    @preview="data => emit('preview', data)"
  />
</template>

<style scoped>
.message {
  display: flex;
}

.message-avatar {
  flex-shrink: 0;
  width: 36px;
  margin-right: 5px;
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

.avatar-ai {
  background: linear-gradient(to top right, #1e293b, #475569);
  color: #fff;
  box-shadow: 0 2px 8px rgba(30, 41, 59, 0.2);
}

.message-body {
  flex: 1;
  min-width: 0;
}

.message-header {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}

.role-name {
  font-weight: 600;
  font-size: 13px;
  margin: 0 8px;
  color: #334155;
}

.typing {
  display: flex;
  align-items: center;
  gap: 4px;
}

.typing .dot {
  width: 8px;
  height: 8px;
  background: #2563eb;
  border-radius: 50%;
  animation: typing 1.4s infinite both;
}

.typing .dot:nth-child(2) {
  animation-delay: 0.2s;
}

.typing .dot:nth-child(3) {
  animation-delay: 0.4s;
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

.compress-summary {
  background: #f8fafc;
  border-left: 3px solid #d97706;
  border-radius: 8px;
  padding: 14px 18px;
  margin: 4px 0 8px;
}

.compress-summary-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #d97706;
  margin-bottom: 6px;
}

.compress-summary-content {
  font-size: 13px;
  color: #475569;
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
