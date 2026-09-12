<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { flowApi } from '@/api/flow'
import { useFlowStore } from '@/stores/flowStore'
import type { FlowToolConfig } from './types'

const props = defineProps<{
  config: FlowToolConfig
  nodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: FlowToolConfig): void
  (e: 'update:label', value: string): void
}>()

const store = useFlowStore()
const flows = ref<{ id: number; name: string; status?: number }[]>([])
const loaded = ref(false)
const loading = ref(false)

const localConfig = ref<FlowToolConfig>({ flow_id: null })

// 服务端已按 flow_type=flow 过滤；前端仅排除当前 Flow（防自调）
const availableFlows = computed(() => {
  const currentId = store.flowInfo.value?.id
  return flows.value.filter(f => f.id !== currentId)
})

watch(
  () => props.config,
  newConfig => {
    localConfig.value = {
      flow_id: newConfig.flow_id || null
    }
  },
  { deep: true, immediate: true }
)

async function loadFlows(): Promise<void> {
  if (loaded.value || loading.value) return
  loading.value = true
  try {
    // 服务端条件过滤：仅取普通 Flow（flow_type=flow），避开后端 page_size 上限 100，
    // 天然排除 is_delete=1 / agent。
    // 注：暂不限制 status（前端无发布入口，草稿也能被 Flow-as-Tool 调用；
    // 发布流程作为独立 P3 处理）。
    const res = await flowApi.page({
      page: 1,
      page_size: 100,
      condition: { flow_type: 'flow' }
    })
    if (res.data.code === 1 && res.data.data) {
      // 后端 PaginatedResponse.items（注意：前端 PaginatedResponse 类型也是 items，
      // 不是 list；ListResponse.list 仅用于旧 API）
      flows.value = (res.data.data.items || []) as {
        id: number
        name: string
        status?: number
      }[]
    }
    loaded.value = true
  } catch {
    flows.value = []
  } finally {
    loading.value = false
  }
}
loadFlows()

function updateConfig(): void {
  const selectedFlow = flows.value.find(f => f.id === localConfig.value.flow_id)
  if (selectedFlow) {
    emit('update:label', selectedFlow.name)
  }
  emit('update:config', { ...localConfig.value })
}
</script>

<template>
  <div class="flow-tool-config">
    <div class="config-section">
      <div class="section-title">Flow工具配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="选择Flow">
          <el-select
            v-model="localConfig.flow_id"
            placeholder="选择Flow"
            style="width: 100%"
            filterable
            :loading="loading"
            @change="updateConfig"
          >
            <el-option
              v-for="flow in availableFlows"
              :key="flow.id"
              :label="flow.name"
              :value="flow.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          Flow工具节点需通过工具连接到LLM节点使用。被引用的Flow类型必须为"普通流程"。
          调用时支持单工具双模式：首次调用不传 execution_id（启动执行）；收到 interrupted
          状态后再次调用，传入 execution_id + human_input 即可恢复等待中的Flow。
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
}

.config-hint {
  margin-top: 8px;
  line-height: 1.5;
}
</style>
