<script setup lang="ts">
/**
 * 问题反问弹窗
 *
 * LLM 调用 ask_user_question 工具时弹出。用户可点击选项或选择 "Other" 自填。
 * - 单选：选项互斥，点击即提交
 * - 多选：选项可多选，需点"确定"提交
 *
 * 与 tool_approval 的差异：这里是结构化选项，不阻塞（用户必须主动选）
 */
import { computed, ref, watch } from 'vue'
import { QuestionFilled, Check } from '@element-plus/icons-vue'

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
}

const props = defineProps<{
  question: PendingQuestion | null
}>()

const emit = defineEmits<{
  (e: 'submit', answers: string[]): void
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

const visible = computed(() => props.question !== null)
const isMultiple = computed(() => props.question?.multiple ?? false)

// 监听 question 变化 → 重置表单
watch(
  () => props.question,
  q => {
    selectedLabels.value = new Set()
    customText.value = ''
    showCustomInput.value = false
    submitted.value = false  // 新问题到来时重置提交标志
    // 默认聚焦到第一个选项（无操作）
    void q
  }
)

function pickOption(opt: QuestionOption) {
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
    // 单选：直接提交
    submitted.value = true
    emit('submit', [opt.label])
  }
}

function pickCustom() {
  showCustomInput.value = true
}

function submitCustom() {
  const text = customText.value.trim()
  if (!text) return
  submitted.value = true
  emit('submit', [text])
}

function submitMultiple() {
  const answers: string[] = []
  for (const label of selectedLabels.value) answers.push(label)
  // 若用户没选任何项但已填 Other，自动带上
  if (answers.length === 0 && customText.value.trim()) {
    answers.push(customText.value.trim())
  }
  submitted.value = true
  emit('submit', answers)
}

function cancel() {
  // 守卫：用户已主动 submit（pickOption / submitCustom / submitMultiple）后，
  // el-dialog 的 v-model 同步仍会触发 @update:model-value="cancel"，
  // 此时若不拦截会 emit 第二个 submit([]) → 后端收到两次 /question/resolve 请求
  if (submitted.value) return
  submitted.value = true
  emit('submit', []) // 空数组表示取消
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    width="560px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
    destroy-on-close
    @update:model-value="cancel"
  >
    <template #header>
      <div class="dialog-header">
        <el-icon :size="20" class="dialog-icon"><QuestionFilled /></el-icon>
        <span class="dialog-title">{{ question?.header || '问题反问' }}</span>
      </div>
    </template>

    <div v-if="question" class="question-body">
      <div class="question-text">{{ question.question }}</div>

      <div class="options-list">
        <button
          v-for="opt in question.options"
          :key="opt.label"
          class="option-item"
          :class="{
            selected: selectedLabels.has(opt.label) && isMultiple
          }"
          @click="pickOption(opt)"
        >
          <span v-if="isMultiple" class="option-marker">
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

        <!-- Other 自填选项（固定追加） -->
        <button class="option-item option-other" @click="pickCustom">
          <span class="option-marker"><span class="marker-empty">✎</span></span>
          <div class="option-content">
            <div class="option-label">Other（手动输入）</div>
            <div v-if="showCustomInput" class="custom-input-wrap" @click.stop>
              <el-input
                v-model="customText"
                type="textarea"
                :rows="2"
                placeholder="输入你的回答"
                autofocus
                @keyup.enter.ctrl="submitCustom"
                @keyup.enter.meta="submitCustom"
              />
              <div class="custom-actions">
                <el-button size="small" @click.stop="cancel">取消</el-button>
                <el-button
                  size="small"
                  type="primary"
                  :disabled="!customText.trim()"
                  @click.stop="submitCustom"
                >
                  确定
                </el-button>
              </div>
            </div>
            <div v-else class="option-description">提供自定义回答</div>
          </div>
        </button>
      </div>
    </div>

    <template v-if="visible && isMultiple" #footer>
      <div class="dialog-footer">
        <el-button @click="cancel">取消</el-button>
        <el-button
          type="primary"
          :disabled="selectedLabels.size === 0 && !customText.trim()"
          @click="submitMultiple"
        >
          确定
        </el-button>
      </div>
    </template>
  </el-dialog>
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

.custom-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
