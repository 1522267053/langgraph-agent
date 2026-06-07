<script setup lang="ts">
import { ref } from 'vue'
import type { FlowIOField } from '@/types/flow'
import FlowInputForm from '@/components/common/FlowInputForm.vue'

defineProps<{
  visible: boolean
  inputFields: FlowIOField[]
  isAgentMode: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (
    e: 'execute',
    input: Record<string, unknown>,
    attachedFiles: Array<{ id: number; original_name: string; mime_type: string }>
  ): void
  (e: 'showHistory'): void
}>()

const formData = ref<Record<string, unknown>>({})
const inputFormRef = ref<InstanceType<typeof FlowInputForm>>()

function handleVisibleChange(val: boolean): void {
  if (val) {
    formData.value = {}
  }
  emit('update:visible', val)
}

function confirmExecute(): void {
  if (!inputFormRef.value) return
  const error = inputFormRef.value.validate()
  if (error) {
    emit('update:visible', false)
    return
  }
  const { input, attachedFiles } = inputFormRef.value.collect()
  emit('execute', input, attachedFiles)
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="执行流程"
    width="650px"
    @update:model-value="handleVisibleChange"
  >
    <div v-if="inputFields.length === 0" style="padding: 20px 0">
      <el-empty description="该流程无需输入参数" :image-size="60" />
    </div>
    <FlowInputForm
      v-else
      ref="inputFormRef"
      v-model="formData"
      :fields="inputFields"
      :source-type="isAgentMode ? 'agent' : 'flow'"
      show-tooltip
    />
    <template #footer>
      <el-button @click="emit('showHistory')">历史执行</el-button>
      <el-button @click="emit('update:visible', false)">取消</el-button>
      <el-button type="primary" @click="confirmExecute">执行</el-button>
    </template>
  </el-dialog>
</template>
