<script setup lang="ts">
import type { ShellConfig } from './types'
import { fieldTypeOptions } from './types'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: ShellConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: ShellConfig): void
}>()

const { localConfig, updateConfig } = useConfigBase(() => props.config, emit)
const { addInputVariable, removeInputVariable, handleSourceTypeChange } = useInputVariables(
  localConfig,
  updateConfig
)

function updateVariableSource(index: number, source: string): void {
  if (localConfig.value.input_variables[index])
    localConfig.value.input_variables[index].source = source
  updateConfig()
}
</script>

<template>
  <div class="shell-config">
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
                :model-value="variable.source"
                :current-node-id="currentNodeId"
                placeholder="选择变量来源"
                @update:model-value="(v: string) => updateVariableSource(index, v)"
                @update:type="t => handleSourceTypeChange(index, t)"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">
          使用下拉选择器选择变量来源，在命令中通过 &#123;&#123; 变量名 &#125;&#125; 引用
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">Shell命令配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="命令">
          <el-input
            v-model="localConfig.command"
            type="textarea"
            :rows="3"
            placeholder="输入 Shell 命令，支持 {{ 变量名 }} 引用输入变量"
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item label="超时">
          <el-input-number
            v-model="localConfig.timeout"
            :min="5"
            :max="300"
            @change="updateConfig"
          />
          <span class="unit-label">秒</span>
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          独立执行时按上方命令运行，支持 &#123;&#123; 变量名 &#125;&#125; 引用输入变量；连接到 LLM
          后，AI 可自主调用 shell_executor 工具执行命令
        </el-text>
      </div>
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
