<script setup lang="ts">
import { computed } from 'vue'
import { RefreshLeft, Operation } from '@element-plus/icons-vue'
import AIMessageContent from '@/components/common/AIMessageContent.vue'
import FilePreviewer from '@/components/common/FilePreviewer.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import { formatChatTime, formatTokenCount } from '@/utils/format'
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
      <div v-else :class="['message', msg.role, 'animate-fade-in']">
        <div class="message-avatar">
          <div v-if="msg.role === 'human'" class="avatar avatar-user">U</div>
          <div v-else class="avatar avatar-ai">
            <el-icon :size="16"><ChatDotRound /></el-icon>
          </div>
        </div>
        <div class="message-body">
          <div class="message-header">
            <span class="role-name">{{ msg.role === 'human' ? '你' : 'AI' }}</span>
            <span class="message-time">{{ formatChatTime(msg.createdAt) }}</span>
            <el-tooltip
              v-if="msg.role === 'human' && !isStreaming"
              content="回退到此消息"
              placement="top"
            >
              <el-button
                :icon="RefreshLeft"
                link
                size="small"
                class="delete-msg-btn"
                @click="emit('delete', msg)"
              />
            </el-tooltip>
          </div>

          <template v-if="msg.role === 'ai' && msg.segments && msg.segments.length > 0">
            <AIMessageContent
              :segments="msg.segments"
              :show-thinking="showThinking"
              :show-tool-calls="showToolCalls"
              :is-streaming="isStreaming"
              @revert="dbMsgId => emit('revert', dbMsgId)"
            />
            <!-- 流式输出指示器：最后一个 assistant 消息在 streaming 期间持续显示 -->
            <div v-if="isStreaming && isLastMessage(idx)" class="streaming-indicator">
              <span class="dot"></span>
              <span class="dot"></span>
              <span class="dot"></span>
            </div>
            <div v-if="msg.total_tokens" class="token-info">
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
          </template>

          <template v-else>
            <div class="message-content">
              {{ msg.content }}
            </div>
            <FilePreviewer
              v-if="msg.files && msg.files.length > 0"
              :files="msg.files"
              @preview="data => emit('preview', data)"
            />
          </template>
        </div>
      </div>
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

.message.human {
  flex-direction: row-reverse;
}

.message-avatar {
  flex-shrink: 0;
  margin-right: 14px;
}

.message.human .message-avatar {
  margin-right: 0;
  margin-left: 14px;
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
}

.token-total {
  color: #2563eb;
  font-weight: 700;
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

/* 流式输出指示器：复用 @keyframes typing，点更小更轻量 */
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
