<script setup lang="ts">
import type { PythonConfig } from './types'
import { fieldTypeOptions } from './types'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: PythonConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: PythonConfig): void
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
  <div class="python-config">
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
                placeholder="变量名（与 main 参数名一致）"
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
          变量名称需与 main 函数参数名一致，如 main(query, data) 对应名称 query、data
        </el-text>
      </div>
    </div>
    <div class="config-section">
      <div class="section-title">Python代码配置</div>
      <el-form label-width="50px" size="small">
        <el-form-item label="代码">
          <el-input
            v-model="localConfig.code"
            type="textarea"
            :rows="10"
            placeholder="# 定义 main 函数，输入变量作为参数&#10;def main(query, data):&#10;    result = query + data&#10;    print(f'处理中...')&#10;    return result"
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
          定义 main 函数，参数名与输入变量名称一致，return 返回结果
          <br />
          输出: {stdout: str, stderr: str, result: 函数返回的结果, success: true / false}
          <br />
          生成媒体文件时，main() 返回 {"__save_file__": True, "content_base64": "&lt;base64&gt;",
          "mime_type": "image/png"} 可自动保存为文件并在聊天中预览
        </el-text>
      </div>
    </div>
    <div class="config-section">
      <div class="section-title">工具模式</div>
      <el-form label-width="100px" size="small">
        <el-form-item label="使用预设代码">
          <el-switch
            :model-value="localConfig.use_preset_for_tool"
            active-text="开"
            inactive-text="关"
            @change="
              (val: boolean | string) => {
                localConfig.use_preset_for_tool = !!val
                if (!val) localConfig.description = ''
                updateConfig()
              }
            "
          />
          <el-text size="small" type="info" style="margin-left: 12px">
            开启后作为工具时使用已配置的代码，LLM 只提供输入变量值
          </el-text>
        </el-form-item>
        <el-form-item v-if="localConfig.use_preset_for_tool" label="工具描述">
          <el-input
            v-model="localConfig.description"
            type="textarea"
            :rows="2"
            placeholder="描述工具的用途，LLM 据此判断何时调用（如：向 eLink 会话发送文本消息）"
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
