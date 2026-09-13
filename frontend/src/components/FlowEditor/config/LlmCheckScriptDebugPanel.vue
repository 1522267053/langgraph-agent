<script setup lang="ts">
/**
 * LLM 节点「必需工具检查脚本」试运行面板
 *
 * 脚本签名：def main(called_tools, last_result): return {"need_retry": bool, "hint": str}
 * 后端 _run_python_in_sandbox 不依赖流程引擎，直接执行脚本。
 *
 * 与流程中真实执行共享 RestrictedPython 沙箱核心（tool_check_script 走同一 Python 沙箱）。
 */
import { computed, ref } from 'vue'
import { CaretRight, Refresh } from '@element-plus/icons-vue'
import { debugApi, type PythonDebugResult } from '@/api/debug'
import type { ConnectedToolInfo } from '@/api/flow'

const props = defineProps<{
  script: string
  availableTools: ConnectedToolInfo[]
}>()

/** called_tools：用户选择/输入的工具名列表 */
const calledTools = ref<string[]>([])

/** last_result：LLM 最后一轮输出的文本 */
const lastResult = ref('')

/** 合并 availableTools 与 calledTools（去重），作为下拉候选项 */
const toolOptions = computed(() => {
  const set = new Set<string>()
  for (const group of props.availableTools) {
    for (const tool of group.tools) {
      if (tool.name) set.add(tool.name)
    }
  }
  // 保留已填的工具名（即使不在 availableTools 中也保留）
  for (const t of calledTools.value) set.add(t)
  return Array.from(set).map(name => ({ label: name, value: name }))
})

const canRun = computed(() => {
  return props.script.trim() !== '' && !running.value
})

const running = ref(false)
const result = ref<PythonDebugResult | null>(null)
const errorMsg = ref('')

interface CheckResult {
  need_retry?: boolean
  hint?: string
}

function parseCheckResult(value: unknown): CheckResult | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as CheckResult
  }
  return null
}

const checkResult = computed<CheckResult | null>(() => {
  if (!result.value || !result.value.success) return null
  return parseCheckResult(result.value.result)
})

async function handleRun(): Promise<void> {
  if (!canRun.value) return
  running.value = true
  errorMsg.value = ''
  result.value = null
  try {
    const res = await debugApi.runPython({
      code: props.script,
      timeout: 30,
      input_data: {
        called_tools: calledTools.value,
        last_result: lastResult.value
      }
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
  calledTools.value = []
  lastResult.value = ''
  result.value = null
  errorMsg.value = ''
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
</script>

<template>
  <div class="check-script-debug-panel">
    <div class="debug-header">
      <span class="debug-title">试运行（独立调试，模拟本轮工具调用结果）</span>
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

    <div class="debug-inputs">
      <div class="input-row">
        <span class="input-label">called_tools</span>
        <el-select
          v-model="calledTools"
          multiple
          filterable
          allow-create
          default-first-option
          placeholder="从已连接工具选择或手动输入"
          size="small"
          style="flex: 1"
        >
          <el-option-group
            v-for="group in availableTools"
            :key="group.node_key"
            :label="group.node_label"
          >
            <el-option
              v-for="tool in group.tools"
              :key="tool.name"
              :label="tool.name"
              :value="tool.name"
            />
          </el-option-group>
          <!-- fallback：手动输入但不在 availableTools 中的工具也展示 -->
          <el-option
            v-for="opt in toolOptions.filter(
              o => !availableTools.some(g => g.tools.some(t => t.name === o.value))
            )"
            :key="'manual-' + opt.value"
            :label="opt.label + ' (手动)'"
            :value="opt.value"
          />
        </el-select>
      </div>
      <div class="input-row">
        <span class="input-label">last_result</span>
        <el-input
          v-model="lastResult"
          type="textarea"
          :rows="3"
          placeholder="LLM 最后一次输出文本"
          size="small"
        />
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
        <div v-if="result.success && checkResult" class="need-retry-block">
          <el-tag
            :type="checkResult.need_retry ? 'warning' : 'success'"
            size="default"
            effect="dark"
          >
            {{ checkResult.need_retry ? '需重试' : '不需重试' }}
          </el-tag>
          <el-alert
            v-if="checkResult.hint"
            :title="checkResult.hint"
            :type="checkResult.need_retry ? 'warning' : 'success'"
            :closable="false"
            show-icon
            class="hint-alert"
          />
          <el-text v-else size="small" type="info">脚本未返回 hint 字段</el-text>
        </div>
      </div>
      <pre class="result-pre">{{ formatJsonOutput(result) }}</pre>
    </div>
  </div>
</template>

<style scoped>
.check-script-debug-panel {
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
  flex-wrap: wrap;
  gap: 8px;
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

.debug-inputs {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.input-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.input-label {
  font-size: 12px;
  color: #606266;
  font-family: 'Courier New', monospace;
  min-width: 100px;
  padding-top: 6px;
  flex-shrink: 0;
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

.need-retry-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 200px;
}

.hint-alert {
  margin-top: 0;
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