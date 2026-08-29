<script setup lang="ts">
import { ChatDotRound, Operation } from '@element-plus/icons-vue'
import MessageBubble from '@/components/AgentChat/MessageBubble.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import type { StreamingMessage } from '@/composables/useStreamingMessage'

defineProps<{
  /** 本行消息（虚拟滚动单行渲染，由 AgentChat.chatRows 决定行类型） */
  msg?: StreamingMessage | null
  /** 渲染独立流式输入指示器行（流式中但最后一条不是 AI 消息时） */
  typing?: boolean
  showThinking: boolean
  showToolCalls: boolean
  isStreaming: boolean
  /** 是否为最后一条消息（流式指示器定位） */
  isLast?: boolean
}>()

const emit = defineEmits<{
  (e: 'delete', msg: StreamingMessage): void
  (e: 'revert', dbMsgId: number): void
  (e: 'preview', data: ImagePreviewData): void
}>()
</script>

<template>
  <div v-if="typing" class="message assistant animate-fade-in">
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
  <div v-else-if="msg && msg.displayType === 'context-summary'" class="compress-summary">
    <div class="compress-summary-label">
      <el-icon :size="14"><Operation /></el-icon>
      <span>上下文摘要</span>
      <span v-if="msg.removedCount">已压缩 {{ msg.removedCount }} 条历史消息</span>
    </div>
    <MarkdownRenderer class="compress-summary-content" :content="msg.content" />
  </div>
  <MessageBubble
    v-else-if="msg"
    :msg="msg"
    :data-msg-id="msg.id"
    :show-thinking="showThinking"
    :show-tool-calls="showToolCalls"
    :is-streaming="isStreaming"
    :is-last="!!isLast"
    @delete="m => emit('delete', m)"
    @revert="dbMsgId => emit('revert', dbMsgId)"
    @preview="data => emit('preview', data)"
  />
</template>

<style scoped>
.message {
  display: flex;
  margin-bottom: 32px;
}

.message-avatar {
  flex-shrink: 0;
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
