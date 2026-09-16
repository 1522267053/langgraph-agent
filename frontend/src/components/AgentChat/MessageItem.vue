<script setup lang="ts">
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
  <!-- 流式指示器状态行：稳定 key + 固定高度，独立于段落行（避免 footer 随
    新段出现迁移造成视口内内容弹跳）。左缩进与气泡内容区对齐 -->
  <div v-if="row?.kind === 'typing'" class="typing-row">
    <span class="dot"></span>
    <span class="dot"></span>
    <span class="dot"></span>
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
/* 与 MessageBubble 的 .footer-row 三点同款观感：6px 灰点、typing 动画；
   左缩进 41px 对齐气泡内容区（头像 36 + 间距 5） */
.typing-row {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 16px 0;
  padding-left: 41px;
}

.typing-row .dot {
  width: 6px;
  height: 6px;
  background: #94a3b8;
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
