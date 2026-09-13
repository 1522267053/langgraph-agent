<script setup lang="ts">
/**
 * Python 节点试运行面板（精简版）
 *
 * 仅负责「输入参数填表 + 运行 + 结果展示」，代码编辑交由 CodeEditor 全屏弹窗左栏承担。
 * 嵌入位置：PythonConfig.vue 内 CodeEditor 全屏弹窗的 slot="debug"。
 *
 * 与流程中真实执行共享 RestrictedPython 沙箱核心逻辑（_run_python_in_sandbox），
 * 但隔离副作用：__save_file__ 落盘到 workspace/temp/debug_uploads/<session_id>/，
 * 不写 File DB、不调 record_tool_file_change。
 *
 * 输入变量填表为可选——未填的参数会自动用 default_value 兜底（与流程中生产行为一致）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { CaretRight, Refresh } from '@element-plus/icons-vue'
import { debugApi, type PythonDebugResult } from '@/api/debug'
import { fieldTypeOptions } from './types'
import type { PythonParam } from './types'

const props = withDefaults(
  defineProps<{
    /**
     * 当前节点代码（来自 PythonConfig.localConfig.code）。
     * 注意：本面板不显示代码区（编辑由 CodeEditor 全屏弹窗左栏承担），但运行时要传给后端。
     */
    code: string
    /** 超时时间（秒），仅作展示与运行时使用；修改请改 PythonConfig.localConfig.timeout */
    timeout: number
    /**
     * 输入变量定义（来自 PythonConfig.input_variables）。
     * 未填参数会用 default_value 兜底（与流程中生产行为一致）。
     */
    inputVariables: PythonParam[]
    /** true: 整面板禁用（use_preset_for_tool=true 时不允许试运行） */
    readonlyCode?: boolean
  }>(),
  { readonlyCode: false }
)

// ---- 输入变量临时值（用户可覆盖 default_value；不写回 config） ----
const overrides = reactive<Record<string, unknown>>({})

/** 初始化 overrides：用 default_value 预填，与流程中生产路径行为一致 */
function syncOverridesFromDefaults(): void {
  for (const v of props.inputVariables) {
    if (!v.name) continue
    if (!(v.name in overrides)) {
      overrides[v.name] = v.default_value ?? null
    }
  }
}
watch(() => props.inputVariables, syncOverridesFromDefaults, {
  immediate: true,
  deep: true
})

/** 必填校验：required=true 且未提供值（覆盖值或默认值）时不可运行 */
const requiredMissing = computed(() => {
  return props.inputVariables.filter(v => {
    if (!v.required || !v.name) return false
    const val = overrides[v.name]
    if (val === null || val === undefined || val === '') return true
    return false
  })
})

const canRun = computed(() => {
  return !props.readonlyCode && requiredMissing.value.length === 0 && !running.value
})

// ---- 运行时状态 ----
const running = ref(false)
const result = ref<PythonDebugResult | null>(null)
const errorMsg = ref('')

/** 收集 overrides 为 input_data（过滤空名参数） */
function collectInputData(): Record<string, unknown> {
  const data: Record<string, unknown> = {}
  for (const v of props.inputVariables) {
    if (!v.name) continue
    data[v.name] = overrides[v.name] ?? null
  }
  return data
}

async function handleRun(): Promise<void> {
  if (!canRun.value) return
  running.value = true
  errorMsg.value = ''
  result.value = null
  try {
    const res = await debugApi.runPython({
      code: props.code,
      timeout: props.timeout,
      input_data: collectInputData()
    })
    if (res.data.code === 1 && res.data.data) {
      result.value = res.data.data
    } else {
      errorMsg.value = res.data.msg || '试运行失败'
    }
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '试运行失败'
  } finally {
    running.value = false
  }
}

function handleReset(): void {
  for (const v of props.inputVariables) {
    if (v.name) overrides[v.name] = v.default_value ?? null
  }
  result.value = null
  errorMsg.value = ''
}

/** 类型标签映射（与 PythonConfig.vue 一致） */
function getTypeLabel(type: string): string {
  const opt = fieldTypeOptions.find(o => o.value === type)
  return opt?.label || type
}

/** 把整个试运行结果序列化为 JSON 字符串 */
function formatJsonOutput(value: unknown): string {
  if (value === null || value === undefined) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** 判断 result 是否含 __save_file__ 文件信息（前端展示预览） */
interface SaveFileInfo {
  preview_url: string
  file_name: string
  mime_type: string
}

interface SaveFileResult {
  __save_file__: true
  __file_info__?: SaveFileInfo
  __file_error__?: string
  __raw__?: unknown
}

function isSaveFileResult(value: unknown): value is SaveFileResult {
  return (
    typeof value === 'object' &&
    value !== null &&
    '__save_file__' in value &&
    (value as SaveFileResult).__save_file__ === true
  )
}

function isImageMime(mime: string): boolean {
  return mime.startsWith('image/')
}
</script>

<template>
  <div class="python-debug-panel">
    <div class="debug-header">
      <span class="debug-title">试运行（独立调试，不影响实际流程）</span>
      <div class="debug-actions">
        <el-button size="small" :disabled="running" @click="handleReset">
          <el-icon><Refresh /></el-icon>
          <span style="margin-left: 4px">重置</span>
        </el-button>
        <el-button
          type="primary"
          size="small"
          :disabled="!canRun"
          :loading="running"
          @click="handleRun"
        >
          <el-icon><CaretRight /></el-icon>
          <span style="margin-left: 4px">{{ running ? '运行中…' : '运行' }}</span>
        </el-button>
      </div>
    </div>

    <!-- 工具模式（use_preset_for_tool）下整面板禁用，避免误运行预设代码外的逻辑 -->
    <div v-if="readonlyCode" class="readonly-hint">
      <el-text size="small" type="warning">
        使用预设代码模式：试运行已禁用（仅作为 LLM 工具暴露，参数固定由 LLM 填入）
      </el-text>
    </div>

    <!-- 输入变量填表 -->
    <div class="debug-input-vars">
      <div v-if="inputVariables.length === 0" class="no-input-hint">
        <el-text size="small" type="info">无入参，点击「运行」直接试运行</el-text>
      </div>
      <div v-else class="debug-fields">
        <div v-for="v in inputVariables" :key="v.name" class="debug-field">
          <div class="field-label-row">
            <span class="field-name">
              {{ v.name }}
              <span v-if="v.required" class="required-mark">*</span>
            </span>
            <el-tag size="small" effect="plain" type="info">{{ getTypeLabel(v.type || 'string') }}</el-tag>
            <el-text v-if="v.description" size="small" type="info" class="field-desc">
              {{ v.description }}
            </el-text>
          </div>
          <el-input
            v-if="v.type === 'string' || !v.type"
            v-model="overrides[v.name!] as string"
            :placeholder="v.default_value != null ? `默认：${v.default_value}` : '字符串'"
            size="small"
            :disabled="readonlyCode"
          />
          <el-input-number
            v-else-if="v.type === 'number'"
            v-model="overrides[v.name!] as number"
            :placeholder="v.default_value != null ? `默认：${v.default_value}` : '数字'"
            size="small"
            style="width: 100%"
            :disabled="readonlyCode"
          />
          <el-select
            v-else-if="v.type === 'boolean'"
            v-model="overrides[v.name!] as boolean"
            :placeholder="v.default_value != null ? `默认：${v.default_value}` : '布尔'"
            size="small"
            clearable
            :disabled="readonlyCode"
          >
            <el-option label="true" :value="true" />
            <el-option label="false" :value="false" />
          </el-select>
          <el-input
            v-else
            v-model="overrides[v.name!] as string"
            :placeholder="v.default_value != null ? `默认：${JSON.stringify(v.default_value)}` : 'JSON 对象/数组'"
            size="small"
            type="textarea"
            :rows="2"
            :disabled="readonlyCode"
          />
        </div>
      </div>
      <div v-if="requiredMissing.length > 0" class="required-warning">
        <el-text size="small" type="warning">
          必填参数未填：{{ requiredMissing.map(v => v.name).join('、') }}
        </el-text>
      </div>
    </div>

    <!-- 错误提示 -->
    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="error"
      :closable="false"
      show-icon
      class="debug-alert"
    />

    <!-- 结果展示 -->
    <div v-if="result" class="debug-result">
      <div class="result-status">
        <el-tag :type="result.success ? 'success' : 'danger'" size="small">
          {{ result.success ? '执行成功' : '执行失败' }}
        </el-tag>
        <!-- __save_file__ 文件预览（仅调试面板：文件落在 /debug-uploads/，7 天清理） -->
        <div
          v-if="result.success && isSaveFileResult(result.result) && result.result.__file_info__"
          class="save-file-preview"
        >
          <el-image
            v-if="isImageMime(result.result.__file_info__.mime_type)"
            :src="result.result.__file_info__.preview_url"
            :alt="result.result.__file_info__.file_name"
            fit="contain"
            style="max-width: 240px; max-height: 180px"
            :preview-src-list="[result.result.__file_info__.preview_url]"
          />
          <a
            v-else
            :href="result.result.__file_info__.preview_url"
            target="_blank"
            class="file-link"
          >
            {{ result.result.__file_info__.file_name }}
          </a>
          <el-text size="small" type="info" class="save-file-hint">
            文件已生成（调试临时，7 天后清理）
          </el-text>
        </div>
        <div
          v-else-if="result.success && isSaveFileResult(result.result) && result.result.__file_error__"
          class="save-file-error"
        >
          <el-text size="small" type="danger">
            {{ result.result.__file_error__ }}
          </el-text>
        </div>
      </div>
      <pre class="result-pre">{{ formatJsonOutput(result) }}</pre>
    </div>
  </div>
</template>

<style scoped>
.python-debug-panel {
  border: 1px dashed #dcdfe6;
  border-radius: 6px;
  padding: 12px;
  background: #fafbfc;
}

.debug-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.debug-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.debug-actions {
  display: flex;
  gap: 8px;
}

.readonly-hint {
  margin-bottom: 8px;
  padding: 6px 10px;
  background: #fdf6ec;
  border-radius: 4px;
}

.debug-input-vars {
  margin-top: 10px;
}

.no-input-hint {
  padding: 8px 0;
}

.debug-fields {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.debug-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-label-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.field-name {
  font-size: 12px;
  font-weight: 600;
  color: #303133;
  font-family: 'Courier New', monospace;
}

.required-mark {
  color: #f56c6c;
  margin-left: 2px;
}

.field-desc {
  font-size: 11px;
  color: #909399;
}

.required-warning {
  margin-top: 8px;
}

.debug-alert {
  margin-top: 10px;
}

.debug-result {
  margin-top: 12px;
}

.result-status {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.save-file-preview {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.file-link {
  color: #2563eb;
  text-decoration: none;
}

.file-link:hover {
  text-decoration: underline;
}

.save-file-hint {
  font-size: 11px;
  color: #909399;
}

.save-file-error {
  margin-top: 4px;
}

.result-pre {
  margin: 0;
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: #1e293b;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 360px;
  overflow: auto;
  font-family: 'Courier New', monospace;
}
</style>
