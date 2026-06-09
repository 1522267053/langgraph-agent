<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import type { ApiConfig } from './types'
import { fieldTypeOptions } from './types'
import type { FileInfo } from '@/api/file'
import FilePickerDialog from '@/components/common/FilePickerDialog.vue'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: ApiConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: ApiConfig): void
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

function ensureDefaults(): void {
  const c = localConfig.value
  if (!c.content_type) c.content_type = 'application/json'
  if (!c.form_fields) c.form_fields = []
  if (!c.input_variables) c.input_variables = []
  if (c.use_preset_for_tool === undefined) c.use_preset_for_tool = false
  if (!c.description) c.description = ''
  if (!c.file_config) {
    c.file_config = {
      upload_fields: [],
      download: { enabled: false }
    }
  }
  if (!c.file_config.upload_fields) {
    c.file_config.upload_fields = []
  }
  if (!c.file_config.download) {
    c.file_config.download = { enabled: false }
  }
}

watch(
  () => props.config,
  () => ensureDefaults(),
  { immediate: true, deep: true }
)

const isMultipart = computed(() => localConfig.value.content_type === 'multipart/form-data')

function onContentTypeChange(val: 'application/json' | 'multipart/form-data'): void {
  localConfig.value.content_type = val
  if (val === 'multipart/form-data') {
    const fields = localConfig.value.file_config.upload_fields
    if (fields.length === 0) {
      fields.push({ field_name: 'file', file_ids: [] })
    }
  }
  updateConfig()
}

function addUploadField(): void {
  localConfig.value.file_config.upload_fields.push({ field_name: '', file_ids: [] })
  updateConfig()
}

function removeUploadField(index: number): void {
  localConfig.value.file_config.upload_fields.splice(index, 1)
  updateConfig()
}

const showFilePicker = ref(false)
const activeFieldIndex = ref(0)
const fileInfoMap = ref<Record<number, FileInfo>>({})

function openFilePicker(fieldIndex: number): void {
  activeFieldIndex.value = fieldIndex
  showFilePicker.value = true
}

function onFilePickerConfirm(ids: number[]): void {
  const fields = localConfig.value.file_config.upload_fields
  if (fields[activeFieldIndex.value]) {
    fields[activeFieldIndex.value].file_ids = ids
    updateConfig()
  }
}

function onFilePickerFiles(files: FileInfo[]): void {
  for (const f of files) {
    fileInfoMap.value[f.id] = f
  }
}

function removeFile(fieldIndex: number, fileId: number): void {
  const ids = localConfig.value.file_config.upload_fields[fieldIndex].file_ids
  const index = ids.indexOf(fileId)
  if (index > -1) {
    ids.splice(index, 1)
    updateConfig()
  }
}

function getFileName(fileId: number): string {
  return fileInfoMap.value[fileId]?.original_name || `文件 #${fileId}`
}

function addFormField(): void {
  localConfig.value.form_fields.push({ key: '', value: '' })
  updateConfig()
}

function removeFormField(index: number): void {
  localConfig.value.form_fields.splice(index, 1)
  updateConfig()
}

function updateFormField(): void {
  updateConfig()
}
</script>

<template>
  <div class="api-config">
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
          API 地址、请求头、请求体均支持 &#123;&#123; 变量名 &#125;&#125;
          模板语法引用输入变量，或直接使用 &#123;&#123; input.xxx &#125;&#125;、&#123;&#123;
          nodes.xxx.output &#125;&#125; 等完整路径
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">API配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="API地址">
          <el-input
            v-model="localConfig.api_url"
            placeholder="https://api.example.com/endpoint 或 https://api.example.com/{{ path }}"
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item label="请求方法">
          <el-select
            v-model="localConfig.method"
            placeholder="选择请求方法"
            style="width: 100%"
            @change="updateConfig"
          >
            <el-option label="GET" value="GET" />
            <el-option label="POST" value="POST" />
            <el-option label="PUT" value="PUT" />
            <el-option label="DELETE" value="DELETE" />
          </el-select>
        </el-form-item>
        <el-form-item label="请求头">
          <el-input
            v-model="localConfig.headers"
            type="textarea"
            :rows="2"
            placeholder='{"Authorization": "Bearer xxx"}'
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item label="下载响应">
          <el-switch
            :model-value="localConfig.file_config?.download?.enabled || false"
            active-text="启用"
            inactive-text="关闭"
            @change="
              (val: boolean | string) => {
                localConfig.file_config.download = { enabled: !!val }
                updateConfig()
              }
            "
          />
          <el-text
            v-if="localConfig.file_config?.download?.enabled"
            size="small"
            type="info"
            style="margin-left: 12px"
          >
            响应为文件时自动保存到文件管理，输出变量 downloaded_file
          </el-text>
        </el-form-item>
      </el-form>

      <template v-if="localConfig.method !== 'GET'">
        <div class="section-title" style="margin-top: 12px">请求体配置</div>
        <el-form label-width="80px" size="small">
          <el-form-item label="类型">
            <el-radio-group :model-value="localConfig.content_type" @change="onContentTypeChange">
              <el-radio value="application/json">JSON</el-radio>
              <el-radio value="multipart/form-data">表单上传</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item v-if="!isMultipart" label="请求体">
            <el-input
              v-model="localConfig.body"
              type="textarea"
              :rows="3"
              placeholder='{"key": "value"}'
              @blur="updateConfig"
            />
          </el-form-item>

          <template v-else>
            <div class="section-title" style="margin-top: 12px; font-size: 12px">
              上传文件字段
              <el-button :icon="Plus" size="small" link type="primary" @click="addUploadField">
                添加
              </el-button>
            </div>
            <div
              v-for="(uf, ufIdx) in localConfig.file_config.upload_fields"
              :key="ufIdx"
              class="upload-field-card"
            >
              <div class="upload-field-header">
                <span class="upload-field-label">字段 {{ ufIdx + 1 }}</span>
                <el-button size="small" type="danger" link @click="removeUploadField(ufIdx)">
                  删除
                </el-button>
              </div>
              <div class="upload-field-row">
                <el-input
                  v-model="uf.field_name"
                  placeholder="字段名（如 resume、photo）"
                  size="small"
                  @blur="updateConfig"
                />
                <el-button size="small" type="primary" plain @click="openFilePicker(ufIdx)">
                  选择文件
                </el-button>
              </div>
              <div v-if="uf.file_ids.length > 0" class="selected-file-list">
                <div v-for="fid in uf.file_ids" :key="fid" class="selected-file-item">
                  <span class="selected-file-name" :title="getFileName(fid)">
                    {{ getFileName(fid) }}
                  </span>
                  <el-button size="small" type="danger" link @click="removeFile(ufIdx, fid)">
                    移除
                  </el-button>
                </div>
              </div>
            </div>

            <div class="section-title" style="margin-top: 16px; font-size: 12px">
              表单字段
              <el-button :icon="Plus" size="small" link type="primary" @click="addFormField">
                添加
              </el-button>
            </div>
            <div v-for="(field, idx) in localConfig.form_fields" :key="idx" class="form-field-row">
              <el-input
                v-model="field.key"
                placeholder="字段名"
                size="small"
                @blur="updateFormField"
              />
              <el-input
                v-model="field.value"
                placeholder="字段值"
                size="small"
                @blur="updateFormField"
              />
              <el-button size="small" type="danger" link @click="removeFormField(idx)">
                删除
              </el-button>
            </div>
          </template>
        </el-form>
      </template>

      <div class="config-hint">
        <el-text size="small" type="info">
          输出变量: body（响应体）、status_code（状态码）、headers（响应头）
          <template v-if="localConfig.file_config?.download?.enabled">
            ；文件下载时为 downloaded_file（包含 file_id, original_name, download_url）
          </template>
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">工具模式</div>
      <el-form label-width="100px" size="small">
        <el-form-item label="使用预设参数">
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
            开启后作为工具时使用已配置的URL/方法/请求体，LLM 只提供输入变量值
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

    <FilePickerDialog
      v-model="showFilePicker"
      :selected-ids="localConfig.file_config.upload_fields[activeFieldIndex]?.file_ids || []"
      :multiple="true"
      @update:selected-ids="onFilePickerConfirm"
      @confirm="onFilePickerFiles"
    />
  </div>
</template>

<style scoped>
@import './config-styles.css';

.upload-field-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.upload-field-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.upload-field-label {
  font-size: 12px;
  font-weight: 600;
  color: #334155;
}

.upload-field-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.upload-field-row .el-input {
  flex: 1;
}

.selected-file-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.selected-file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 0;
}

.selected-file-name {
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.form-field-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.form-field-row .el-input {
  flex: 1;
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
