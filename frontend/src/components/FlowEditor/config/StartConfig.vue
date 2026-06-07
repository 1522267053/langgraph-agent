<script setup lang="ts">
import { ref, watch } from 'vue'
import { Lock } from '@element-plus/icons-vue'
import type { StartConfig, FlowIOField } from './types'
import { fieldTypeOptions } from './types'

const startFieldTypeOptions = fieldTypeOptions.filter(
  item => item.value !== 'object' && item.value !== 'array' && item.value !== 'python_result'
)

const props = defineProps<{
  config: StartConfig
  isAgentMode?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:config', value: StartConfig): void
  (e: 'update:input-schema', fields: FlowIOField[]): void
}>()

function cloneConfig(c: StartConfig): StartConfig {
  return { ...c, input_variables: [...(c.input_variables ?? [])] }
}

const localConfig = ref<StartConfig>(cloneConfig(props.config))

watch(
  () => props.config,
  newConfig => {
    localConfig.value = cloneConfig(newConfig)
  },
  { deep: true, immediate: true }
)

function isAgentFixedField(index: number): boolean {
  return props.isAgentMode && localConfig.value.input_variables[index]?.name === 'message'
}

function updateConfig(): void {
  emit('update:config', cloneConfig(localConfig.value))
  emit('update:input-schema', [...localConfig.value.input_variables])
}

function addInputField(): void {
  localConfig.value.input_variables.push({
    name: '',
    type: 'string',
    description: '',
    required: false
  })
  updateConfig()
}

function removeInputField(index: number): void {
  if (isAgentFixedField(index)) return
  localConfig.value.input_variables.splice(index, 1)
  updateConfig()
}

const acceptPresets = [
  { label: '图片', value: 'image/*' },
  { label: '文档', value: '.txt,.md,.pdf,.doc,.docx,.xls,.xlsx' },
  { label: '视频', value: '.mp4,.avi,.mov,.wmv' },
  { label: '音频', value: '.mp3,.wav,.ogg,.flac' }
]

function applyAcceptPreset(field: FlowIOField, preset: string): void {
  field.accept = preset
  updateConfig()
}
</script>

<template>
  <div class="start-config">
    <div class="config-section">
      <div class="section-title">
        <span>{{ isAgentMode ? '输入参数定义' : '输入参数定义' }}</span>
        <el-button type="primary" size="small" link @click="addInputField">+ 添加字段</el-button>
      </div>

      <div class="input-fields">
        <div v-for="(field, index) in localConfig.input_variables" :key="index" class="input-field">
          <div class="field-header">
            <span class="field-index">
              <template v-if="isAgentFixedField(index)">
                <el-icon style="margin-right: 4px"><Lock /></el-icon>
              </template>
              {{ isAgentFixedField(index) ? 'message' : `字段 ${index + 1}` }}
            </span>
            <el-button
              v-if="!isAgentFixedField(index)"
              type="danger"
              size="small"
              link
              @click="removeInputField(index)"
            >
              删除
            </el-button>
          </div>
          <el-form label-width="60px" size="small">
            <el-form-item label="名称">
              <el-input v-if="isAgentFixedField(index)" model-value="message" disabled />
              <el-input
                v-else
                v-model="field.name"
                placeholder="字段名（如: user_input）"
                @blur="updateConfig"
              />
            </el-form-item>
            <el-form-item label="类型">
              <el-select
                v-if="isAgentFixedField(index)"
                model-value="string"
                disabled
                style="width: 100%"
              />
              <el-select
                v-else
                v-model="field.type"
                placeholder="选择类型"
                style="width: 100%"
                @change="updateConfig"
              >
                <el-option
                  v-for="item in startFieldTypeOptions"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="field.description" placeholder="字段描述" @blur="updateConfig" />
            </el-form-item>
            <el-form-item label="占位提示">
              <el-input
                v-model="field.placeholder"
                placeholder="输入框内的提示文本"
                @blur="updateConfig"
              />
            </el-form-item>
            <el-form-item label="必填">
              <el-switch v-model="field.required" @change="updateConfig" />
            </el-form-item>
            <template v-if="field.type === 'file_list' && !isAgentFixedField(index)">
              <el-form-item label="文件类型">
                <div class="accept-config">
                  <div class="accept-presets">
                    <el-button
                      v-for="preset in acceptPresets"
                      :key="preset.value"
                      size="small"
                      :type="field.accept === preset.value ? 'primary' : 'default'"
                      plain
                      @click="applyAcceptPreset(field, preset.value)"
                    >
                      {{ preset.label }}
                    </el-button>
                  </div>
                  <el-input
                    v-model="field.accept"
                    placeholder="如: image/*,.pdf,.docx"
                    @blur="updateConfig"
                  />
                </div>
              </el-form-item>
              <el-form-item label="多文件">
                <el-switch v-model="field.multiple" @change="updateConfig" />
              </el-form-item>
              <el-form-item label="最大MB">
                <el-input-number
                  v-model="field.max_size"
                  :min="1"
                  :max="500"
                  placeholder="文件大小限制"
                  style="width: 100%"
                  @change="updateConfig"
                />
              </el-form-item>
            </template>
          </el-form>
        </div>
      </div>

      <div class="input-hint">
        <el-text size="small" type="info">
          {{
            isAgentMode
              ? 'message 为 Agent 固定输入，接收用户消息。扩展参数通过 input.{字段名} 传递。'
              : '定义的输入参数将作为流程的接口，供其他流程引用时使用。'
          }}
        </el-text>
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

.input-hint {
  margin-top: 12px;
  padding: 8px;
  background: #fdf6ec;
  border-radius: 4px;
}

.input-fields {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.input-field {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
}

.field-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.field-index {
  font-size: 12px;
  font-weight: 500;
  color: #409eff;
  display: flex;
  align-items: center;
}

.accept-config {
  width: 100%;
}

.accept-presets {
  display: flex;
  gap: 4px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
</style>
