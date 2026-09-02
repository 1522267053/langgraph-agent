<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument, RefreshLeft, SetUp } from '@element-plus/icons-vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import KnowledgeCitationList from '@/components/common/KnowledgeCitationList.vue'
import TodoList from '@/components/common/TodoList.vue'
import ToolResultViewer from '@/components/common/ToolResultViewer.vue'
import type { Segment } from '@/types/segment'
import { useKnowledgeReferenceDrawer } from '@/composables/useKnowledgeReferenceDrawer'
import { formatToolArgs, formatToolArgsExpanded, hasStringifiedJson } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    segments: Segment[]
    showThinking?: boolean
    showToolCalls?: boolean
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
  }>(),
  {
    showThinking: true,
    showToolCalls: true,
    isStreaming: false,
    disableActions: false,
    singleSegment: false,
    isMsgLastSegment: true,
    isMsgLastContent: true,
    isMsgThinkingInProgress: false
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

function toggleArgsFormat(segment: Segment, idx: number): void {
  const key = segmentKey(segment, idx)
  if (expandedArgsSegments.value.has(key)) {
    expandedArgsSegments.value.delete(key)
  } else {
    expandedArgsSegments.value.add(key)
  }
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
</script>

<template>
  <div v-if="hiddenSegmentCount > 0" class="expand-earlier" @click="expanded = true">
    展开更早的 {{ hiddenSegmentCount }} 个分段
  </div>
  <template v-for="(segment, idx) in visibleSegments" :key="segmentKey(segment, idx)">
    <div v-if="segment.type === 'thinking'" class="thinking-block">
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
      <pre v-if="showThinking" class="thinking-content">{{ segment.thinking }}</pre>
    </div>

    <div v-else-if="segment.type === 'tool' && segment.tool" class="tool-block">
      <div :class="['code-block-header', 'tool-header-' + segment.tool.status]">
        <el-icon class="tool-header-icon"><SetUp /></el-icon>
        <span class="tool-header-name">{{ segment.tool.name }}</span>
        <span :class="['tool-status-badge', segment.tool.status]">
          <span v-if="segment.tool.status === 'running'" class="status-spinner"></span>
          {{
            segment.tool.status === 'running'
              ? '执行中'
              : segment.tool.status === 'error'
                ? '失败'
                : '完成'
          }}
        </span>
      </div>
      <!-- 子Agent实时输出预览（call_sub_agent_* 工具执行中展示，完成后由最终结果替代） -->
      <div
        v-if="segment.tool.status === 'running' && segment.tool.liveOutput"
        class="tool-live-wrapper"
      >
        <div class="tool-live-label">
          子Agent「{{ segment.tool.liveAgentName || '子Agent' }}」输出中
        </div>
        <pre class="tool-content tool-live-output">{{ segment.tool.liveOutput }}</pre>
      </div>
      <!-- 入参 JSON：仅勾选"工具调用"时展示 -->
      <div
        v-if="showToolCalls && segment.tool.args && Object.keys(segment.tool.args).length > 0"
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
      <!-- 结果：始终尝试渲染；取消勾选时仅隐藏纯 JSON（裸字符串结果如文件写入消息、
           子Agent 回复等始终展示），失败结果（错误详情）始终展示 -->
      <ToolResultViewer
        v-if="segment.tool.result !== undefined"
        :tool-name="segment.tool.name"
        :result="segment.tool.result"
        :hide-plain-json="!showToolCalls && segment.tool.status !== 'error'"
      />
      <pre v-else-if="segment.tool.status === 'error'" class="tool-content tool-content-error">
执行失败</pre>
      <div v-else-if="segment.tool.status === 'running'" class="tool-content tool-loading-text">
        执行中...
      </div>
    </div>

    <div v-else-if="segment.type === 'content'" class="message-content">
      <div class="content-actions">
        <el-tooltip v-if="!disableActions && segment.content" content="复制源文本" placement="top">
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
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  color: #64748b;
  font-size: 13px;
  cursor: pointer;
  user-select: none;
  transition: all 0.2s;
}

.expand-earlier:hover {
  color: #409eff;
  border-color: #409eff;
  background: #f8fafc;
}

.thinking-block,
.tool-block {
  border-radius: 12px;
  overflow: hidden;
  margin-bottom: 12px;
  border: 1px solid #e2e8f0;
  box-shadow:
    0 2px 15px -3px rgba(0, 0, 0, 0.07),
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
}

.code-block-header {
  background: #0f172a;
  padding: 8px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.tool-header-success {
  background: #059669;
}

.tool-header-error {
  background: #dc2626;
}

.tool-header-running {
  background: #0f172a;
}

.tool-header-icon {
  font-size: 20px;
  color: #fff;
  opacity: 0.9;
}

.code-block-dots {
  display: flex;
  gap: 5px;
}

.code-block-dots .dot-red,
.code-block-dots .dot-amber,
.code-block-dots .dot-green {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.code-block-dots .dot-red {
  background: rgba(239, 68, 68, 0.8);
}

.code-block-dots .dot-amber {
  background: rgba(245, 158, 11, 0.8);
}

.code-block-dots .dot-green {
  background: rgba(16, 185, 129, 0.8);
}

.tool-header-name {
  font-size: 15px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.01em;
}

.thinking-label {
  font-size: 14px;
  font-weight: 600;
  color: #cbd5e1;
  letter-spacing: 0.01em;
  text-transform: none;
}

.code-block-label {
  font-size: 10px;
  font-family: 'Courier New', monospace;
  color: #94a3b8;
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

.tool-status-badge {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 4px;
}

.tool-status-badge.running {
  background: rgba(245, 158, 11, 0.15);
  color: #fbbf24;
}

.tool-status-badge.success {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.tool-status-badge.error {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.status-spinner {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 2px solid rgba(251, 191, 36, 0.3);
  border-top-color: #fbbf24;
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
  background: rgba(248, 250, 252, 0.8);
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-word;
}

.tool-content {
  margin: 0;
  padding: 12px 16px;
  background: rgba(248, 250, 252, 0.8);
  font-family: 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #64748b;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 150px;
  overflow-y: auto;
}

.tool-content-args {
  border-top: 1px solid #e2e8f0;
}

.tool-content-args-wrapper {
  position: relative;
}

.args-toggle-btn {
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 11px;
  color: #64748b;
  z-index: 1;
}

.args-toggle-btn:hover {
  color: #409eff;
}

.tool-content-error {
  border-top: 1px solid #fecaca;
  background: rgba(254, 242, 242, 0.6);
  color: #dc2626;
}

.tool-loading-text {
  padding: 12px 16px;
  color: #94a3b8;
  font-size: 13px;
}

.tool-live-wrapper {
  border-top: 1px solid #e2e8f0;
  background: rgba(239, 246, 255, 0.6);
}

.tool-live-label {
  padding: 8px 16px 0;
  font-size: 11px;
  font-weight: 600;
  color: #2563eb;
}

.tool-live-output {
  border-top: none;
  max-height: 180px;
  color: #334155;
}

.revert-btn {
  color: #64748b;
  font-size: 14px;
  transition: color 0.2s;
}

.revert-btn:hover {
  color: #f87171;
}

.message-content {
  word-break: break-word;
  line-height: 1.7;
  background: #fff;
  padding: 20px 22px;
  border-radius: 4px 16px 16px 16px;
  border: 1px solid #e2e8f0;
  box-shadow:
    0 2px 15px -3px rgba(0, 0, 0, 0.07),
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
  margin-bottom: 10px;
  position: relative;
  font-size: 15px;
}

.content-actions {
  position: absolute;
  top: 2px;
  right: 14px;
  display: flex;
  align-items: center;
  gap: 2px;
}

.copy-btn {
  color: #c0c4cc;
  font-size: 14px;
  transition: color 0.2s;
}

.copy-btn:hover {
  color: #409eff;
}

.content-revert-btn {
  color: #c0c4cc;
  font-size: 14px;
  transition: color 0.2s;
}

.content-revert-btn:hover {
  color: #f56c6c;
}

.todo-block {
  background: #f9fafb;
  padding: 20px;
  margin-bottom: 12px;
  border-radius: 16px;
  border: 1px solid rgba(37, 99, 235, 0.08);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.todo-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.todo-badge {
  padding: 2px 8px;
  background: #2563eb;
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
  color: #1e293b;
}
</style>
