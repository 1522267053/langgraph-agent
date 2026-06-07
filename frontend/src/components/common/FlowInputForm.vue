<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { QuestionFilled } from '@element-plus/icons-vue'
import type { FileInfo } from '@/api/file'
import type { FlowIOField } from '@/types/flow'
import FilePickerDialog from '@/components/common/FilePickerDialog.vue'

const props = defineProps<{
  fields: FlowIOField[]
  modelValue: Record<string, unknown>
  sourceType?: string
  showTooltip?: boolean
  labelWidth?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, unknown>): void
}>()

const formData = reactive<Record<string, unknown>>({})
const filePickerVisible = ref(false)
const currentFileField = ref<string | null>(null)

function getDefaultValue(type: string): unknown {
  switch (type) {
    case 'number':
      return 0
    case 'boolean':
      return false
    case 'file_list':
      return [] as FileInfo[]
    default:
      return ''
  }
}

watch(
  () => props.fields,
  fields => {
    for (const field of fields) {
      if (!(field.name in formData)) {
        formData[field.name] = getDefaultValue(field.type)
      }
    }
    syncToParent()
  },
  { immediate: true, deep: true }
)

watch(
  () => props.modelValue,
  val => {
    if (val) {
      for (const field of props.fields) {
        if (field.name in val && val[field.name] !== undefined) {
          formData[field.name] = val[field.name]
        }
      }
    }
  },
  { immediate: true, deep: true }
)

watch(formData, () => syncToParent(), { deep: true })

function syncToParent(): void {
  emit('update:modelValue', { ...formData })
}

function openFilePicker(fieldName: string): void {
  currentFileField.value = fieldName
  filePickerVisible.value = true
}

function handleFilePickerConfirm(files: FileInfo[]): void {
  if (currentFileField.value) {
    formData[currentFileField.value] = files
  }
}

function removeFile(fieldName: string, fileId: number): void {
  const files = formData[fieldName] as FileInfo[]
  formData[fieldName] = files.filter(f => f.id !== fileId)
}

function validate(): string | null {
  for (const field of props.fields) {
    if (!field.required) continue
    const val = formData[field.name]
    if (field.type === 'file_list') {
      const fileVal = val as FileInfo[] | number[] | undefined
      if (!fileVal || !(Array.isArray(fileVal) && fileVal.length > 0)) {
        return `请上传文件：${field.name}`
      }
    } else if (!val) {
      return `请填写输入参数：${field.name}`
    }
  }
  return null
}

function collect(): {
  input: Record<string, unknown>
  attachedFiles: Array<{ id: number; original_name: string; mime_type: string }>
} {
  const input: Record<string, unknown> = {}
  const attachedFiles: Array<{
    id: number
    original_name: string
    mime_type: string
  }> = []

  for (const field of props.fields) {
    const value = formData[field.name]
    if (field.type === 'file_list') {
      const files = value as FileInfo[] | undefined
      if (Array.isArray(files) && files.length > 0) {
        input[field.name] = files.map(f => ({
          id: f.id,
          original_name: f.original_name,
          file_type: f.file_type,
          file_size: f.file_size,
          mime_type: f.mime_type,
          preview_url: '/' + f.file_path,
          file_path: f.file_path
        }))
        for (const f of files) {
          attachedFiles.push({
            id: f.id,
            original_name: f.original_name,
            mime_type: f.mime_type
          })
        }
      }
    } else if (field.type === 'object' || field.type === 'array') {
      if (typeof value === 'string' && value.trim()) {
        try {
          input[field.name] = JSON.parse(value)
        } catch {
          ElMessage.error(`字段 "${field.name}" 格式错误，请输入有效的JSON`)
          throw new Error(`字段 "${field.name}" JSON格式错误`)
        }
      } else {
        input[field.name] = field.type === 'object' ? {} : []
      }
    } else {
      if (value !== undefined && value !== '') {
        input[field.name] = value
      }
    }
  }

  return { input, attachedFiles }
}

defineExpose({ validate, collect })
</script>

<template>
  <el-form
    :label-width="props.labelWidth || 'auto'"
    class="flow-input-form"
    style="flex: 1; overflow-y: auto"
    @submit.prevent
  >
    <el-form-item
      v-for="field in fields"
      :key="field.name"
      :label="field.description || field.name"
      :required="field.required"
    >
      <template v-if="showTooltip" #label>
        <span class="field-label-text">{{ field.description || field.name }}</span>
        <el-tooltip
          v-if="field.placeholder || field.description"
          :content="field.placeholder || field.description"
          placement="top"
        >
          <el-icon style="margin-left: 4px; cursor: help">
            <QuestionFilled />
          </el-icon>
        </el-tooltip>
      </template>
      <el-input
        v-if="field.type === 'string'"
        v-model="formData[field.name] as string"
        type="textarea"
        :rows="3"
        :placeholder="field.placeholder || field.description || '请输入'"
      />
      <el-input-number
        v-else-if="field.type === 'number'"
        v-model="formData[field.name] as number"
        controls-position="right"
        style="width: 100%"
      />
      <el-switch v-else-if="field.type === 'boolean'" v-model="formData[field.name] as boolean" />
      <el-input
        v-else-if="field.type === 'object' || field.type === 'array'"
        v-model="formData[field.name] as string"
        type="textarea"
        :rows="3"
        :placeholder="
          field.placeholder || (field.type === 'object' ? '请输入JSON对象' : '请输入JSON数组')
        "
      />
      <template v-else-if="field.type === 'file_list'">
        <div v-if="(formData[field.name] as FileInfo[]).length > 0" class="selected-files">
          <el-tag
            v-for="f in formData[field.name] as FileInfo[]"
            :key="f.id"
            closable
            size="small"
            @close="removeFile(field.name, f.id)"
          >
            {{ f.original_name }}
          </el-tag>
        </div>
        <el-button size="small" @click="openFilePicker(field.name)">选择文件</el-button>
        <FilePickerDialog
          v-model="filePickerVisible"
          :selected-ids="(formData[currentFileField!] as FileInfo[])?.map(f => f.id) || []"
          :multiple="field.multiple"
          :max-size="field.max_size"
          :accept="field.accept"
          @confirm="handleFilePickerConfirm"
        />
      </template>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.flow-input-form :deep(.el-form-item__label) {
  white-space: normal;
  word-break: break-all;
  line-height: 1.4;
}

.flow-input-form .field-label-text {
  white-space: normal;
  word-break: break-all;
}

.selected-files {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}
</style>
