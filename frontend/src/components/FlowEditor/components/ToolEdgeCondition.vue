<script setup lang="ts">
import { computed } from 'vue'
import { useFlowStore } from '@/stores/flowStore'

interface IntentItem {
  key: string
  description?: string
}

interface RouterInfo {
  nodeKey: string
  nodeName: string
  intents: IntentItem[]
}

const store = useFlowStore()

const props = defineProps<{
  /** 当前边的 data 字段（对应后端 condition） */
  modelValue?: Record<string, unknown> | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, unknown> | null): void
}>()

// 从当前 flow 中扫描所有意图路由节点及其配置
const routers = computed<RouterInfo[]>(() => {
  const result: RouterInfo[] = []
  for (const node of store.nodes) {
    if (node.type !== 'intent_router') continue
    const config = node.data?.config as { intents?: IntentItem[] } | undefined
    const intents = config?.intents || []
    result.push({
      nodeKey: node.id,
      nodeName: (node.data?.label as string) || node.id,
      intents
    })
  }
  return result
})

const isToolEdge = computed(() => store.selectedEdge?.sourceHandle === 'tools')

// 当前 condition 的 intent_filters
const intentFilters = computed<Record<string, string[]>>(() => {
  const data = props.modelValue
  if (!data) return {}
  return (data.intent_filters as Record<string, string[]>) || {}
})

// 当前 filter_logic
const filterLogic = computed(() => {
  const data = props.modelValue
  return (data?.filter_logic as string) || 'and'
})

// 判断某个路由器的某个意图是否被选中
function isIntentChecked(routerKey: string, intentKey: string): boolean {
  const filters = intentFilters.value
  const values = filters[routerKey]
  return values ? values.includes(intentKey) : false
}

// 判断某个路由器是否处于"不限制"状态（即未参与过滤）
function isRouterUnrestricted(routerKey: string): boolean {
  const filters = intentFilters.value
  return !filters[routerKey] || filters[routerKey].length === 0
}

// 切换某个意图的选中状态
function toggleIntent(routerKey: string, intentKey: string): void {
  const current = intentFilters.value[routerKey] || []
  const newValues = current.includes(intentKey)
    ? current.filter(k => k !== intentKey)
    : [...current, intentKey]
  updateRouterFilter(routerKey, newValues)
}

// 设置路由器为"不限制"
function setRouterUnrestricted(routerKey: string): void {
  updateRouterFilter(routerKey, [])
}

// 更新某个路由器的过滤值
function updateRouterFilter(routerKey: string, values: string[]): void {
  const newFilters = { ...intentFilters.value }
  if (values.length === 0) {
    delete newFilters[routerKey]
  } else {
    newFilters[routerKey] = values
  }
  emitCondition(newFilters, filterLogic.value)
}

// 切换 AND/OR
function setFilterLogic(logic: string): void {
  emitCondition(intentFilters.value, logic)
}

// 发出 condition 更新
function emitCondition(filters: Record<string, string[]>, logic: string): void {
  const hasFilters = Object.keys(filters).length > 0
  if (!hasFilters) {
    emit('update:modelValue', null)
    return
  }
  emit('update:modelValue', {
    intent_filters: filters,
    filter_logic: logic
  })
}

// 清除条件
function clearCondition(): void {
  emit('update:modelValue', null)
}

const hasActiveFilters = computed(() => {
  if (!props.modelValue) return false
  const filters = props.modelValue.intent_filters as Record<string, string[]> | undefined
  if (!filters) return false
  return Object.values(filters).some(v => v && v.length > 0)
})

// 条件预览文本
const previewText = computed(() => {
  const filters = intentFilters.value
  const entries = Object.entries(filters).filter(([, v]) => v && v.length > 0)
  if (entries.length === 0) return '始终启用'

  const logic = filterLogic.value
  const parts = entries.map(([routerKey, values]) => {
    const router = routers.value.find(r => r.nodeKey === routerKey)
    const name = router?.nodeName || routerKey
    return `${name} ∈ {${values.join(', ')}}`
  })
  const connector = logic === 'or' ? ' OR ' : ' AND '
  return parts.join(connector)
})
</script>

<template>
  <div v-if="isToolEdge && routers.length > 0" class="tool-edge-condition">
    <div class="condition-header">
      <span class="condition-title">工具过滤条件</span>
      <el-button v-if="hasActiveFilters" size="small" text type="primary" @click="clearCondition">
        清除
      </el-button>
    </div>

    <!-- AND/OR 切换 -->
    <div v-if="routers.length > 1" class="logic-toggle">
      <span class="logic-label">路由器间关系：</span>
      <el-radio-group :model-value="filterLogic" size="small" @change="setFilterLogic">
        <el-radio-button value="and">全部满足 (AND)</el-radio-button>
        <el-radio-button value="or">任一满足 (OR)</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 各路由器的意图列表 -->
    <div v-for="router in routers" :key="router.nodeKey" class="router-group">
      <div class="router-header">
        <span class="router-name">{{ router.nodeName }}</span>
        <el-button
          v-if="!isRouterUnrestricted(router.nodeKey)"
          size="small"
          text
          @click="setRouterUnrestricted(router.nodeKey)"
        >
          不限制
        </el-button>
      </div>

      <div v-if="router.intents.length === 0" class="no-intents">未配置意图</div>

      <el-checkbox
        v-for="intent in router.intents"
        :key="intent.key"
        :model-value="isIntentChecked(router.nodeKey, intent.key)"
        class="intent-checkbox"
        @change="toggleIntent(router.nodeKey, intent.key)"
      >
        <span class="intent-key">{{ intent.key }}</span>
        <span v-if="intent.description" class="intent-desc">({{ intent.description }})</span>
      </el-checkbox>
    </div>

    <!-- 条件预览 -->
    <div class="condition-preview">
      <el-text size="small" type="info">
        {{ previewText }}
      </el-text>
    </div>
  </div>

  <div v-else-if="isToolEdge && routers.length === 0" class="no-routers-hint">
    <el-text size="small" type="info">未检测到意图路由节点，此工具边始终启用</el-text>
  </div>
</template>

<style scoped>
.tool-edge-condition {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
  padding: 12px;
  background: var(--el-bg-color-page);
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
}

.condition-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.condition-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.logic-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logic-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.router-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  background: var(--el-bg-color);
  border-radius: 6px;
}

.router-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.router-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.no-intents {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  padding: 4px 0;
}

.intent-checkbox {
  margin-right: 0;
  height: auto;
  min-height: 24px;
  white-space: normal;
}

.intent-checkbox :deep(.el-checkbox__label) {
  white-space: normal;
  word-break: break-word;
  line-height: 1.4;
  display: inline;
}

.intent-key {
  font-size: 12px;
  font-family: monospace;
}

.intent-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-left: 4px;
}

.condition-preview {
  padding: 6px 8px;
  background: var(--el-bg-color);
  border-radius: 4px;
  border: 1px dashed var(--el-border-color);
  word-break: break-all;
}

.no-routers-hint {
  margin-top: 12px;
  padding: 8px;
  text-align: center;
}
</style>
