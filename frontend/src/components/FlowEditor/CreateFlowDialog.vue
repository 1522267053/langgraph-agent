<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { flowApi } from '@/api/flow'

const props = defineProps<{
  visible: boolean
  isAgentMode: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'created', flowId: number): void
}>()

const router = useRouter()
const flowName = ref('')
const flowDescription = ref('')

watch(
  () => props.visible,
  visible => {
    if (!visible) {
      flowName.value = ''
      flowDescription.value = ''
    }
  }
)

async function handleCreate(): Promise<void> {
  if (!flowName.value.trim()) {
    ElMessage.warning(props.isAgentMode ? '请输入Agent名称' : '请输入流程名称')
    return
  }

  try {
    const res = props.isAgentMode
      ? await flowApi.createAgent({ name: flowName.value, description: flowDescription.value })
      : await flowApi.create({ name: flowName.value, description: flowDescription.value })
    if (res.data.code === 1) {
      emit('created', res.data.data.id!)
      emit('update:visible', false)
      ElMessage.success('创建成功')
    }
  } catch {
    // ignore
  }
}

function handleCancel(): void {
  router.push('/flow')
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="isAgentMode ? '创建 Agent' : '创建流程'"
    width="450px"
    :close-on-click-modal="false"
    :show-close="false"
    @update:model-value="emit('update:visible', $event)"
  >
    <el-form label-width="100px">
      <el-form-item :label="isAgentMode ? 'Agent名称' : '流程名称'" required>
        <el-input
          v-model="flowName"
          :placeholder="isAgentMode ? '请输入Agent名称' : '请输入流程名称'"
        />
      </el-form-item>
      <el-form-item label="描述">
        <el-input
          v-model="flowDescription"
          type="textarea"
          :rows="3"
          :placeholder="isAgentMode ? '描述这个Agent的功能和用途' : '描述这个流程的功能和用途'"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="handleCancel">取消</el-button>
      <el-button type="primary" @click="handleCreate">创建</el-button>
    </template>
  </el-dialog>
</template>
