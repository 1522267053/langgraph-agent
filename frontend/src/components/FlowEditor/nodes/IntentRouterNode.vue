<script setup lang="ts">
import { computed } from 'vue'
import { Aim } from '@element-plus/icons-vue'
import BaseNode, { type HandleConfig } from './BaseNode.vue'

interface IntentItem {
  key: string
  description?: string
  examples?: string[]
  rule?: { keywords?: string[]; regex_patterns?: string[] }
}

const props = defineProps<{
  id: string
  data: { label?: string; config?: Record<string, unknown> }
  selected?: boolean
}>()

const cfg = computed(() => (props.data?.config ?? {}) as Record<string, unknown>)
const intents = computed<IntentItem[]>(() => (cfg.value.intents as IntentItem[]) ?? [])
const enableRule = computed(() => cfg.value.enable_rule_layer !== false)
const enableLlm = computed(() => cfg.value.enable_llm_layer !== false)

const layerBadge = computed(() => {
  if (enableRule.value && enableLlm.value) return '规则 + LLM'
  if (enableRule.value) return '仅规则'
  if (enableLlm.value) return '仅 LLM'
  return '未启用'
})

const handles = computed<HandleConfig[]>(() => {
  const list: HandleConfig[] = [
    {
      type: 'target',
      position: 'left',
      id: 'default',
      label: '入',
      color: 'green'
    }
  ]
  for (const it of intents.value) {
    const key = it.key?.trim()
    if (!key) continue
    list.push({
      type: 'source',
      position: 'right',
      id: key,
      label: key,
      color: 'blue'
    })
  }
  list.push({
    type: 'source',
    position: 'right',
    id: 'default',
    label: '默认',
    color: 'red'
  })
  return list
})
</script>

<template>
  <BaseNode
    :id="props.id"
    :data="props.data"
    :selected="props.selected"
    node-type="intent_router"
    color="#9c27b0"
    :icon="Aim"
    type-label="意图路由"
    :handles="handles"
  >
    <template #content>
      <div class="intent-router-body">
        <span class="intent-label">{{ props.data?.label || '意图路由' }}</span>
        <div class="intent-meta">
          <el-tag size="small" type="info" effect="plain">{{ intents.length }} 个意图</el-tag>
          <el-tag size="small" type="warning" effect="plain">{{ layerBadge }}</el-tag>
        </div>
      </div>
    </template>
  </BaseNode>
</template>

<style scoped>
.intent-router-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.intent-label {
  font-size: 13px;
  color: #303133;
}

.intent-meta {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
</style>
