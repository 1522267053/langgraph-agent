<script setup lang="ts">
import MessageBubble from '@/components/AgentChat/MessageBubble.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import type { ChatRow } from '@/components/AgentChat/chatRow'
import type { StreamingMessage } from '@/composables/useStreamingMessage'

/**
 * 虚拟行透传层：ChatRow 字段适配 + 事件转发，视觉形态全部收敛在 MessageBubble
 * （消息气泡 / 上下文摘要卡 / 流式空窗期合成占位行）。row 在 virtualizer
 * 窗口外可能瞬时为空，由 row?.msg 兜底不渲染
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
  <MessageBubble
    v-if="row?.msg"
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
