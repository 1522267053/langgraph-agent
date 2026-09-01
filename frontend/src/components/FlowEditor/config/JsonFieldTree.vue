<script setup lang="ts">
import type { JsonOutputField } from './types'

const props = withDefaults(
  defineProps<{
    /** 嵌套层级：0 为根（显示"添加字段"工具条），子级由父级渲染"添加子字段"入口 */
    level?: number
  }>(),
  { level: 0 }
)

const fields = defineModel<JsonOutputField[]>({ required: true })

const typeOptions = [
  { label: '字符串', value: 'string' },
  { label: '数字', value: 'number' },
  { label: '布尔', value: 'boolean' },
  { label: '数组', value: 'array' },
  { label: '对象', value: 'object' }
]

const itemTypeOptions = [
  { label: '字符串', value: 'string' },
  { label: '数字', value: 'number' },
  { label: '布尔', value: 'boolean' },
  { label: '对象', value: 'object' }
]

function createEmptyField(): JsonOutputField {
  return { name: '', type: 'string', description: '', required: true }
}

function addField(): void {
  fields.value.push(createEmptyField())
}

function removeField(index: number): void {
  fields.value.splice(index, 1)
}

/** object 或 数组元素为对象 时需要 children 子字段编辑区 */
function needsChildren(field: JsonOutputField): boolean {
  return field.type === 'object' || (field.type === 'array' && field.item_type === 'object')
}

/** 切换类型时清理不相关键（item_type 仅数组用；children 仅 object/数组元素为对象用） */
function handleTypeChange(field: JsonOutputField): void {
  if (field.type === 'array' && !field.item_type) {
    field.item_type = 'string'
  }
  if (field.type !== 'array') {
    delete field.item_type
  }
  if (needsChildren(field)) {
    if (!field.children) field.children = []
  } else {
    delete field.children
  }
}
</script>

<template>
  <div class="json-field-tree">
    <div v-if="props.level === 0" class="tree-toolbar">
      <el-button type="primary" size="small" link @click="addField">+ 添加字段</el-button>
    </div>
    <div v-for="(field, index) in fields" :key="index" class="field-node">
      <div class="field-row">
        <el-input
          v-model="field.name"
          placeholder="字段名"
          size="small"
          class="name-input"
        />
        <el-select
          v-model="field.type"
          size="small"
          class="type-select"
          @change="handleTypeChange(field)"
        >
          <el-option
            v-for="opt in typeOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-checkbox v-model="field.required" size="small" class="required-check">必填</el-checkbox>
        <el-button type="danger" size="small" link @click="removeField(index)">删除</el-button>
      </div>
      <el-input
        v-model="field.description"
        placeholder="字段含义，供模型理解"
        size="small"
        class="desc-input"
      />
      <!-- 数组：选择元素类型，元素为对象时可继续定义元素字段 -->
      <div v-if="field.type === 'array'" class="item-type-row">
        <span class="row-label">元素类型</span>
        <el-select
          v-model="field.item_type"
          size="small"
          class="type-select"
          @change="handleTypeChange(field)"
        >
          <el-option
            v-for="opt in itemTypeOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </div>
      <!-- 子字段区：无限层级递归 -->
      <div v-if="needsChildren(field)" class="children-block">
        <div class="children-header">
          <span class="children-title">{{ field.type === 'array' ? '元素字段' : '子字段' }}</span>
          <el-button
            type="primary"
            size="small"
            link
            @click="field.children?.push(createEmptyField())"
          >
            + 添加
          </el-button>
        </div>
        <JsonFieldTree v-model="field.children!" :level="props.level + 1" />
      </div>
    </div>
    <el-text v-if="fields.length === 0" size="small" type="info">暂无字段</el-text>
  </div>
</template>

<style scoped>
.json-field-tree {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tree-toolbar {
  display: flex;
  justify-content: flex-start;
}

.field-node {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fafafa;
}

.field-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.name-input {
  flex: 1;
  min-width: 80px;
}

.type-select {
  width: 92px;
  flex-shrink: 0;
}

.required-check {
  flex-shrink: 0;
  height: auto;
}

.desc-input {
  width: 100%;
}

.item-type-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.row-label {
  font-size: 12px;
  color: #64748b;
  flex-shrink: 0;
}

.children-block {
  padding-left: 10px;
  border-left: 2px solid #dcdfe6;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.children-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.children-title {
  font-size: 12px;
  color: #64748b;
  font-weight: 600;
}
</style>
