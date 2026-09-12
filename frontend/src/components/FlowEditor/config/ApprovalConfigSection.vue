<script setup lang="ts">
/**
 * 工具审批配置公共组件（已下沉到工具节点内部）
 *
 * 供 Shell/Ssh/Python/Api/Mcp 节点复用，包含：
 *  - 需审批工具名（多选下拉，可手动输入）
 *  - 命令/内容危险模式（Python re 语法正则列表）
 *
 * 字段语义与后端 BaseNodeConfig.approval_required_tools / approval_required_patterns 对齐。
 */
import { ref, watch } from 'vue'

export interface ApprovalConfig {
  approval_required_tools: string[]
  approval_required_patterns: string[]
}

const props = defineProps<{
  modelValue: ApprovalConfig
  /** 预置工具名选项（如 shell_executor / api_call_tool_<key> 等） */
  availableTools?: { label: string; value: string }[]
  /** 配置区下方提示文案 */
  hint?: string
  /** 工具名下拉占位文案 */
  toolsPlaceholder?: string
  /** 正则模式占位文案 */
  patternPlaceholder?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: ApprovalConfig): void
}>()

/** 内部副本：避免直接改 props（与项目其他 ConfigSection 写法一致） */
const local = ref<ApprovalConfig>({
  approval_required_tools: Array.isArray(props.modelValue.approval_required_tools)
    ? [...props.modelValue.approval_required_tools]
    : [],
  approval_required_patterns: Array.isArray(props.modelValue.approval_required_patterns)
    ? [...props.modelValue.approval_required_patterns]
    : []
})

watch(
  () => props.modelValue,
  val => {
    local.value = {
      approval_required_tools: Array.isArray(val.approval_required_tools)
        ? [...val.approval_required_tools]
        : [],
      approval_required_patterns: Array.isArray(val.approval_required_patterns)
        ? [...val.approval_required_patterns]
        : []
    }
  },
  { deep: true }
)

function commit(): void {
  emit('update:modelValue', {
    approval_required_tools: [...local.value.approval_required_tools],
    approval_required_patterns: [...local.value.approval_required_patterns]
  })
}

function addPattern(): void {
  local.value.approval_required_patterns.push('')
  commit()
}

function removePattern(index: number): void {
  local.value.approval_required_patterns.splice(index, 1)
  commit()
}

/** 工具名去空白 + 去重（多选下拉允许 create 容易产生 "shell_executor " "shell_executor" 双份） */
function normalizeTools(): void {
  local.value.approval_required_tools = [
    ...new Set(
      (local.value.approval_required_tools || [])
        .map(s => (typeof s === 'string' ? s.trim() : ''))
        .filter(Boolean)
    )
  ]
  commit()
}
</script>

<template>
  <div class="config-section">
    <div class="section-title">
      <span>工具审批（仅 Agent 模式生效）</span>
    </div>
    <el-form label-width="100px" size="small">
      <el-form-item label="需审批工具名">
        <el-select
          v-model="local.approval_required_tools"
          multiple
          filterable
          allow-create
          default-first-option
          :placeholder="toolsPlaceholder || '留空表示不过审批；可填完整工具名'"
          style="width: 100%"
          @change="normalizeTools"
        >
          <el-option
            v-for="opt in availableTools || []"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="危险内容模式">
        <div class="pattern-list">
          <div
            v-for="(_, idx) in local.approval_required_patterns"
            :key="idx"
            class="pattern-row"
          >
            <el-input
              v-model="local.approval_required_patterns[idx]"
              :placeholder="patternPlaceholder || '正则表达式（Python re 语法，逐条 re.search(content, ...)）'"
              @blur="commit"
            />
            <el-button
              type="danger"
              size="small"
              link
              @click="removePattern(idx)"
            >
              删除
            </el-button>
          </div>
          <el-button
            type="primary"
            size="small"
            link
            @click="addPattern"
          >
            + 添加模式
          </el-button>
        </div>
      </el-form-item>
    </el-form>
    <div v-if="hint" class="config-hint">
      <el-text size="small" type="info">{{ hint }}</el-text>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';

.pattern-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.pattern-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pattern-row :deep(.el-input) {
  flex: 1;
}
</style>