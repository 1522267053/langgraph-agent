<script setup lang="ts">
import { computed } from 'vue'
import type {
  ImportConflictAction,
  ImportConflictItem,
  ImportConflictPolicy,
  ImportConflictResult
} from '@/types/flow'

const props = defineProps<{
  conflicts: ImportConflictResult
  modelValue: ImportConflictPolicy
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: ImportConflictPolicy): void
}>()

interface BlockConfig {
  key: 'skills' | 'knowledge_bases' | 'mcp_servers'
  itemPrefix: string
  label: string
}

const blocks: BlockConfig[] = [
  { key: 'skills', itemPrefix: 'skill:', label: '技能' },
  { key: 'knowledge_bases', itemPrefix: 'knowledge_base:', label: '知识库' },
  { key: 'mcp_servers', itemPrefix: 'mcp_server:', label: 'MCP 服务器' }
]

/** 仅展示有冲突的块 */
const activeBlocks = computed(() =>
  blocks
    .map(b => ({ ...b, items: props.conflicts[b.key] as ImportConflictItem[] }))
    .filter(b => b.items.length > 0)
)

const hasConflicts = computed(() => activeBlocks.value.length > 0)

function blockValue(key: BlockConfig['key']): ImportConflictAction {
  return props.modelValue[key] ?? 'copy'
}

function itemValue(prefix: string, name: string): ImportConflictAction | 'default' {
  return props.modelValue.items?.[prefix + name] ?? 'default'
}

function update(patch: Partial<ImportConflictPolicy>) {
  emit('update:modelValue', { ...props.modelValue, ...patch })
}

function setBlock(key: BlockConfig['key'], value: ImportConflictAction) {
  update({ [key]: value } as Partial<ImportConflictPolicy>)
}

function setItem(prefix: string, name: string, value: ImportConflictAction | 'default') {
  const items = { ...(props.modelValue.items || {}) }
  const key = prefix + name
  if (value === 'default') {
    delete items[key]
  } else {
    items[key] = value
  }
  update({ items })
}
</script>

<template>
  <div v-if="hasConflicts" class="conflict-selector">
    <div class="conflict-header">
      <span class="conflict-title">检测到与本地同名的资源，请选择处理方式</span>
      <span class="conflict-hint">「创建副本」保留本地资源不动，导入为新副本；「使用现有的」导入时直接引用本地资源</span>
    </div>

    <div v-for="block in activeBlocks" :key="block.key" class="conflict-block">
      <div class="block-head">
        <span class="block-label">{{ block.label }}</span>
        <el-radio-group
          :model-value="blockValue(block.key)"
          size="small"
          @update:model-value="(v: any) => setBlock(block.key, v)"
        >
          <el-radio-button value="copy">创建副本</el-radio-button>
          <el-radio-button value="reuse">使用现有的</el-radio-button>
        </el-radio-group>
      </div>

      <div class="block-items">
        <div v-for="item in block.items" :key="item.name" class="conflict-item">
          <span class="item-name" :title="item.name">{{ item.name }}</span>
          <el-radio-group
            :model-value="itemValue(block.itemPrefix, item.name)"
            size="small"
            @update:model-value="(v: any) => setItem(block.itemPrefix, item.name, v)"
          >
            <el-radio-button value="default">跟随上方</el-radio-button>
            <el-radio-button value="copy">副本</el-radio-button>
            <el-radio-button value="reuse">现有</el-radio-button>
          </el-radio-group>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.conflict-selector {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  background: #f8fafc;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.conflict-header {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.conflict-title {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
}

.conflict-hint {
  font-size: 12px;
  color: #94a3b8;
}

.conflict-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.block-label {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}

.block-items {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-left: 12px;
  border-left: 2px solid #e2e8f0;
}

.conflict-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.item-name {
  font-size: 12px;
  color: #64748b;
  max-width: 60%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
