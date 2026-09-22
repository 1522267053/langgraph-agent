<script setup lang="ts">
import type { FlowIOField } from '@/types/flow'
import type { FileInfo } from '@/api/file'
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Loading,
  Promotion,
  Document,
  FolderOpened,
  CircleClose,
  QuestionFilled,
  List,
  Lightning,
  Cpu
} from '@element-plus/icons-vue'
import FilePickerDialog from '@/components/common/FilePickerDialog.vue'
import type { ReasoningMeta } from '@/api/ai_provider'

const props = defineProps<{
  fields: FlowIOField[]
  isStreaming: boolean
  isStopping?: boolean
  isWaitingHuman: boolean
  totalTokens?: number
  latestPromptTokens?: number
  planMode?: boolean
  restoreParams?: Record<string, unknown> | null
  /** 会话级项目工作路径（空表示未选择，使用 Agent 默认工作目录） */
  workDir?: string
  /** 可选模型分组（按供应商分组；为空时不展示下拉框） */
  modelGroups?: Array<{
    label: string
    options: Array<{ value: string; label: string; multimodal?: boolean }>
  }>
  /** Agent 配置的默认模型名（仅展示用） */
  defaultModelLabel?: string
  /** 当前选中模型的推理深度档位（null=无元数据/未选模型，隐藏选择器） */
  reasoningOptions?: ReasoningMeta | null
}>()

const emit = defineEmits<{
  (
    e: 'send',
    params: Record<string, unknown>,
    attachedFiles: Array<{ id: number; original_name: string; mime_type: string }>,
    message: string
  ): void
  (e: 'stop'): void
  (e: 'toggle-plan-mode'): void
  (e: 'restore-consumed'): void
  (e: 'select-workdir'): void
  (e: 'clear-workdir'): void
}>()

const inputMessage = defineModel<string>('inputMessage', { default: '' })
/** 临时模型覆盖（空串/未选表示使用 Agent 默认模型） */
const selectedModel = defineModel<string>('selectedModel', { default: '' })
/** 临时推理深度覆盖（空串=跟随节点配置；仅选中模型后可操作） */
const selectedReasoning = defineModel<string>('selectedReasoning', { default: '' })
const sendMessageDisabled = computed(() => !inputMessage.value.trim())

const showModelSelect = computed(() => props.modelGroups?.some(g => g.options.length > 0) ?? false)

/** 推理深度选择器：仅当前模型带声明档位（effort/toggle/budget 预设）时显示 */
const showReasoningSelect = computed(() => !!props.reasoningOptions && !!selectedModel.value)

// ---- 推理深度：图标按钮 + popover 菜单形态（省工具栏横向空间） ----
const reasoningPopoverVisible = ref(false)

/** 按钮上的档位徽标：off 显示「关闭」，其余显示档位原文，空=纯图标 */
const reasoningBtnLabel = computed(() => {
  if (!selectedReasoning.value) return ''
  return selectedReasoning.value === 'off' ? '关闭' : selectedReasoning.value
})

function selectReasoning(value: string) {
  selectedReasoning.value = value
  reasoningPopoverVisible.value = false
}

// ---- 模型切换：图标按钮 + popover 分组菜单形态（与推理深度同款交互） ----
const modelPopoverVisible = ref(false)

/** 按钮上的模型徽标：临时模型显示模型名，未选显示「默认」 */
const modelBtnLabel = computed(() => {
  if (selectedModel.value) {
    return selectedModel.value.split('::')[1] || selectedModel.value
  }
  return ''
})

function selectModel(value: string) {
  selectedModel.value = value
  modelPopoverVisible.value = false
}

/** 工作路径缩略显示：取末段目录名 */
const workDirName = computed(() => {
  const dir = props.workDir
  if (!dir) return ''
  const normalized = dir.replace(/[\\/]+$/, '')
  const segments = normalized.split(/[\\/]/)
  return segments[segments.length - 1] || dir
})

function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return (tokens / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M'
  if (tokens >= 1_000) return (tokens / 1_000).toFixed(1).replace(/\.0$/, '') + 'K'
  return tokens.toLocaleString()
}

const FIELD_TYPE_LABELS: Record<string, string> = {
  string: '文本',
  number: '数字',
  boolean: '布尔',
  object: '对象',
  array: '数组',
  file_list: '文件'
}

function getTypeLabel(type: string): string {
  return FIELD_TYPE_LABELS[type] || type
}

const paramFormData = reactive<Record<string, unknown>>({})
const filePickerVisible = ref(false)
const currentFileField = ref<string | null>(null)

function getDefaultValue(type: string): unknown {
  if (type === 'number') return 0
  if (type === 'boolean') return false
  if (type === 'file_list') return [] as FileInfo[]
  return ''
}

watch(
  () => props.fields,
  fields => {
    for (const field of fields) {
      if (!(field.name in paramFormData)) {
        paramFormData[field.name] = getDefaultValue(field.type)
      }
    }
  },
  { immediate: true }
)

function resetParams(): void {
  Object.keys(paramFormData).forEach(key => delete paramFormData[key])
  for (const field of props.fields) {
    paramFormData[field.name] = getDefaultValue(field.type)
  }
}

defineExpose({ resetParams })

function isFieldFilled(field: FlowIOField): boolean {
  const value = paramFormData[field.name]
  if (field.type === 'boolean') return value === true
  if (field.type === 'number') return value !== 0 && value != null
  if (field.type === 'file_list') return Array.isArray(value) && value.length > 0
  return typeof value === 'string' && value.trim() !== ''
}

const filledCount = computed(() => props.fields.filter(f => isFieldFilled(f)).length)
const hasFilledParams = computed(() => filledCount.value > 0)

// ---- 必填校验：发送时拦截未填的 required 字段 ----
const paramPopoverVisible = ref(false)
const errorFieldNames = ref(new Set<string>())

/** 单个字段修正后即时清除其错误态（避免已填字段残留红框） */
watch(
  () => props.fields.map(f => isFieldFilled(f)).join(','),
  () => {
    if (errorFieldNames.value.size === 0) return
    const next = new Set<string>()
    for (const name of errorFieldNames.value) {
      const field = props.fields.find(f => f.name === name)
      if (field && !isFieldFilled(field)) next.add(name)
    }
    errorFieldNames.value = next
  }
)

/** 必填校验：返回未填的 required 字段列表 */
function validateRequiredFields(): FlowIOField[] {
  return props.fields.filter(f => f.required && !isFieldFilled(f))
}

function openFilePicker(fieldName: string): void {
  currentFileField.value = fieldName
  filePickerVisible.value = true
}

function handleFilePickerConfirm(files: FileInfo[]): void {
  if (currentFileField.value) {
    paramFormData[currentFileField.value] = files
  }
}

function removeFile(fieldName: string, fileId: number): void {
  const files = paramFormData[fieldName] as FileInfo[]
  paramFormData[fieldName] = files.filter(f => f.id !== fileId)
}

watch(
  () => props.restoreParams,
  params => {
    if (!params) return
    for (const field of props.fields) {
      if (field.name in params) {
        paramFormData[field.name] = params[field.name]
      }
    }
    emit('restore-consumed')
  },
  { immediate: true }
)

function handleSend() {
  if (sendMessageDisabled.value || props.isStreaming) return

  // 必填校验：未填则弹出「参数设置」popover 并红框标记，不发送
  const missing = validateRequiredFields()
  if (missing.length > 0) {
    errorFieldNames.value = new Set(missing.map(f => f.name))
    paramPopoverVisible.value = true
    ElMessage.warning({
      message: `请填写必填参数：${missing.map(f => f.description || f.name).join('、')}`,
      duration: 5000
    })
    return
  }
  errorFieldNames.value = new Set()

  const params: Record<string, unknown> = {}
  const attachedFiles: Array<{ id: number; original_name: string; mime_type: string }> = []

  for (const field of props.fields) {
    const value = paramFormData[field.name]
    if (field.type === 'file_list') {
      const files = value as FileInfo[] | undefined
      if (Array.isArray(files) && files.length > 0) {
        params[field.name] = files.map(f => ({
          id: f.id,
          original_name: f.original_name,
          file_type: f.file_type,
          file_size: f.file_size,
          mime_type: f.mime_type,
          preview_url: '/' + f.file_path,
          file_path: f.file_path
        }))
        attachedFiles.push(
          ...files.map(f => ({
            id: f.id,
            original_name: f.original_name,
            mime_type: f.mime_type
          }))
        )
      }
    } else if (field.type === 'object' || field.type === 'array') {
      if (typeof value === 'string' && value.trim()) {
        try {
          params[field.name] = JSON.parse(value)
        } catch {
          ElMessage.error({
            message: `参数 "${field.name}" 格式错误，请输入有效的JSON`,
            duration: 5000
          })
          return
        }
      } else {
        params[field.name] = field.type === 'object' ? {} : []
      }
    } else {
      params[field.name] = value
    }
  }

  emit('send', params, attachedFiles, inputMessage.value.trim())
  inputMessage.value = ''
  resetParams()
}

function handleKeyDown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

function handleStop() {
  emit('stop')
}
</script>

<template>
  <div class="chat-input-area">
    <div class="chat-input-main">
      <div class="input-box">
        <textarea
          v-model="inputMessage"
          class="input-textarea"
          :placeholder="
            isStreaming
              ? 'AI 输出中…仍可输入，结束后发送 (Enter 发送, Shift+Enter 换行)'
              : '输入消息... (Enter 发送, Shift+Enter 换行)'
          "
          :disabled="isWaitingHuman"
          rows="2"
          @keydown="handleKeyDown"
        ></textarea>
        <div class="input-toolbar">
          <div class="toolbar-left">
            <el-popover
              v-if="fields.length > 0"
              v-model:visible="paramPopoverVisible"
              placement="top-start"
              :width="380"
              trigger="click"
            >
              <template #reference>
                <button class="toolbar-icon-btn" :class="{ active: hasFilledParams }">
                  <el-icon :size="18"><SetUp /></el-icon>
                  <span v-if="hasFilledParams" class="param-dot"></span>
                </button>
              </template>
              <div class="param-popover">
                <div class="param-popover-header">
                  <div class="param-popover-title-group">
                    <span class="param-popover-title">参数设置</span>
                    <el-tag
                      size="small"
                      :type="filledCount === fields.length ? 'success' : 'info'"
                      round
                    >
                      {{ filledCount }}/{{ fields.length }}
                    </el-tag>
                  </div>
                  <el-button size="small" text class="param-reset-btn" @click="resetParams">
                    重置
                  </el-button>
                </div>
                <div class="param-popover-body">
                  <div
                    v-for="field in fields"
                    :key="field.name"
                    class="param-field"
                    :class="{ 'is-error': errorFieldNames.has(field.name) }"
                  >
                    <div
                      class="param-field-header"
                      :class="{ 'is-inline': field.type === 'boolean' }"
                    >
                      <div class="param-field-label-group">
                        <span class="param-field-label">
                          {{ field.description || field.name }}
                          <span v-if="field.required" class="param-required">*</span>
                        </span>
                        <el-tooltip
                          v-if="field.placeholder"
                          :content="field.placeholder"
                          placement="top"
                        >
                          <el-icon class="param-hint-icon"><QuestionFilled /></el-icon>
                        </el-tooltip>
                        <el-tag size="small" class="param-type-tag" effect="plain">
                          {{ getTypeLabel(field.type) }}
                        </el-tag>
                      </div>
                      <el-switch
                        v-if="field.type === 'boolean'"
                        v-model="paramFormData[field.name] as boolean"
                        size="small"
                      />
                      <span v-else class="param-field-name">{{ field.name }}</span>
                    </div>
                    <div v-if="field.type !== 'boolean'" class="param-field-control">
                      <el-input
                        v-if="field.type === 'string'"
                        v-model="paramFormData[field.name] as string"
                        :placeholder="field.placeholder || field.description || '请输入'"
                        size="small"
                      />
                      <el-input-number
                        v-else-if="field.type === 'number'"
                        v-model="paramFormData[field.name] as number"
                        size="small"
                        style="width: 100%"
                      />
                      <el-input
                        v-else-if="field.type === 'object' || field.type === 'array'"
                        v-model="paramFormData[field.name] as string"
                        type="textarea"
                        :rows="2"
                        :placeholder="
                          field.placeholder ||
                          (field.type === 'object' ? '请输入JSON对象' : '请输入JSON数组')
                        "
                        size="small"
                      />
                      <div v-else-if="field.type === 'file_list'" class="file-field">
                        <div
                          v-if="(paramFormData[field.name] as FileInfo[]).length > 0"
                          class="selected-files"
                        >
                          <el-tag
                            v-for="f in paramFormData[field.name] as FileInfo[]"
                            :key="f.id"
                            closable
                            size="small"
                            type="info"
                            @close="removeFile(field.name, f.id)"
                          >
                            <el-icon class="selected-file-icon"><Document /></el-icon>
                            {{ f.original_name }}
                          </el-tag>
                        </div>
                        <el-button
                          size="small"
                          plain
                          class="file-pick-btn"
                          @click="openFilePicker(field.name)"
                        >
                          <el-icon><FolderOpened /></el-icon>
                          <span>
                            {{
                              (paramFormData[field.name] as FileInfo[]).length > 0
                                ? '继续选择'
                                : '选择文件'
                            }}
                          </span>
                        </el-button>
                        <FilePickerDialog
                          v-model="filePickerVisible"
                          :selected-ids="
                            (paramFormData[currentFileField!] as FileInfo[])?.map(f => f.id) || []
                          "
                          :multiple="field.multiple"
                          :max-size="field.max_size"
                          :accept="field.accept"
                          @confirm="handleFilePickerConfirm"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </el-popover>
            <el-tooltip content="计划模式：只读探索与规划，禁用写操作工具" placement="top">
              <button
                class="toolbar-icon-btn"
                :class="{ 'plan-active': planMode }"
                @click="emit('toggle-plan-mode')"
              >
                <el-icon :size="18"><List /></el-icon>
              </button>
            </el-tooltip>
            <el-tooltip
              :content="workDir || '工作目录：未选择（使用 Agent 默认目录）'"
              placement="top"
            >
              <button
                class="toolbar-icon-btn workdir-btn"
                :class="{ active: !!workDir }"
                @click="emit('select-workdir')"
              >
                <el-icon :size="18"><FolderOpened /></el-icon>
                <span v-if="workDir" class="workdir-name">{{ workDirName }}</span>
                <span
                  v-if="workDir"
                  class="workdir-clear"
                  title="清除（回退默认目录）"
                  @click.stop="emit('clear-workdir')"
                >
                  <el-icon :size="13"><CircleClose /></el-icon>
                </span>
              </button>
            </el-tooltip>
            <!-- 模型切换：图标按钮 + 分组弹出菜单（与推理深度同款交互）；
                 reference 插槽内必须是原生元素（挂运行时指令），不能包 el-tooltip 组件。
                 清空回退 Agent 默认模型；仅当前临时模型与默认不同时清除项才显示 -->
            <el-popover
              v-if="showModelSelect"
              v-model:visible="modelPopoverVisible"
              placement="top-start"
              :width="230"
              trigger="click"
            >
              <template #reference>
                <button
                  class="toolbar-icon-btn model-btn"
                  :class="{ active: !!selectedModel }"
                  :disabled="isStreaming || isWaitingHuman"
                >
                  <el-icon :size="18"><Cpu /></el-icon>
                  <span v-if="modelBtnLabel" class="model-btn-label">{{ modelBtnLabel }}</span>
                </button>
              </template>
              <div class="model-menu">
                <div class="model-menu-title">
                  模型
                  <span class="model-menu-title-sub">
                    {{ selectedModel ? '临时覆盖' : `默认：${defaultModelLabel || 'Agent 配置'}` }}
                  </span>
                </div>
                <div
                  v-if="selectedModel"
                  class="model-menu-item model-menu-clear"
                  @click="selectModel('')"
                >
                  <el-icon :size="13"><CircleClose /></el-icon>
                  清除（回退默认模型）
                </div>
                <template v-for="group in modelGroups" :key="group.label">
                  <div class="model-menu-group-label">{{ group.label }}</div>
                  <div
                    v-for="opt in group.options"
                    :key="opt.value"
                    class="model-menu-item"
                    :class="{ selected: selectedModel === opt.value }"
                    @click="selectModel(opt.value)"
                  >
                    <span class="model-menu-name">{{ opt.label }}</span>
                    <span v-if="opt.multimodal" class="model-menu-badge">多模态</span>
                  </div>
                </template>
              </div>
            </el-popover>
            <!-- 推理深度：图标按钮 + 弹出菜单；仅当前模型带声明档位时显示，空=跟随节点。
                 reference 插槽内必须是原生元素（挂运行时指令），不能包 el-tooltip 组件 -->
            <el-popover
              v-if="showReasoningSelect"
              v-model:visible="reasoningPopoverVisible"
              placement="top-start"
              :width="190"
              trigger="click"
            >
              <template #reference>
                <button
                  class="toolbar-icon-btn reasoning-btn"
                  :class="{ active: !!selectedReasoning }"
                  :disabled="isStreaming || isWaitingHuman"
                >
                  <el-icon :size="18"><Lightning /></el-icon>
                  <span v-if="reasoningBtnLabel" class="reasoning-btn-label">{{
                    reasoningBtnLabel
                  }}</span>
                </button>
              </template>
              <div class="reasoning-menu">
                <div class="reasoning-menu-title">
                  推理深度
                  <span class="reasoning-menu-title-sub">空=跟随节点配置</span>
                </div>
                <div
                  class="reasoning-menu-item"
                  :class="{ selected: !selectedReasoning }"
                  @click="selectReasoning('')"
                >
                  跟随节点
                </div>
                <div class="reasoning-menu-divider" />
                <div
                  v-for="v in reasoningOptions!.values"
                  :key="v"
                  class="reasoning-menu-item"
                  :class="{ selected: selectedReasoning === v }"
                  @click="selectReasoning(v)"
                >
                  {{ v === 'off' ? '关闭' : v }}
                </div>
              </div>
            </el-popover>
          </div>
          <div class="toolbar-right">
            <div v-if="totalTokens" class="token-count">
              <span class="token-label">累计</span>
              <span class="token-value">{{ formatTokenCount(totalTokens) }}</span>
              <template v-if="latestPromptTokens">
                <span class="token-sep">·</span>
                <span class="token-label">历史</span>
                <span class="token-value">{{ formatTokenCount(latestPromptTokens) }}</span>
              </template>
              <span class="token-unit">token</span>
            </div>
            <button
              v-if="isStreaming || isStopping"
              class="stop-btn"
              :class="{ disabled: isStopping }"
              :disabled="isStopping"
              @click="handleStop"
            >
              <el-icon :size="16" class="is-loading"><Loading /></el-icon>
              <span class="btn-text">{{ isStopping ? '停止中…' : '停止' }}</span>
            </button>
            <button
              v-else
              :class="['send-btn', { disabled: sendMessageDisabled || isWaitingHuman || isStreaming }]"
              :disabled="sendMessageDisabled || isWaitingHuman || isStreaming"
              @click="handleSend"
            >
              <span class="btn-text">发送</span>
              <el-icon :size="16"><Promotion /></el-icon>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts">
import { SetUp } from '@element-plus/icons-vue'
export default {
  components: { SetUp }
}
</script>

<style scoped>
.chat-input-area {
  max-width: 896px;
  margin: 0 auto;
  width: 100%;
}

.chat-input-main {
  width: 100%;
}

.param-popover {
  max-height: 400px;
  overflow-y: auto;
}

.param-popover-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid #f1f5f9;
  margin-bottom: 10px;
}

.param-popover-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.param-reset-btn {
  color: var(--paper-ink-4);
  font-size: 12px;
  height: auto;
  padding: 2px 6px;
}

.param-reset-btn:hover {
  color: var(--vermilion);
}

.param-popover-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--paper-ink-2);
}

.param-popover-body {
  padding: 0 14px 10px;
}

.param-field {
  padding: 8px 0;
  border-top: 1px solid #f1f5f9;
}

.param-field:first-child {
  border-top: none;
  padding-top: 0;
}

.param-field-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.param-field-header.is-inline {
  margin-bottom: 0;
}

.param-field-label-group {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.param-type-tag {
  transform: scale(0.85);
  transform-origin: left center;
  font-size: 11px;
}

.param-field-label {
  font-size: 13px;
  color: var(--paper-ink-2);
  font-weight: 400;
}

.param-hint-icon {
  color: var(--paper-ink-4);
  cursor: help;
  font-size: 14px;
  flex-shrink: 0;
}

.param-required {
  color: #ef4444;
  margin-left: 2px;
}

.param-field-name {
  font-size: 11px;
  color: var(--paper-ink-4);
  font-family: var(--font-mono);
}

.param-field-control {
  width: 100%;
}

/* 必填校验错误态：label 红 + 输入控件红边框 */
.param-field.is-error .param-field-label {
  color: #ef4444;
  font-weight: 500;
}

.param-field.is-error :deep(.el-input__wrapper),
.param-field.is-error :deep(.el-textarea__inner) {
  box-shadow: 0 0 0 1px #ef4444 inset;
}

.param-field.is-error :deep(.el-input__wrapper.is-focus),
.param-field.is-error :deep(.el-textarea__inner:focus) {
  box-shadow:
    0 0 0 1px #ef4444 inset,
    0 0 0 3px rgba(239, 68, 68, 0.15);
}

.file-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-pick-btn {
  width: fit-content;
}

.selected-files {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.selected-file-icon {
  margin-right: 2px;
  vertical-align: -2px;
}

.input-box {
  background: var(--paper-card);
  border: 1px solid var(--paper-line);
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 4px 16px rgba(60, 50, 35, 0.06);
  transition:
    border-color 0.2s,
    box-shadow 0.2s;
}

.input-box:focus-within {
  border-color: var(--vermilion);
  box-shadow:
    0 0 0 3px rgba(194, 65, 12, 0.07),
    0 4px 16px rgba(60, 50, 35, 0.06);
}

.input-textarea {
  width: 100%;
  min-height: 50px;
  padding: 16px 20px;
  border: none;
  outline: none;
  resize: none;
  font-size: 14px;
  line-height: 1.6;
  color: var(--paper-ink);
  background: transparent;
  font-family: inherit;
}

.input-textarea::placeholder {
  color: var(--paper-ink-5);
}

.input-textarea:disabled {
  background: var(--paper-warm);
  color: var(--paper-ink-4);
}

.input-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 14px;
  background: var(--paper-warm);
  border-top: 1px solid var(--paper-line-soft);
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  /* 空间不足时左侧整体可裁剪（内部各可收缩项先吸收），
     严禁挤压右侧发送按钮 */
  min-width: 0;
  overflow: hidden;
}

.toolbar-icon-btn {
  position: relative;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  border-radius: 8px;
  color: var(--paper-ink-3);
  cursor: pointer;
  transition: all 0.2s;
  /* 图标按钮是功能入口，任何宽度下保持完整 */
  flex-shrink: 0;
}

.toolbar-icon-btn:hover,
.toolbar-icon-btn.active {
  color: var(--vermilion);
  background: var(--paper-card);
}

.toolbar-icon-btn.active:hover {
  background: var(--vermilion-soft);
}

.toolbar-icon-btn.plan-active {
  color: var(--vermilion);
  background: var(--paper-card);
}

.toolbar-icon-btn.plan-active:hover {
  background: var(--vermilion-soft);
}

/* 工具栏按钮统一禁用态：灰化并压制 active/hover 高亮（流式/等待人工输入期间
   模型与推理深度按钮禁用，橙色激活样式会误导用户以为仍可操作） */
.toolbar-icon-btn:disabled,
.toolbar-icon-btn:disabled:hover {
  color: var(--paper-ink-4);
  background: transparent;
  cursor: not-allowed;
}

/* 工作目录按钮：选中时横向展示 目录名 + 清除按钮；空间不足时优先收缩 */
.workdir-btn {
  width: auto;
  min-width: 32px;
  max-width: 220px;
  padding: 0 8px;
  gap: 4px;
  flex-shrink: 1;
  overflow: hidden;
}

.workdir-name {
  max-width: 140px;
  overflow: hidden;
  color: inherit;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workdir-clear {
  display: flex;
  align-items: center;
  color: var(--paper-ink-4);
  border-radius: 50%;
  transition: color 0.15s;
}

.workdir-clear:hover {
  color: var(--vermilion);
}

.param-dot {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 8px;
  height: 8px;
  background: var(--vermilion);
  border-radius: 50%;
  border: 1.5px solid var(--paper-warm);
}

/* 模型按钮：与推理深度按钮同形态，[图标+模型名] 徽标 */
.model-btn {
  width: auto;
  min-width: 32px;
  max-width: 150px;
  padding: 0 8px;
  gap: 4px;
  flex-shrink: 1;
  overflow: hidden;
  cursor: pointer;
}

.model-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

/* 推理深度按钮禁用态与模型按钮一致 */
.reasoning-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.model-btn-label {
  max-width: 110px;
  overflow: hidden;
  color: inherit;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 模型弹出菜单：标题行 + 清除项 + 分组列表 */
.model-menu {
  display: flex;
  flex-direction: column;
  max-height: 320px;
  overflow-y: auto;
}

.model-menu-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 10px 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--paper-ink);
}

.model-menu-title-sub {
  font-size: 10px;
  font-weight: 400;
  color: var(--paper-ink-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-menu-group-label {
  padding: 6px 10px 3px;
  font-size: 10px;
  font-weight: 600;
  color: var(--paper-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.model-menu-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  font-size: 13px;
  color: var(--paper-ink-2);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}

.model-menu-item:hover {
  background: var(--paper-warm);
  color: var(--paper-ink);
}

.model-menu-item.selected {
  background: var(--vermilion-soft);
  color: var(--vermilion);
  font-weight: 600;
}

.model-menu-clear {
  color: var(--paper-ink-3);
  font-size: 12px;
}

.model-menu-clear:hover {
  color: var(--vermilion);
}

.model-menu-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-menu-badge {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--vermilion);
  background: var(--vermilion-soft);
  border-radius: 4px;
  padding: 0 5px;
}

/* 推理深度按钮：对标 workdir-btn 的 [图标+文字] 形态，未选档位时纯图标 */
.reasoning-btn {
  width: auto;
  min-width: 32px;
  max-width: 96px;
  padding: 0 8px;
  gap: 4px;
  flex-shrink: 1;
  overflow: hidden;
}

.reasoning-btn-label {
  max-width: 56px;
  overflow: hidden;
  color: inherit;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 弹出菜单：标题行 + 跟随节点/关闭/档位列表，当前项朱砂高亮 */
.reasoning-menu {
  display: flex;
  flex-direction: column;
}

.reasoning-menu-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 10px 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--paper-ink);
}

.reasoning-menu-title-sub {
  font-size: 10px;
  font-weight: 400;
  color: var(--paper-ink-4);
}

.reasoning-menu-item {
  padding: 7px 10px;
  font-size: 13px;
  color: var(--paper-ink-2);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}

.reasoning-menu-item:hover {
  background: var(--paper-warm);
  color: var(--paper-ink);
}

.reasoning-menu-item.selected {
  background: var(--vermilion-soft);
  color: var(--vermilion);
  font-weight: 600;
}

.reasoning-menu-divider {
  height: 1px;
  margin: 4px 6px;
  background: var(--paper-line-soft);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  /* 发送/停止按钮与 token 计数绝不因左侧过宽而被挤压竖排 */
  flex-shrink: 0;
}

.token-count {
  display: flex;
  align-items: baseline;
  margin-right: 4px;
  white-space: nowrap;
}

.token-label {
  font-size: 10px;
  font-weight: 500;
  color: var(--paper-ink-5);
  margin-right: 2px;
}

.token-value {
  font-size: 11px;
  font-weight: 600;
  color: var(--paper-ink-2);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  min-width: 3ch;
  text-align: right;
}

.token-sep {
  font-size: 11px;
  color: var(--paper-ink-5);
  margin: 0 4px;
}

.token-unit {
  font-size: 10px;
  color: var(--paper-ink-5);
  margin-left: 2px;
}

.send-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px;
  background: var(--paper-ink);
  color: var(--paper);
  border: none;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(60, 50, 35, 0.18);
  transition: all 0.2s;
  /* CJK 逐字断行是按钮被挤成竖排的根因，任何断点下都不允许 */
  white-space: nowrap;
  flex-shrink: 0;
}

.send-btn:hover:not(.disabled) {
  background: var(--paper-ink-2);
}

.send-btn:active:not(.disabled) {
  transform: scale(0.97);
}

.send-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.stop-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px;
  background: #ef4444;
  color: #fff;
  border: none;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.25);
  transition: all 0.2s;
}

.stop-btn:hover {
  background: #dc2626;
}

@media (max-width: 768px) {
  .token-count {
    display: none;
  }

  /* 推理深度按钮收成纯图标（档位经弹出菜单仍可选，当前档位见菜单高亮项） */
  .reasoning-btn {
    max-width: 32px;
    padding: 0;
  }

  .reasoning-btn-label {
    display: none;
  }

  .send-btn,
  .stop-btn {
    padding: 8px 12px;
    gap: 0;
    /* 空间不足时禁止按钮内文字换行（CJK 可逐字断行导致竖排） */
    white-space: nowrap;
    flex-shrink: 0;
  }

  /* 发送/停止收成纯图标按钮，为左侧工具栏腾出空间 */
  .send-btn .btn-text,
  .stop-btn .btn-text {
    display: none;
  }

  /* 工作目录按钮收成纯图标：目录名与清除小图标隐藏（tooltip 仍有全路径，
     清除操作走弹窗内"清除选择"），为右侧发送按钮腾出空间 */
  .workdir-btn {
    max-width: 32px;
    padding: 0;
  }

  .workdir-name,
  .workdir-clear {
    display: none;
  }

  /* 模型按钮徽标隐藏收成纯图标（tooltip 层面已有标题说明当前模型） */
  .model-btn-label {
    display: none;
  }
}
</style>
