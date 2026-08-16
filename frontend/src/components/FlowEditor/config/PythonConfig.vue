<script setup lang="ts">
import type { PythonConfig } from './types'
import { fieldTypeOptions } from './types'
import { ElMessage } from 'element-plus'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'
import CodeEditor from '@/components/CodeEditor.vue'

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

/** 按参数类型规范化默认值（number 转数字、boolean 转 true/false，清空为 null） */
function updateDefaultValue(index: number, val: unknown): void {
  const variable = localConfig.value.input_variables[index]
  if (!variable) return
  if (val === '' || val === undefined || val === null) {
    variable.default_value = null
  } else if (variable.type === 'boolean') {
    variable.default_value = val === 'true' || val === true
  } else if (variable.type === 'number') {
    const num = Number(val)
    variable.default_value = Number.isNaN(num) ? null : num
  } else {
    variable.default_value = String(val)
  }
  updateConfig()
}

/** 工具名校验：字母开头，仅含字母/数字/下划线，非法名称后端回退默认名 */
function validateToolName(): void {
  const name = (localConfig.value.tool_name || '').trim()
  if (name && !/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name)) {
    ElMessage.warning('工具名需以字母开头，仅含字母、数字、下划线；非法名称将回退默认名')
  }
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
            <el-form-item label="描述">
              <el-input
                v-model="variable.description"
                placeholder="参数说明（工具模式下 LLM 可见）"
                @blur="updateConfig"
              />
            </el-form-item>
            <el-form-item label="必填">
              <el-switch
                :model-value="!!variable.required"
                active-text="是"
                inactive-text="否"
                @change="(val: boolean | string) => { variable.required = !!val; updateConfig() }"
              />
            </el-form-item>
            <!-- 默认值：按参数类型切换输入控件，未绑定来源或值为空时兜底 -->
            <el-form-item label="默认值">
              <el-input-number
                v-if="variable.type === 'number'"
                :model-value="typeof variable.default_value === 'number' ? variable.default_value : undefined"
                placeholder="未设置"
                style="width: 100%"
                @change="(val: number | undefined) => updateDefaultValue(index, val)"
              />
              <el-select
                v-else-if="variable.type === 'boolean'"
                :model-value="variable.default_value == null ? '' : String(variable.default_value)"
                clearable
                placeholder="未设置"
                @change="(val: string) => updateDefaultValue(index, val)"
              >
                <el-option label="true" value="true" />
                <el-option label="false" value="false" />
              </el-select>
              <el-input
                v-else
                :model-value="variable.default_value == null ? '' : String(variable.default_value)"
                placeholder="未绑定来源或值为空时使用"
                @blur="(e: FocusEvent) => updateDefaultValue(index, (e.target as HTMLInputElement).value)"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">
          变量名称需与 main 函数参数名一致，如 main(query, data) 对应名称 query、data
          <br />
          工具模式下：必填参数 LLM 必须提供，参数描述作为 LLM 填参依据，默认值作为参数缺省值
        </el-text>
      </div>
    </div>
    <div class="config-section">
      <div class="section-title">Python代码配置</div>
      <el-form label-width="50px" size="small">
        <el-form-item label="代码">
          <CodeEditor
            v-model="localConfig.code"
            placeholder="# 定义 main 函数，输入变量作为参数&#10;# 可在顶层 import 模块、定义辅助函数供 main 调用&#10;import math&#10;def helper(x):&#10;    return math.sqrt(x)&#10;def main(query, data):&#10;    result = helper(query) + data&#10;    print(f'处理中...')&#10;    return result"
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
          定义 main 函数，参数名与输入变量名称一致，return 返回结果；可在顶层导入模块、定义辅助函数供 main 调用
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
        <el-form-item v-if="localConfig.use_preset_for_tool" label="工具名">
          <el-input
            v-model="localConfig.tool_name"
            placeholder="可选，字母开头、仅含字母/数字/下划线，留空默认 python_节点key"
            @blur="validateToolName"
          />
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
