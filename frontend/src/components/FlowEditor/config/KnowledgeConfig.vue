<script setup lang="ts">
import { inject, ref, type Ref } from 'vue'
import type { KnowledgeConfig } from './types'
import { fieldTypeOptions } from './types'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: KnowledgeConfig
  nodeId: string
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: KnowledgeConfig): void
  (e: 'update:label', value: string): void
}>()

const knowledgeBases = inject<Ref<{ id: number; name: string }[]>>('knowledgeBases', ref([]))

const { localConfig, updateConfig } = useConfigBase(
  () => props.config,
  (e, v) => emit('update:config', v)
)
const { addInputVariable, removeInputVariable, handleSourceTypeChange } = useInputVariables(
  localConfig,
  updateConfig
)

function updateConfigWithLabel(): void {
  const kb = knowledgeBases.value.find(kb => kb.id === localConfig.value.knowledge_base_id)
  localConfig.value.knowledge_base_name = kb?.name || ''
  emit('update:config', { ...localConfig.value })
  emit('update:label', kb?.name || '')
}
</script>

<template>
  <div class="knowledge-config">
    <div class="config-section">
      <div class="section-title">
        <span>输入变量</span>
        <el-button type="primary" size="small" link @click="addInputVariable">+ 添加变量</el-button>
      </div>
      <div class="input-variables">
        <div
          v-for="(variable, index) in localConfig.input_variables"
          :key="index"
          class="input-variable"
        >
          <div class="variable-header">
            <span class="variable-index">变量 {{ index + 1 }}</span>
            <el-button type="danger" size="small" link @click="removeInputVariable(index)">
              删除
            </el-button>
          </div>
          <el-form label-width="60px" size="small">
            <el-form-item label="名称">
              <el-input
                v-model="variable.name"
                placeholder="变量名（如: query）"
                @blur="updateConfig"
              />
            </el-form-item>
            <el-form-item label="类型">
              <el-select
                v-model="variable.type"
                placeholder="选择类型"
                style="width: 100%"
                @change="updateConfig"
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
                @update:model-value="updateConfig"
                @update:type="t => handleSourceTypeChange(index, t)"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">
          使用下拉选择器选择变量来源
          <br />
          名称为 "query" 的变量将作为查询条件
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">知识库配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="选择知识库">
          <el-select
            v-model="localConfig.knowledge_base_id"
            placeholder="选择知识库"
            style="width: 100%"
            @change="updateConfigWithLabel"
          >
            <el-option v-for="kb in knowledgeBases" :key="kb.id" :label="kb.name" :value="kb.id" />
          </el-select>
        </el-form-item>
      </el-form>
    </div>

    <div class="config-section">
      <div class="section-title">输出配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="返回数量">
          <el-input-number v-model="localConfig.top_k" :min="1" :max="20" @change="updateConfig" />
          <span class="unit-label">条</span>
        </el-form-item>
      </el-form>
    </div>

    <div class="config-section">
      <div class="section-title">输出变量</div>
      <div class="output-variables-info">
        <div v-for="ov in localConfig.output_variables" :key="ov.name" class="output-var-tag">
          <el-tag size="small" type="info">{{ ov.name }}</el-tag>
          <span class="output-var-type">{{ ov.type || '' }}</span>
        </div>
        <el-text size="small" type="info">下游节点通过变量映射使用</el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';

.output-variables-info {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.output-var-tag {
  display: flex;
  align-items: center;
  gap: 4px;
}
.output-var-type {
  font-size: 12px;
  color: #909399;
}
</style>
