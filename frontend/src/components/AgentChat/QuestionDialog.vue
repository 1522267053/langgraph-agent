<script setup lang="ts">
/**
 * 问题反问弹窗
 *
 * LLM 调用 ask_user_question 工具时弹出。用户可点击选项或选择 "Other" 自填。
 * - 单选：选项互斥，点击即提交
 * - 多选：选项可多选，需点"确定"提交
 * - 倒计时归零：本地自动关闭（emit expire → store 清 pendingQuestion，不调
 *   resolve），后端超时后自行向 LLM 返回过期错误；倒计时来源为后端权威
 *   expires_in（刷新重连回放时由服务端重算）
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import { QuestionFilled, Check, Minus, FullScreen } from '@element-plus/icons-vue'
import { formatCountdown } from '@/utils/format'
import { USER_RESPONSE_COUNTDOWN_SECONDS } from '@/constants/timing'

interface QuestionOption {
  label: string
  description?: string
  preview?: string
}

interface PendingQuestion {
  questionId: string
  nodeKey: string
  question: string
  header?: string | null
  options: QuestionOption[]
  multiple: boolean
  expiresIn?: number
}

const props = defineProps<{
  question: PendingQuestion | null
  /** 子Agent名称（子Agent ask_user_question 穿透时显示来源标识，空表示当前Agent） */
  subAgentName?: string | null
}>()

const emit = defineEmits<{
  (e: 'submit', answers: string[]): void
  (e: 'expire'): void
}>()

// 多选模式下的当前选中 label 集合
const selectedLabels = ref<Set<string>>(new Set())
// Other 模式：自填文本
const customText = ref('')
// 是否正在显示 Other 输入框
const showCustomInput = ref(false)
// 用户已主动提交（submit / cancel 按钮触发过）后置 true，
// 防止 store 清空 pendingQuestion 引发 dialog v-model 同步时再次误触发 cancel → submit
const submitted = ref(false)
// 最小化状态：true 时隐藏弹窗（含遮罩），右下角显示悬浮条，可随时恢复
const minimized = ref(false)

function minimize(): void {
  minimized.value = true
}

function restore(): void {
  minimized.value = false
}

// ---- 回答倒计时 ----
const remainingSeconds = ref(0)
const expired = ref(false)
let countdownTimer: ReturnType<typeof setInterval> | null = null

function stopCountdown(): void {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
}

function startCountdown(q: PendingQuestion): void {
  stopCountdown()
  expired.value = false
  const fromServer = Math.floor(Number(q.expiresIn) || 0)
  remainingSeconds.value = fromServer > 0 ? fromServer : USER_RESPONSE_COUNTDOWN_SECONDS
  countdownTimer = setInterval(() => {
    remainingSeconds.value -= 1
    if (remainingSeconds.value > 0) return
    stopCountdown()
    expired.value = true
    // 置 submitted 吞掉 store 清空 question 引发的 v-model cancel（防误发 submit([])）
    submitted.value = true
    emit('expire')
  }, 1000)
}

const visible = computed(() => props.question !== null)
const isMultiple = computed(() => props.question?.multiple ?? false)

// Other 自填是否有内容：作为 Other 选项的勾选态（有内容即勾选）
const otherSelected = computed(() => customText.value.trim().length > 0)

// 单选互斥：Other 有内容时清除已选选项；多选允许勾选项与 Other 并存
watch(customText, text => {
  if (text.trim() && !isMultiple.value) {
    selectedLabels.value = new Set()
  }
})

// 监听 question 变化 → 重置表单 + 重启倒计时
watch(
  () => props.question,
  q => {
    selectedLabels.value = new Set()
    customText.value = ''
    showCustomInput.value = false
    submitted.value = false // 新问题到来时重置提交标志
    minimized.value = false // 新问题默认弹出（最小化只由用户主动触发）
    if (q) startCountdown(q)
    else stopCountdown()
  }
)

onUnmounted(stopCountdown)

function pickOption(opt: QuestionOption) {
  if (expired.value) return
  if (isMultiple.value) {
    // 多选：toggle 选中
    if (selectedLabels.value.has(opt.label)) {
      selectedLabels.value.delete(opt.label)
    } else {
      selectedLabels.value.add(opt.label)
    }
    // 触发响应式
    selectedLabels.value = new Set(selectedLabels.value)
  } else {
    // 单选：radio 语义，替换选中并放弃 Other 自填（与 Other 互斥），统一由底部确定提交
    selectedLabels.value = new Set([opt.label])
    customText.value = ''
  }
}

function pickCustom() {
  if (expired.value) return
  showCustomInput.value = true
}

function submitMultiple() {
  if (expired.value) return
  const answers: string[] = []
  for (const label of selectedLabels.value) answers.push(label)
  // Other 自填内容始终追加：多选 = 勾选项与 Other 可并存
  const custom = customText.value.trim()
  if (custom) answers.push(custom)
  submitted.value = true
  emit('submit', answers)
}

// 底部统一确认：多选提交勾选集合；单选提交选中项（自填内容优先——输入即表示
// 选项都不符合），无任何选择时不可提交
function submitDialog() {
  if (expired.value) return
  if (isMultiple.value) {
    submitMultiple()
    return
  }
  const custom = customText.value.trim()
  const label = [...selectedLabels.value][0]
  const answer = custom || label
  if (!answer) return
  submitted.value = true
  emit('submit', [answer])
}

// 确定按钮可用性：需有勾选/选中项或自填内容
const confirmDisabled = computed(() => {
  return expired.value || (selectedLabels.value.size === 0 && !customText.value.trim())
})
</script>

<template>
  <el-dialog
    :model-value="visible && !minimized"
    width="560px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
    destroy-on-close
  >
    <template #header>
      <div class="dialog-header">
        <el-icon :size="20" class="dialog-icon"><QuestionFilled /></el-icon>
        <span class="dialog-title">{{ question?.header || '问题反问' }}</span>
        <!-- 模式标识：单选点击替换选中，多选可勾选多项后统一提交 -->
        <span class="dialog-mode" :class="isMultiple ? 'is-multiple' : 'is-single'">
          {{ isMultiple ? '多选' : '单选' }}
        </span>
        <span v-if="subAgentName" class="dialog-sub-agent">子Agent「{{ subAgentName }}」</span>
        <span v-if="expired" class="dialog-expired">已过期</span>
        <span v-else-if="remainingSeconds > 0" class="dialog-countdown">
          {{ formatCountdown(remainingSeconds) }}
        </span>
        <!-- 最小化：隐藏弹窗与遮罩，右下角悬浮条恢复；倒计时与已选状态保持 -->
        <button
          class="dialog-minimize"
          type="button"
          title="最小化（可回看对话，稍后恢复作答）"
          @click="minimize"
        >
          <el-icon :size="16"><Minus /></el-icon>
        </button>
      </div>
    </template>

    <div v-if="question" class="question-body" :class="{ 'is-expired': expired }">
      <div class="question-text">{{ question.question }}</div>

      <div class="options-list">
        <button
          v-for="opt in question.options"
          :key="opt.label"
          class="option-item"
          :class="{
            selected: selectedLabels.has(opt.label),
            'option-disabled': expired
          }"
          :disabled="expired"
          @click="pickOption(opt)"
        >
          <span class="option-marker">
            <el-icon v-if="selectedLabels.has(opt.label)">
              <Check />
            </el-icon>
            <span v-else class="marker-empty">○</span>
          </span>
          <div class="option-content">
            <div class="option-label">{{ opt.label }}</div>
            <div v-if="opt.description" class="option-description">
              {{ opt.description }}
            </div>
            <div v-if="opt.preview" class="option-preview">
              <pre>{{ opt.preview }}</pre>
            </div>
          </div>
        </button>

        <!-- Other 自填选项（固定追加；提交走底部统一确认按钮） -->
        <button
          class="option-item option-other"
          :class="{ selected: otherSelected, 'option-disabled': expired }"
          :disabled="expired"
          @click="pickCustom"
        >
          <span class="option-marker">
            <el-icon v-if="otherSelected">
              <Check />
            </el-icon>
            <span v-else class="marker-empty">✎</span>
          </span>
          <div class="option-content">
            <div class="option-label">Other（手动输入）</div>
            <div v-if="showCustomInput" class="custom-input-wrap" @click.stop>
              <el-input
                v-model="customText"
                type="textarea"
                :rows="2"
                placeholder="输入你的回答"
                autofocus
              />
            </div>
            <div v-else class="option-description">提供自定义回答</div>
          </div>
        </button>
      </div>
    </div>

    <!-- 底部统一确认：多选提交勾选集合；单选仅在其他输入有内容时可确定。
         无取消按钮——用户必须从选项中选一个答案；超时由后端兜底 -->
    <template v-if="visible" #footer>
      <div class="dialog-footer">
        <el-button type="primary" :disabled="confirmDisabled" @click="submitDialog">确定</el-button>
      </div>
    </template>
  </el-dialog>

  <!-- 最小化悬浮条：弹窗隐藏期间显示，点击恢复作答；倒计时/过期状态实时同步 -->
  <Teleport to="body">
    <button
      v-if="visible && minimized"
      class="question-minimized-bar"
      type="button"
      :class="{ 'is-expired': expired }"
      @click="restore"
    >
      <el-icon :size="16" class="bar-icon"><QuestionFilled /></el-icon>
      <span class="bar-title">{{ question?.header || '问题反问' }}</span>
      <span class="bar-question">{{ question?.question }}</span>
      <span v-if="expired" class="bar-expired">已过期</span>
      <span v-else-if="remainingSeconds > 0" class="bar-countdown">
        {{ formatCountdown(remainingSeconds) }}
      </span>
      <el-icon :size="14" class="bar-expand"><FullScreen /></el-icon>
    </button>
  </Teleport>
</template>

<style scoped>
.dialog-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dialog-icon {
  color: #a855f7;
}

.dialog-title {
  font-weight: 600;
  font-size: 15px;
  color: #303133;
}

.dialog-mode {
  flex-shrink: 0;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
}

.dialog-mode.is-multiple {
  color: #7e22ce;
  background: #f3e8ff;
}

.dialog-mode.is-single {
  color: #1d4ed8;
  background: #dbeafe;
}

.dialog-sub-agent {
  flex-shrink: 0;
  font-size: 12px;
  color: #b45309;
  background: #fef3c7;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
}

.dialog-countdown {
  margin-left: auto;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: #e6a23c;
  background: #fdf6ec;
  padding: 2px 8px;
  border-radius: 10px;
}

.dialog-expired {
  margin-left: auto;
  font-size: 13px;
  color: #909399;
  background: #f4f4f5;
  padding: 2px 8px;
  border-radius: 10px;
}

.question-body.is-expired {
  opacity: 0.55;
  pointer-events: none;
}

.option-disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.question-body {
  padding: 4px 0;
}

.question-text {
  font-size: 14px;
  line-height: 1.6;
  color: #303133;
  margin-bottom: 16px;
  padding: 12px;
  background: #faf5ff;
  border-left: 3px solid #a855f7;
  border-radius: 4px;
}

.options-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 400px;
  overflow-y: auto;
}

.option-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
  width: 100%;
  background-color: #fff;
}

.option-item:hover {
  border-color: #a855f7;
  background: #faf5ff;
}

.option-item.selected {
  border-color: #a855f7;
  background: #f3e8ff;
}

.option-marker {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #a855f7;
  font-weight: 600;
  font-size: 18px;
  line-height: 1;
}

.marker-empty {
  color: #c0c4cc;
}

.option-content {
  flex: 1;
  min-width: 0;
}

.option-label {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 2px;
}

.option-description {
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}

.option-preview {
  margin-top: 8px;
  padding: 8px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  max-height: 120px;
  overflow-y: auto;
}

.option-preview pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: 'Menlo', 'Monaco', monospace;
}

.custom-input-wrap {
  margin-top: 8px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* header 最小化按钮 */
.dialog-minimize {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  margin-left: 8px;
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

/* 最小化悬浮条（Teleport 到 body，fixed 定位不随页面滚动） */
.question-minimized-bar {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 2500;
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 420px;
  padding: 10px 14px;
  border: 1px solid #a855f7;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 4px 16px rgba(168, 85, 247, 0.25);
  cursor: pointer;
  text-align: left;
  transition: box-shadow 0.15s, transform 0.15s;
}

.question-minimized-bar:hover {
  box-shadow: 0 6px 20px rgba(168, 85, 247, 0.35);
  transform: translateY(-1px);
}

.question-minimized-bar.is-expired {
  border-color: #dcdfe6;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  opacity: 0.75;
}

.bar-icon {
  flex-shrink: 0;
  color: #a855f7;
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

.bar-countdown {
  flex-shrink: 0;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: #e6a23c;
  background: #fdf6ec;
  padding: 2px 8px;
  border-radius: 10px;
}

.bar-expired {
  flex-shrink: 0;
  font-size: 12px;
  color: #909399;
  background: #f4f4f5;
  padding: 2px 8px;
  border-radius: 10px;
}

.bar-expand {
  flex-shrink: 0;
  color: #c0c4cc;
}
</style>
