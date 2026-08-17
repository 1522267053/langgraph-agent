<script setup lang="ts">
import { computed } from 'vue'
import { Operation } from '@element-plus/icons-vue'
import MessageBubble from '@/components/AgentChat/MessageBubble.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import type { StreamingMessage } from '@/composables/useStreamingMessage'

const COMPRESS_MARKER = '[上下文压缩]'

const props = defineProps<{
  messages: StreamingMessage[]
  showThinking: boolean
  showToolCalls: boolean
  isStreaming: boolean
}>()

const emit = defineEmits<{
  (e: 'delete', msg: StreamingMessage): void
  (e: 'revert', dbMsgId: number): void
  (e: 'preview', data: ImagePreviewData): void
}>()

function isCompressMarker(msg: StreamingMessage): boolean {
  return msg.role === 'human' && msg.content.startsWith(COMPRESS_MARKER)
}

function isCompressSummary(msg: StreamingMessage, index: number): boolean {
  if (msg.role !== 'ai') return false
  const prev = props.messages[index - 1]
  return !!prev && isCompressMarker(prev)
}

const hasTextContent = computed(() => {
  if (!props.isStreaming) return false
  const last = props.messages.at(-1)
  if (!last || last.role !== 'ai') return true
  return !last.segments || last.segments.length === 0
})

/** 判断指定消息是否为最后一条（用于流式指示器定位） */
function isLastMessage(idx: number): boolean {
  return idx === props.messages.length - 1
}
</script>

<template>
  <div class="messages-list">
    <template v-for="(msg, idx) in messages" :key="msg.id">
      <div v-if="isCompressSummary(msg, idx)" class="compress-summary">
        <div class="compress-summary-label">上下文摘要</div>
        <div class="compress-summary-content">{{ msg.content }}</div>
      </div>
      <div v-else-if="isCompressMarker(msg)" class="message compress-marker">
        <div class="compress-notice">
          <el-icon :size="14"><Operation /></el-icon>
          <span>{{ msg.content }}</span>
        </div>
      </div>
      <MessageBubble
        v-else
        :msg="msg"
        :data-msg-id="msg.id"
        :show-thinking="showThinking"
        :show-tool-calls="showToolCalls"
        :is-streaming="isStreaming"
        :is-last="isLastMessage(idx)"
        @delete="m => emit('delete', m)"
        @revert="dbMsgId => emit('revert', dbMsgId)"
        @preview="data => emit('preview', data)"
      />
    </template>

    <div v-if="hasTextContent" class="message assistant animate-fade-in">
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
  </div>
</template>

<script lang="ts">
import { ChatDotRound } from '@element-plus/icons-vue'
export default {
  components: { ChatDotRound }
}
</script>

<style scoped>
.messages-list {
  max-width: 896px;
  margin: 0 auto;
}

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

.compress-marker {
  padding: 8px 0 4px;
}

.compress-notice {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #94a3b8;
  background: #f8fafc;
  padding: 8px 14px;
  border-radius: 8px;
}

.compress-summary {
  background: #f8fafc;
  border-left: 3px solid #d97706;
  border-radius: 8px;
  padding: 14px 18px;
  margin: 4px 0 8px;
}

.compress-summary-label {
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
