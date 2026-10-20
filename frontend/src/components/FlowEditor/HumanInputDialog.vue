<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { QuestionFilled, Warning, Minus, FullScreen } from '@element-plus/icons-vue'
import { formatToolArgs, formatCountdown } from '@/utils/format'
import { useDraggableBar } from '@/composables/useDraggableBar'
import { useDialogExitSnapshot } from '@/composables/useDialogExitSnapshot'

interface ConversationMessage {
  role: string
  content: string
  name?: string
  tool_calls?: Array<{ name: string; args: Record<string, unknown>; id?: string }>
}

const props = defineProps<{
  visible: boolean
  question: string
  context: string
  messages: ConversationMessage[]
  loading: boolean
  /** 工具审批模式（Flow future 路线）：非空时渲染独立审批弹窗（对齐 AgentChat 审批弹窗风格） */
  approval?: {
    toolCalls: Array<{ id?: string; name: string; args: Record<string, unknown> }>
    approvalNeeded: string[]
    approvalReason: string | null
    /** 倒计时死线（本地毫秒时间戳；null 不显示倒计时） */
    expiresAt?: number | null
  } | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'submit', value: string): void
  (e: 'cancel'): void
  (e: 'approval', action: 'approved' | 'rejected', rememberSession: boolean, toolDecisions?: Record<string, 'approved' | 'rejected'>): void
}>()

const inputValue = ref('')
// 最小化状态：true 时隐藏弹窗（含遮罩），右下角显示悬浮条，可随时恢复。
// 输入内容保留——最小化/恢复间不丢已填回答
const minimized = ref(false)

const isApprovalMode = computed(() => !!props.approval)

// 退场快照：审批决策提交后父组件清空 pendingApproval（await 响应后），
// 关闭动画期间 approval 已空导致内容先消失——快照最近非空批次续渲染，
// 动画结束 @closed 重置
const {
  display: displayApproval,
  reset: resetApprovalSnapshot
} = useDialogExitSnapshot(() => props.approval, a => !a)

// ---- 逐工具决策（方案B：单窗口多卡片独立决策） ----
// tool_call_id → 用户选择；默认全部 approved（卡片上可单独切为拒绝）。
// 仅当用户改过任一卡片（存在 rejected）才走逐工具分发，否则保持整批语义。
type ToolDecision = 'approved' | 'rejected'
const toolDecisions = ref<Record<string, ToolDecision>>({})

// 批内是否存在逐工具的拒绝项（决定提交时是否携带 tool_decisions）
const hasPerToolRejection = computed(
  () => Object.values(toolDecisions.value).some(d => d === 'rejected')
)

// 多卡片时显示逐工具开关；单卡片保持经典三按钮（决策粒度等价，不添乱）
const showPerToolControls = computed(
  () => (props.approval?.toolCalls.length ?? 0) > 1
)

function decisionKey(tc: { id?: string; name: string }, index: number): string {
  return tc.id || `${tc.name}#${index}`
}

function getDecision(tc: { id?: string; name: string }, index: number): ToolDecision {
  return toolDecisions.value[decisionKey(tc, index)] || 'approved'
}

function toggleDecision(tc: { id?: string; name: string }, index: number): void {
  const key = decisionKey(tc, index)
  toolDecisions.value[key] = getDecision(tc, index) === 'approved' ? 'rejected' : 'approved'
}

// 全拒守卫：所有卡片均为 rejected 时，「批准/批准并记住」改走整批拒绝——
// 保证审计语义正确（interaction_audit_log 记 rejected 而非 approved），文案与结果不脱钩
function isAllRejected(): boolean {
  const calls = props.approval?.toolCalls ?? []
  return calls.length > 0 && calls.every((tc, i) => getDecision(tc, i) === 'rejected')
}

/** 批准（守卫接管：全 rejected 时上抛 rejected 整批决策） */
function handleApprove(): void {
  if (isAllRejected()) {
    emit('approval', 'rejected', false)
    return
  }
  emit('approval', 'approved', false, buildToolDecisions())
}

/** 批准并记住（守卫同上） */
function handleApproveAndRemember(): void {
  if (isAllRejected()) {
    emit('approval', 'rejected', false)
    return
  }
  emit('approval', 'approved', true, buildToolDecisions())
}

/** 组装逐工具决策 payload（仅存在拒绝项时携带，缺省走整批语义） */
function buildToolDecisions(): Record<string, ToolDecision> | undefined {
  if (!showPerToolControls.value || !hasPerToolRejection.value) return undefined
  const result: Record<string, ToolDecision> = {}
  for (let i = 0; i < (props.approval?.toolCalls.length ?? 0); i++) {
    const tc = props.approval!.toolCalls[i]
    result[decisionKey(tc, i)] = getDecision(tc, i)
  }
  return result
}

// 弹窗重开（新批次到来）时重置逐工具决策
watch(
  () => props.approval?.toolCalls,
  () => {
    toolDecisions.value = {}
  }
)

// ---- 审批倒计时（对齐 AgentChat approvalCountdown：每秒重算，过期显示「已过期」） ----
const countdownTick = ref(0)
let countdownTimer: ReturnType<typeof setInterval> | null = null

const approvalCountdown = computed<number>(() => {
  void countdownTick.value
  const deadline = props.approval?.expiresAt
  if (!deadline) return 0
  return Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
})

const approvalExpired = computed(
  () => isApprovalMode.value && !!props.approval?.expiresAt && approvalCountdown.value <= 0
)

watch(
  () => [props.visible, props.approval?.expiresAt] as const,
  ([visible]) => {
    if (visible && isApprovalMode.value) {
      if (!countdownTimer) {
        countdownTimer = setInterval(() => {
          countdownTick.value++
        }, 1000)
      }
    } else if (countdownTimer) {
      clearInterval(countdownTimer)
      countdownTimer = null
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
})

function minimize(): void {
  minimized.value = true
}

function restore(): void {
  minimized.value = false
}

// 最小化悬浮条可拖动（位置记忆 localStorage；位移<4px 仍视为点击恢复）
const { barStyle: barDragStyle, onPointerDown: onBarPointerDown, isDragging: barDragging } =
  useDraggableBar('agent-approval-bar')

/**
 * el-dialog model-value 变化分流：
 * - 最小化引发的内部关闭（minimized=true）不上报父组件——否则父组件把 visible
 *   置 false，悬浮条（visible && minimized）随之消失，最小化失效
 * - 真实用户关闭（modal/esc，当前均已禁用）仍透传，保持 v-model:visible 语义
 */
function onDialogModelChange(val: boolean): void {
  if (!val && minimized.value) return
  emit('update:visible', val)
}

// 弹窗重新打开（新问题到来）时退出最小化，默认弹出
watch(
  () => props.visible,
  visible => {
    if (visible) minimized.value = false
  }
)

// 悬浮条摘要：审批模式显示工具名，提问模式显示问题
const questionPreview = computed(() => {
  if (isApprovalMode.value && props.approval) {
    const names = props.approval.toolCalls.map(tc => tc.name).join('、')
    return names ? `请求执行工具：${names}` : '请求执行工具'
  }
  return props.question || '请提供输入'
})

function getRoleLabel(role: string): string {
  const labels: Record<string, string> = {
    system: '系统',
    user: '用户',
    assistant: 'AI',
    tool: '工具'
  }
  return labels[role] || role
}

function handleSubmit(): void {
  if (!inputValue.value.trim()) return
  emit('submit', inputValue.value)
}

function handleOpen(): void {
  inputValue.value = ''
}
</script>

<template>
  <!-- 常规人工输入弹窗（审批模式下隐藏，由下方独立审批弹窗接管） -->
  <el-dialog
    :model-value="visible && !minimized && !isApprovalMode"
    title="需要您的输入"
    width="600px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
    @update:model-value="onDialogModelChange"
    @open="handleOpen"
  >
    <template #header>
      <div class="dialog-header">
        <span class="dialog-title">需要您的输入</span>
        <!-- 最小化：隐藏弹窗与遮罩，右下角悬浮条恢复；已填回答保留 -->
        <button
          class="dialog-minimize"
          type="button"
          title="最小化（可回看执行输出，稍后恢复作答）"
          @click="minimize"
        >
          <el-icon :size="16"><Minus /></el-icon>
        </button>
      </div>
    </template>
    <div class="human-input-content">
      <div class="question">
        <el-icon style="margin-right: 8px; color: #e6a23c">
          <QuestionFilled />
        </el-icon>
        {{ question }}
      </div>

      <div v-if="messages.length > 0 || context" class="context-panel">
        <div class="context-label">上下文与对话历史：</div>

        <div v-if="messages.length > 0" class="history-messages">
          <div
            v-for="(msg, index) in messages"
            :key="index"
            :class="['message-item', `message-${msg.role}`]"
          >
            <span class="message-role">
              {{ getRoleLabel(msg.role) }}
              <template v-if="msg.name">({{ msg.name }})</template>
            </span>
            <div class="message-content">
              <template v-if="msg.tool_calls && msg.tool_calls.length > 0">
                <div class="tool-calls">
                  <div
                    v-for="(tc, tcIndex) in msg.tool_calls"
                    :key="tcIndex"
                    class="tool-call-item"
                  >
                    <div class="tool-call-name">🔧 {{ tc.name }}</div>
                    <pre v-if="tc.args && Object.keys(tc.args).length > 0" class="tool-call-args">{{
                      formatToolArgs(tc.args, 200)
                    }}</pre>
                  </div>
                </div>
                <div v-if="msg.content" class="tool-call-content">{{ msg.content }}</div>
              </template>
              <template v-else>{{ msg.content }}</template>
            </div>
          </div>
        </div>

        <div v-if="context" class="context-text">{{ context }}</div>
      </div>
      <el-input
        v-model="inputValue"
        type="textarea"
        :rows="4"
        placeholder="请输入您的回答..."
        @keydown.enter.ctrl="handleSubmit"
      />
    </div>
    <template #footer>
      <div style="display: flex; justify-content: space-between; width: 100%">
        <el-button @click="emit('cancel')">取消执行</el-button>
        <el-button type="primary" :loading="loading" @click="handleSubmit">提交并继续</el-button>
      </div>
    </template>
  </el-dialog>

  <!-- 工具审批弹窗：独立 el-dialog，风格对齐 AgentChat 审批弹窗
       （Warning 图标 + 「请求执行工具」标题 + 深色参数块 + 拒绝/批准并记住/批准） -->
  <el-dialog
    :model-value="visible && !minimized && isApprovalMode"
    width="560px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
    destroy-on-close
    @update:model-value="onDialogModelChange"
    @closed="resetApprovalSnapshot"
  >
    <template #header>
      <div class="approval-dialog-header">
        <el-icon :size="20" class="approval-dialog-icon"><Warning /></el-icon>
        <span class="approval-dialog-title">请求执行工具</span>
        <span v-if="approvalExpired" class="approval-dialog-expired">已过期</span>
        <span v-else-if="approvalCountdown > 0" class="approval-dialog-countdown">
          {{ formatCountdown(approvalCountdown) }}
        </span>
        <button
          class="dialog-minimize"
          type="button"
          title="最小化（可回看执行输出，稍后恢复审批）"
          @click="minimize"
        >
          <el-icon :size="16"><Minus /></el-icon>
        </button>
      </div>
    </template>
    <div v-if="displayApproval" class="approval-body">
      <div v-if="displayApproval.approvalReason" class="approval-reason">
        审批原因：{{ displayApproval.approvalReason }}
      </div>
      <div class="approval-tools">
        <div
          v-for="(tc, index) in displayApproval.toolCalls"
          :key="index"
          class="approval-tool-item"
          :class="{ 'approval-tool-rejected': getDecision(tc, index) === 'rejected' }"
        >
          <div class="approval-tool-name">
            <el-tag
              :type="displayApproval.approvalNeeded.includes(tc.name) ? 'danger' : 'info'"
              size="small"
              style="margin-right: 6px"
            >
              {{ displayApproval.approvalNeeded.includes(tc.name) ? '需确认' : '普通' }}
            </el-tag>
            {{ tc.name }}
            <el-switch
              v-if="showPerToolControls"
              class="approval-tool-switch"
              :model-value="getDecision(tc, index) === 'approved'"
              inline-prompt
              active-text="批准"
              inactive-text="拒绝"
              @change="toggleDecision(tc, index)"
            />
          </div>
          <pre v-if="tc.args && Object.keys(tc.args).length > 0" class="approval-tool-args">{{
            formatToolArgs(tc.args, 500)
          }}</pre>
        </div>
      </div>
    </div>
    <template #footer>
      <div class="approval-dialog-footer">
        <el-button type="danger" :loading="loading" @click="emit('approval', 'rejected', false)">
          拒绝
        </el-button>
        <div>
          <el-button :loading="loading" @click="handleApproveAndRemember">
            批准并记住
          </el-button>
          <el-button type="primary" :loading="loading" @click="handleApprove">
            批准
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>

  <!-- 最小化悬浮条：弹窗隐藏期间显示，点击恢复作答/审批 -->
  <Teleport to="body">
    <button
      v-if="visible && minimized"
      class="human-input-minimized-bar"
      :class="{ 'is-dragging': barDragging }"
      type="button"
      :style="barDragStyle"
      @pointerdown="onBarPointerDown"
      @click="restore"
    >
      <el-icon :size="16" class="bar-icon">
        <Warning v-if="isApprovalMode" />
        <QuestionFilled v-else />
      </el-icon>
      <span class="bar-title">{{ isApprovalMode ? '请求执行工具' : '需要您的输入' }}</span>
      <span class="bar-question">{{ questionPreview }}</span>
      <el-icon :size="14" class="bar-expand"><FullScreen /></el-icon>
    </button>
  </Teleport>
</template>

<style scoped>
.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.dialog-title {
  font-weight: 600;
  font-size: 15px;
  color: #303133;
}

/* header 最小化按钮（提问弹窗 / 审批弹窗共用） */
.dialog-minimize {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #909399;
  cursor: pointer;
  transition: all 0.15s;
}

.dialog-minimize:hover {
  background: #f0f2f5;
  color: #606266;
}

/* ---- 审批弹窗（对齐 AgentChat 审批弹窗风格） ---- */
.approval-dialog-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.approval-dialog-icon {
  color: #e6a23c;
}

.approval-dialog-title {
  font-weight: 600;
  font-size: 15px;
  color: #303133;
}

/* 倒计时/过期徽章（对齐 AgentChat approval-dialog-countdown/expired） */
.approval-dialog-countdown {
  margin-left: auto;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: #e6a23c;
  background: #fdf6ec;
  padding: 2px 8px;
  border-radius: 10px;
}

.approval-dialog-expired {
  margin-left: auto;
  font-size: 13px;
  color: #909399;
  background: #f4f4f5;
  padding: 2px 8px;
  border-radius: 10px;
}

.approval-body {
  padding: 2px 0;
}

.approval-reason {
  background: #fdf6ec;
  color: #e6a23c;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  margin-bottom: 12px;
}

.approval-tools {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.approval-tool-item {
  background: var(--paper-warm, #f5f7fa);
  border-radius: 8px;
  padding: 12px;
}

/* 逐工具决策为拒绝时卡片降透明度提示（仍展示参数供回看） */
.approval-tool-item.approval-tool-rejected {
  opacity: 0.55;
}

.approval-tool-name {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
}

/* 逐工具决策开关推到卡片行尾 */
.approval-tool-switch {
  margin-left: auto;
}

.approval-tool-args {
  font-size: 12px;
  background: var(--ink-island, #2b2b2b);
  color: #e8e3d8;
  padding: 10px 12px;
  border-radius: 6px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}

.approval-dialog-footer {
  display: flex;
  justify-content: space-between;
  width: 100%;
}

/* 最小化悬浮条（Teleport 到 body，fixed 定位） */
.human-input-minimized-bar {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 2500;
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 420px;
  padding: 10px 14px;
  border: 1px solid #e6a23c;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 4px 16px rgba(230, 162, 60, 0.25);
  cursor: pointer;
  text-align: left;
  transition: box-shadow 0.15s, transform 0.15s;
}

.human-input-minimized-bar:hover {
  box-shadow: 0 6px 20px rgba(230, 162, 60, 0.35);
  transform: translateY(-1px);
}

/* 拖动中：grabbing 光标 + 禁 hover 位移（防抖动） */
.human-input-minimized-bar.is-dragging {
  cursor: grabbing;
  transform: none;
  transition: none;
}

.bar-icon {
  flex-shrink: 0;
  color: #e6a23c;
}

.bar-title {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.bar-question {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar-expand {
  flex-shrink: 0;
  color: #c0c4cc;
}

.human-input-content {
  padding: 10px 0;
}

.human-input-content .question {
  font-size: 16px;
  font-weight: 500;
  margin-bottom: 16px;
  display: flex;
  align-items: flex-start;
}

.human-input-content .context-panel {
  background: #f5f7fa;
  border-radius: 6px;
  margin-bottom: 16px;
  max-height: 300px;
  overflow-y: auto;
}

.human-input-content .context-label {
  font-size: 12px;
  color: #909399;
  padding: 8px 12px 4px;
  border-bottom: 1px solid #ebeef5;
}

.human-input-content .history-messages {
  padding: 8px 12px;
}

.human-input-content .context-text {
  font-size: 14px;
  color: #606266;
  white-space: pre-wrap;
  padding: 8px 12px;
  border-top: 1px solid #ebeef5;
}

.human-input-content .message-item {
  margin-bottom: 10px;
  padding: 8px;
  border-radius: 4px;
}

.human-input-content .message-item:last-child {
  margin-bottom: 0;
}

.human-input-content .message-system {
  background: #f0f9eb;
}

.human-input-content .message-user {
  background: #ecf5ff;
}

.human-input-content .message-assistant {
  background: #fef0f0;
}

.human-input-content .message-tool {
  background: #fdf6ec;
}

.human-input-content .message-role {
  font-size: 12px;
  font-weight: 500;
  color: #606266;
  margin-bottom: 4px;
  display: block;
}

.human-input-content .message-content {
  font-size: 13px;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
}

.human-input-content .tool-calls {
  margin-bottom: 8px;
}

.human-input-content .tool-call-item {
  background: rgba(0, 0, 0, 0.03);
  border-radius: 4px;
  padding: 6px 8px;
  margin-bottom: 6px;
}

.human-input-content .tool-call-item:last-child {
  margin-bottom: 0;
}

.human-input-content .tool-call-name {
  font-weight: 500;
  color: #409eff;
  margin-bottom: 4px;
}

.human-input-content .tool-call-args {
  margin: 0;
  padding: 6px;
  background: #f5f5f5;
  border-radius: 4px;
  font-size: 12px;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 120px;
  overflow-y: auto;
}

.human-input-content .tool-call-content {
  padding-top: 8px;
  border-top: 1px dashed #dcdfe6;
}
</style>
