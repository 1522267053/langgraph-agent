<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import type { FieldType } from '@/types/flow'
import {
  useAvailableVariables,
  formatVariablePath,
  parseVariablePath,
  getVariableTypeByPath
} from '@/composables/useAvailableVariables'
import { useFlowStore } from '@/stores/flowStore'
import { VariablePrefix } from '@/constants/variable'

const props = defineProps<{
  modelValue: string
  currentNodeId: string
  placeholder?: string
  disabled?: boolean
  outputMode?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'update:type', type: FieldType | undefined): void
}>()

const flowStore = useFlowStore()
const { variableOptions, upstreamNodes } = useAvailableVariables(props.currentNodeId, {
  allNodes: props.outputMode
})

const subVar = ref('')

const selectedPath = computed(() => {
  if (!props.modelValue) return []
  let path = parseVariablePath(props.modelValue)
  // 子视图中 nodes.<loopKey>.input_xxx 反向显示为 input.xxx
  if (
    flowStore.isInSubView &&
    flowStore.subViewParentId &&
    path[0] === VariablePrefix.NODES &&
    path[1] === flowStore.subViewParentId &&
    path[2]?.startsWith('input_')
  ) {
    const field = path[2].slice(6)
    path = [VariablePrefix.INPUT, field, ...path.slice(3)]
  }
  return path.slice(0, 3)
})

watch(
  () => props.modelValue,
  val => {
    if (!val) {
      subVar.value = ''
      return
    }
    const path = parseVariablePath(val)
    if (path.length > selectedPath.value.length) {
      subVar.value = path.slice(selectedPath.value.length).join('.')
    } else {
      subVar.value = ''
    }
  },
  { immediate: true }
)

const cascaderKey = computed(() => {
  const nodeLabels = upstreamNodes.value
    .filter(n => !['start', 'condition', 'end'].includes(n.type || ''))
    .map(n => `${n.id}:${n.data?.label}`)
    .join(';')
  const inputFields = flowStore.flowInfo?.input_schema?.fields?.map(f => f.name).join(';') || ''
  const subViewKey = flowStore.isInSubView ? `sub:${flowStore.subViewParentId}` : 'main'
  return `${props.currentNodeId}|${subViewKey}|${nodeLabels}|${inputFields}`
})

const resolvedPath = computed(() => {
  const base = formatVariablePath(selectedPath.value)
  return subVar.value ? `${base}.${subVar.value}` : base
})

const resolvedType = computed<FieldType | undefined>(() => {
  if (!props.modelValue) return undefined
  const base = formatVariablePath(selectedPath.value)
  return getVariableTypeByPath(
    base,
    props.currentNodeId,
    flowStore.nodes,
    flowStore.edges,
    flowStore.flowInfo?.input_schema || null,
    flowStore.flowInfo?.flow_type
  )
})

const fieldTypeLabel = computed(() => {
  if (!resolvedType.value) return ''
  const item = fieldTypeLabelMap[resolvedType.value]
  return item || resolvedType.value
})

const typeTooltip = computed(() => {
  if (!resolvedType.value) return ''
  const label = fieldTypeLabel.value
  const fields = typeFieldMap[resolvedType.value]
  if (fields) {
    const lines = fields.map(f => `  ${f.name} (${f.type}) — ${f.desc}`)
    return `变量类型: ${label}\n可用字段:\n${lines.join('\n')}`
  }
  return `变量类型: ${label}`
})

const fieldTypeLabelMap: Record<string, string> = {
  string: '字符串',
  number: '数字',
  boolean: '布尔',
  object: '对象',
  array: '数组',
  file_list: '文件列表',
  python_result: 'Python执行结果'
}

const typeFieldMap: Record<string, Array<{ name: string; type: string; desc: string }>> = {
  python_result: [
    { name: 'stdout', type: 'string', desc: '标准输出' },
    { name: 'stderr', type: 'string', desc: '标准错误' },
    { name: 'result', type: 'any', desc: '函数返回值' },
    { name: 'success', type: 'boolean', desc: '是否执行成功' }
  ],
  file_list: [
    { name: 'id', type: 'number', desc: '文件ID' },
    { name: 'original_name', type: 'string', desc: '原始文件名' },
    { name: 'file_path', type: 'string', desc: '存储路径' },
    { name: 'mime_type', type: 'string', desc: 'MIME类型' },
    { name: 'file_size', type: 'number', desc: '文件大小(字节)' },
    { name: 'file_type', type: 'string', desc: '扩展名' },
    { name: 'download_url', type: 'string', desc: '下载地址' },
    { name: 'preview_url', type: 'string', desc: '预览地址' }
  ]
}

function handleChange(value: string[] | null): void {
  if (!value || value.length === 0) {
    clearValue()
    return
  }
  let base = formatVariablePath(value)
  // 子视图中 input.xxx 自动转换为 nodes.<loopKey>.input_xxx
  if (
    flowStore.isInSubView &&
    flowStore.subViewParentId &&
    base.startsWith(`${VariablePrefix.INPUT}.`)
  ) {
    const field = base.slice(VariablePrefix.INPUT.length + 1)
    base = `${VariablePrefix.NODES}.${flowStore.subViewParentId}.input_${field}`
  }
  const fullPath = subVar.value ? `${base}.${subVar.value}` : base
  if (fullPath === props.modelValue) return
  emit('update:modelValue', fullPath)

  const varType = getVariableTypeByPath(
    base,
    props.currentNodeId,
    flowStore.nodes,
    flowStore.edges,
    flowStore.flowInfo?.input_schema || null,
    flowStore.flowInfo?.flow_type
  )
  emit('update:type', varType)
}

function handleSubVarChange(): void {
  const base = formatVariablePath(selectedPath.value)
  const fullPath = subVar.value ? `${base}.${subVar.value}` : base
  emit('update:modelValue', fullPath)
}

function clearValue(): void {
  subVar.value = ''
  emit('update:modelValue', '')
  emit('update:type', undefined)
}
</script>

<template>
  <div class="variable-selector">
    <el-cascader
      :key="cascaderKey"
      :model-value="selectedPath"
      :options="variableOptions"
      :placeholder="placeholder || '选择变量'"
      :disabled="disabled"
      clearable
      filterable
      size="small"
      class="variable-cascader"
      @update:model-value="handleChange"
      @clear="clearValue"
    />
    <div v-if="modelValue" class="path-row">
      <el-input :model-value="modelValue" size="small" disabled class="variable-path">
        <template #prepend>路径</template>
      </el-input>
      <el-tooltip v-if="resolvedType" placement="top">
        <template #content>
          <div style="white-space: pre-line; font-size: 12px">{{ typeTooltip }}</div>
        </template>
        <el-icon class="type-help"><QuestionFilled /></el-icon>
      </el-tooltip>
    </div>
    <div v-if="modelValue" class="sub-var-row">
      <el-input
        v-model="subVar"
        size="small"
        placeholder="子变量路径（可选），如 data.field"
        clearable
        class="sub-var-input"
        @input="handleSubVarChange"
        @clear="handleSubVarChange"
      >
        <template #prepend>子变量</template>
      </el-input>
      <el-tooltip :content="`解析为: ${resolvedPath}`" placement="top">
        <el-icon class="sub-var-help"><QuestionFilled /></el-icon>
      </el-tooltip>
    </div>
  </div>
</template>

<style scoped>
.variable-selector {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.variable-cascader {
  width: 100%;
}

.variable-path {
  font-size: 12px;
}

.variable-path :deep(.el-input-group__prepend) {
  font-size: 12px;
  padding: 0 8px;
}

.path-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.type-help {
  flex-shrink: 0;
  color: #909399;
  cursor: help;
}

.sub-var-input {
  font-size: 12px;
}

.sub-var-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.sub-var-help {
  flex-shrink: 0;
  color: #909399;
  cursor: help;
}

.sub-var-input :deep(.el-input-group__prepend) {
  font-size: 12px;
  padding: 0 8px;
}
</style>
