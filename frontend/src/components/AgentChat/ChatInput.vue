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
  List
} from '@element-plus/icons-vue'
import FilePickerDialog from '@/components/common/FilePickerDialog.vue'

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
  /** 可选模型列表（当前 Agent 供应商下的模型；为空时不展示下拉框） */
  modelOptions?: Array<{ value: string; label: string; multimodal?: boolean }>
  /** Agent 配置的默认模型名（仅展示用） */
  defaultModelLabel?: string
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
const sendMessageDisabled = computed(() => !inputMessage.value.trim())

const showModelSelect = computed(() => (props.modelOptions?.length ?? 0) > 0)

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
          placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
          :disabled="isStreaming || isWaitingHuman"
          rows="2"
          @keydown="handleKeyDown"
        ></textarea>
        <div class="input-toolbar">
          <div class="toolbar-left">
            <el-popover v-if="fields.length > 0" placement="top-start" :width="380" trigger="click">
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
                  <div v-for="field in fields" :key="field.name" class="param-field">
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
            <!-- 模型切换：仅同供应商内覆盖，清空即回退 Agent 默认模型 -->
            <el-tooltip
              v-if="showModelSelect"
              :content="
                selectedModel
                  ? `临时模型：${selectedModel}（清除后回退默认）`
                  : `默认模型：${defaultModelLabel || 'Agent 配置'}`
              "
              placement="top"
            >
              <el-select
                v-model="selectedModel"
                class="model-select"
                size="small"
                filterable
                clearable
                :disabled="isStreaming || isWaitingHuman"
                placeholder="默认模型"
                no-data-text="暂无可用模型"
              >
                <el-option
                  v-for="opt in modelOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                >
                  <div class="model-option">
                    <span class="model-option-name">{{ opt.label }}</span>
                    <span v-if="opt.multimodal" class="model-option-badge">多模态</span>
                  </div>
                </el-option>
              </el-select>
            </el-tooltip>
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
              <span>{{ isStopping ? '停止中…' : '停止' }}</span>
            </button>
            <button
              v-else
              :class="['send-btn', { disabled: sendMessageDisabled || isWaitingHuman }]"
              :disabled="sendMessageDisabled || isWaitingHuman"
              @click="handleSend"
            >
              <span>发送</span>
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
  color: #94a3b8;
  font-size: 12px;
  height: auto;
  padding: 2px 6px;
}

.param-reset-btn:hover {
  color: #2563eb;
}

.param-popover-title {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
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
  color: #334155;
  font-weight: 400;
}

.param-hint-icon {
  color: #909399;
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
  color: #94a3b8;
  font-family: 'Courier New', monospace;
}

.param-field-control {
  width: 100%;
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
  background: #fff;
  border: 2px solid #e2e8f0;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 4px 16px rgba(226, 232, 240, 0.3);
  transition:
    border-color 0.2s,
    box-shadow 0.2s;
}

.input-box:focus-within {
  border-color: #2563eb;
  box-shadow: 0 4px 16px rgba(37, 99, 235, 0.12);
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
  color: #1e293b;
  background: transparent;
  font-family: inherit;
}

.input-textarea::placeholder {
  color: #94a3b8;
}

.input-textarea:disabled {
  background: #f8fafc;
  color: #94a3b8;
}

.input-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: #f8fafc;
  border-top: 1px solid #f1f5f9;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
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
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.2s;
}

.toolbar-icon-btn:hover,
.toolbar-icon-btn.active {
  color: #2563eb;
  background: #fff;
}

.toolbar-icon-btn.active:hover {
  background: #eff6ff;
}

.toolbar-icon-btn.plan-active {
  color: #ea580c;
  background: #fff;
}

.toolbar-icon-btn.plan-active:hover {
  background: #fff7ed;
}

/* 工作目录按钮：选中时横向展示 目录名 + 清除按钮 */
.workdir-btn {
  width: auto;
  min-width: 32px;
  max-width: 220px;
  padding: 0 8px;
  gap: 4px;
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
  color: #94a3b8;
  border-radius: 50%;
  transition: color 0.15s;
}

.workdir-clear:hover {
  color: #ef4444;
}

.param-dot {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 8px;
  height: 8px;
  background: #2563eb;
  border-radius: 50%;
  border: 1.5px solid #f8fafc;
}

/* 模型下拉框：与工具栏图标按钮高度对齐 */
.model-select {
  width: 130px;
}

.model-select :deep(.el-select__wrapper) {
  min-height: 32px;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 0 0 1px #e2e8f0 inset;
}

.model-select :deep(.el-select__wrapper.is-hovering),
.model-select :deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px #2563eb inset;
}

.model-select :deep(.el-select__placeholder) {
  font-size: 12px;
}

.model-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.model-option-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-option-badge {
  flex-shrink: 0;
  font-size: 10px;
  line-height: 16px;
  color: #2563eb;
  background: #eff6ff;
  border-radius: 4px;
  padding: 0 5px;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
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
  color: #94a3b8;
  margin-right: 2px;
}

.token-value {
  font-size: 11px;
  font-weight: 600;
  color: #334155;
  font-family: 'Courier New', monospace;
  font-variant-numeric: tabular-nums;
  min-width: 3ch;
  text-align: right;
}

.token-sep {
  font-size: 11px;
  color: #cbd5e1;
  margin: 0 4px;
}

.token-unit {
  font-size: 10px;
  color: #94a3b8;
  margin-left: 2px;
}

.send-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px;
  background: #2563eb;
  color: #fff;
  border: none;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25);
  transition: all 0.2s;
}

.send-btn:hover:not(.disabled) {
  background: #1d4ed8;
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

  .send-btn,
  .stop-btn {
    padding: 8px 14px;
  }
}
</style>
