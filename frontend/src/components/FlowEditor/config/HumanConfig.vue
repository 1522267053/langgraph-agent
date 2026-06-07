<script setup lang="ts">
import type { HumanConfig } from './types'
import { fieldTypeOptions } from './types'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'
import { QuestionFilled } from '@element-plus/icons-vue'

const props = defineProps<{
  config: HumanConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: HumanConfig): void
}>()

const { localConfig, updateConfig } = useConfigBase(() => props.config, emit)
const { addInputVariable, removeInputVariable, handleSourceTypeChange } = useInputVariables(
  localConfig,
  updateConfig
)
</script>

<template>
  <div class="human-config">
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
              <el-input v-model="variable.name" placeholder="变量名" @blur="updateConfig" />
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
        <el-text size="small" type="info">输入变量将在审核时显示给用户</el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">人工协助配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item>
          <template #label>
            <span>协助提示</span>
            <el-tooltip content="连接到LLM节点时生效，引导AI何时请求人工帮助" placement="top">
              <el-icon class="hint-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <el-input
            v-model="localConfig.assist_prompt"
            type="textarea"
            :rows="2"
            placeholder="告诉AI何时请求人工帮助（连接到LLM时生效）"
            @blur="updateConfig"
          />
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          连接到LLM节点后，AI可自主调用 request_human_help 工具
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">人工审核配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item>
          <template #label>
            <span>审核提示</span>
            <el-tooltip
              content="作为流程检查点时生效。支持 {{变量名}} 引用下方输入变量，或 {{nodes.xxx.output}} 直接引用上游节点变量"
              placement="top"
            >
              <el-icon class="hint-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <el-input
            v-model="localConfig.review_prompt"
            type="textarea"
            :rows="2"
            placeholder="流程暂停时显示的提示（作为检查点时生效）"
            @blur="updateConfig"
          />
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

.hint-icon {
  margin-left: 4px;
  cursor: help;
  color: var(--el-text-color-placeholder);
}

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
