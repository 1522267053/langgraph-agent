<script setup lang="ts">
import type { EndConfig, FieldType } from './types'
import { fieldTypeOptions } from './types'
import { useConfigBase } from '@/composables/useConfigBase'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: EndConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: EndConfig): void
  (
    e: 'update:output-schema',
    fields: { name: string; type: FieldType; description: string; required: boolean }[]
  ): void
}>()

const { localConfig } = useConfigBase(
  () => props.config,
  (e, v) => emit('update:config', v)
)

function emitUpdate(): void {
  emit('update:config', { ...localConfig.value })
  emit(
    'update:output-schema',
    localConfig.value.output_variables.map(v => ({
      name: v.name,
      type: (v.type || 'string') as FieldType,
      description: v.source,
      required: false
    }))
  )
}

function addOutputVariable(): void {
  if (!localConfig.value.output_variables) {
    localConfig.value.output_variables = []
  }
  localConfig.value.output_variables.push({
    name: '',
    source: '',
    type: 'string'
  })
  emitUpdate()
}

function removeOutputVariable(index: number): void {
  if (!localConfig.value.output_variables) return
  localConfig.value.output_variables.splice(index, 1)
  emitUpdate()
}

function handleSourceTypeChange(index: number, type: FieldType | undefined): void {
  if (type && localConfig.value.output_variables?.[index]) {
    localConfig.value.output_variables[index].type = type
    emitUpdate()
  }
}
</script>

<template>
  <div class="end-config">
    <div class="config-section">
      <div class="section-title">
        <span>输出配置</span>
        <el-button type="primary" size="small" link @click="addOutputVariable">
          + 添加变量
        </el-button>
      </div>

      <div class="output-variables">
        <div
          v-for="(variable, index) in localConfig.output_variables"
          :key="index"
          class="output-variable"
        >
          <div class="variable-header">
            <span class="variable-index">变量 {{ index + 1 }}</span>
            <el-button type="danger" size="small" link @click="removeOutputVariable(index)">
              删除
            </el-button>
          </div>
          <el-form label-width="60px" size="small">
            <el-form-item label="名称">
              <el-input v-model="variable.name" placeholder="输出变量名" @blur="emitUpdate" />
            </el-form-item>
            <el-form-item label="类型">
              <el-select
                v-model="variable.type"
                placeholder="选择类型"
                style="width: 100%"
                @change="emitUpdate"
              >
                <el-option
                  v-for="item in fieldTypeOptions"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="来源">
              <VariableSelector
                v-model="variable.source"
                :current-node-id="currentNodeId"
                placeholder="选择变量来源"
                @update:model-value="emitUpdate"
                @update:type="t => handleSourceTypeChange(index, t)"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>

      <div class="config-hint">
        <el-text size="small" type="info">使用下拉选择器选择输出变量的来源</el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';
</style>
