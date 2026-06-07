<script setup lang="ts">
import { ref, watch } from 'vue'
import type { ConditionConfig, ConditionRule } from './types'
import { conditionOperators } from './types'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: ConditionConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: ConditionConfig): void
}>()

const defaultRules: ConditionRule[] = [{ variable: '', operator: '==', value: '' }]

function cloneConfig(c: ConditionConfig): ConditionConfig {
  return { ...c, rules: [...(c.rules ?? defaultRules)] }
}

const localConfig = ref<ConditionConfig>(cloneConfig(props.config))

watch(
  () => props.config,
  newConfig => {
    localConfig.value = cloneConfig(newConfig)
  },
  { deep: true, immediate: true }
)

function updateConfig(): void {
  emit('update:config', { ...localConfig.value })
}

function addConditionRule(): void {
  localConfig.value.rules.push({
    variable: '',
    operator: '==',
    value: ''
  })
  updateConfig()
}

function removeConditionRule(index: number): void {
  localConfig.value.rules.splice(index, 1)
  updateConfig()
}
</script>

<template>
  <div class="condition-config">
    <div class="config-section">
      <div class="section-title">
        <span>条件配置</span>
        <el-button type="primary" size="small" link @click="addConditionRule">+ 添加条件</el-button>
      </div>

      <el-form label-width="60px" size="small" class="condition-form">
        <el-form-item label="逻辑">
          <el-radio-group v-model="localConfig.logic" @change="updateConfig">
            <el-radio value="and">且 (AND)</el-radio>
            <el-radio value="or">或 (OR)</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>

      <div class="condition-rules">
        <div v-for="(rule, index) in localConfig.rules" :key="index" class="condition-rule">
          <div class="rule-header">
            <span class="rule-index">条件 {{ index + 1 }}</span>
            <el-button
              type="danger"
              size="small"
              link
              :disabled="localConfig.rules.length <= 1"
              @click="removeConditionRule(index)"
            >
              删除
            </el-button>
          </div>
          <el-form label-width="60px" size="small">
            <el-form-item label="变量">
              <VariableSelector
                v-model="rule.variable"
                :current-node-id="currentNodeId"
                placeholder="选择条件变量"
              />
            </el-form-item>
            <el-form-item label="操作">
              <el-select
                v-model="rule.operator"
                placeholder="选择操作符"
                style="width: 100%"
                @change="updateConfig"
              >
                <el-option
                  v-for="op in conditionOperators"
                  :key="op.value"
                  :label="op.label"
                  :value="op.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item v-if="!['is_empty', 'is_not_empty'].includes(rule.operator)" label="值">
              <el-input v-model="rule.value" placeholder="比较值" @blur="updateConfig" />
            </el-form-item>
          </el-form>
        </div>
      </div>

      <div class="condition-hint">
        <el-text size="small" type="info">使用下拉选择器选择条件变量</el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-section {
  margin-bottom: 16px;
}

.section-title {
  font-size: 13px;
  color: #606266;
  margin-bottom: 12px;
  font-weight: 500;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.condition-form {
  margin-bottom: 12px;
}

.condition-rules {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.condition-rule {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
}

.rule-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.rule-index {
  font-size: 12px;
  font-weight: 500;
  color: #409eff;
}

.condition-hint {
  margin-top: 12px;
  padding: 8px;
  background: #fdf6ec;
  border-radius: 4px;
}
</style>
