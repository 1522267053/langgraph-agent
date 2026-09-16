<script setup lang="ts">
import { ChatDotRound } from '@element-plus/icons-vue'
import MessageBubble from '@/components/AgentChat/MessageBubble.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import type { ChatRow } from '@/components/AgentChat/chatRow'
import type { StreamingMessage } from '@/composables/useStreamingMessage'

/**
 * 虚拟行分发层：流式指示器状态行 + ChatRow→MessageBubble 透传（视觉形态收敛
 * 在 MessageBubble：消息气泡 / 上下文摘要卡）。row 在 virtualizer 窗口外可能
 * 瞬时为空，由 v-if 链兜底不渲染
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
  (e: 'revert', dbMsgId: number): void
  (e: 'preview', data: ImagePreviewData): void
}>()
</script>

<template>
  <!-- 打字指示器状态行（仅空窗期：AI 消息尚未创建）：AI 头像 + 「AI」角色名 +
    角色名下方三点，与真实消息行（头像/角色名/内容）同构；AI 消息创建后本行
    消失（MessageBubble 内的 footer 三点已按需求移除，两者无冲突） -->
  <div v-if="row?.kind === 'typing'" class="typing-row">
    <div class="avatar avatar-ai">
      <el-icon :size="16"><ChatDotRound /></el-icon>
    </div>
    <div class="typing-body">
      <div class="message-header">
        <span class="role-name">AI</span>
      </div>
      <div class="typing">
        <span class="dot"></span>
        <span class="dot"></span>
        <span class="dot"></span>
      </div>
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
    @revert="dbMsgId => emit('revert', dbMsgId)"
    @preview="data => emit('preview', data)"
  />
</template>

<style scoped>
.message-header {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}

.role-name {
  font-weight: 600;
  font-size: 13px;
  margin: 0;
  color: var(--paper-ink);
}
/* AI 头像与 MessageBubble 的 .avatar 同款；右侧纵排「AI」角色名 + 三点
   （三点在角色名正下方），与真实消息行（头像/角色名/内容）同构 */
.typing-row {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  padding: 8px 0;
}

.typing-body {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
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
  background: linear-gradient(to top right, var(--paper-ink), #3d3833);
  color: var(--paper);
  box-shadow: 0 2px 8px rgba(31, 29, 26, 0.2);
}
.typing {
  /* 三点与「AI」角色名左对齐 */
  display: flex;
  align-items: center;
  gap: 4px;
  padding-left: 1px;
  margin: 10px 0px 20px 0px;
}
.typing-row .dot {
  width: 6px;
  height: 6px;
  background: var(--paper-ink-4);
  border-radius: 50%;
  animation: typing 1.4s infinite both;
}

.typing-row .dot:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-row .dot:nth-child(3) {
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
</style>
