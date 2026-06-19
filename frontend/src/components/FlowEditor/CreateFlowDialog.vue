<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { flowTemplateApi } from '@/api/flowTemplate'
import type { FlowTemplate } from '@/types/flowTemplate'

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
const templates = ref<FlowTemplate[]>([])
const selectedTemplateId = ref('')
const loadingTemplates = ref(false)

watch(
  () => props.visible,
  visible => {
    if (visible) {
      loadTemplates()
    } else {
      flowName.value = ''
      flowDescription.value = ''
    }
  }
)

async function loadTemplates() {
  loadingTemplates.value = true
  try {
    const flowType = props.isAgentMode ? 'agent' : 'flow'
    const res = await flowTemplateApi.list(flowType)
    if (res.data.code === 1) {
      templates.value = res.data.data || []
      if (templates.value.length > 0 && !selectedTemplateId.value) {
        selectedTemplateId.value = templates.value[0].id
      }
    }
  } catch {
    templates.value = []
  } finally {
    loadingTemplates.value = false
  }
}

function getTemplateIcon(id: string): string {
  const icons: Record<string, string> = {
    blank_flow: '⬜',
    rag_qa: '📚',
    customer_service: '🎧',
    data_pipeline: '⚙️',
    blank_agent: '🤖',
    knowledge_agent: '📖',
    full_agent: '🛠️'
  }
  return icons[id] || '📋'
}

function getTemplateDescription(id: string): string {
  const t = templates.value.find(t => t.id === id)
  return t?.description || ''
}

async function handleCreate(): Promise<void> {
  if (!flowName.value.trim()) {
    ElMessage.warning(props.isAgentMode ? '请输入Agent名称' : '请输入流程名称')
    return
  }

  if (!selectedTemplateId.value) {
    ElMessage.warning('请选择一个模板')
    return
  }

  try {
    const res = await flowTemplateApi.createFromTemplate({
      template_id: selectedTemplateId.value,
      name: flowName.value,
      description: flowDescription.value || undefined
    })
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
    width="520px"
    :close-on-click-modal="false"
    :show-close="false"
    @update:model-value="emit('update:visible', $event)"
  >
    <div class="template-section">
      <label class="template-label">选择模板</label>
      <div v-loading="loadingTemplates" class="template-grid">
        <div
          v-for="t in templates"
          :key="t.id"
          :class="['template-card', { active: selectedTemplateId === t.id }]"
          @click="selectedTemplateId = t.id"
        >
          <div class="template-icon">{{ getTemplateIcon(t.id) }}</div>
          <div class="template-info">
            <div class="template-name">{{ t.name }}</div>
            <div class="template-count">{{ t.node_count }} 个节点</div>
          </div>
        </div>
      </div>
      <div v-if="selectedTemplateId" class="template-desc">
        {{ getTemplateDescription(selectedTemplateId) }}
      </div>
    </div>

    <el-form label-width="100px" class="form-section">
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
          :rows="2"
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

<style scoped>
.template-section {
  margin-bottom: 20px;
}

.template-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 10px;
}

.template-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  min-height: 60px;
}

.template-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  background: #fff;
}

.template-card:hover {
  border-color: #409eff;
  background: #f0f7ff;
}

.template-card.active {
  border-color: #409eff;
  background: #ecf5ff;
  box-shadow: 0 0 0 1px #409eff;
}

.template-icon {
  font-size: 24px;
  line-height: 1;
  flex-shrink: 0;
}

.template-info {
  min-width: 0;
}

.template-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  line-height: 1.3;
}

.template-count {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.template-desc {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
  padding: 6px 10px;
  background: #f5f7fa;
  border-radius: 6px;
  line-height: 1.5;
}

.form-section {
  border-top: 1px solid #e4e7ed;
  padding-top: 16px;
}
</style>
