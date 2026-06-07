<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Delete } from '@element-plus/icons-vue'
import type { LoopConfig } from './types'
import type { FieldType } from '@/types/flow'
import { fieldTypeOptions } from './types'
import VariableSelector from '../components/VariableSelector.vue'
import { useFlowStore } from '@/stores/flowStore'

const props = defineProps<{
  config: LoopConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: LoopConfig): void
}>()

const store = useFlowStore()

function cloneConfig(c: LoopConfig): LoopConfig {
  return {
    ...c,
    input_mappings: [...(c.input_mappings ?? [])],
    output_variables: [...(c.output_variables ?? [])]
  }
}

const localConfig = ref<LoopConfig>(cloneConfig(props.config))

watch(
  () => props.config,
  newConfig => {
    localConfig.value = cloneConfig(newConfig)
  },
  { deep: true, immediate: true }
)

function updateConfig(): void {
  emit('update:config', cloneConfig(localConfig.value))
}

function enterSubView(): void {
  store.enterSubView(props.currentNodeId)
}

function addInputMapping(): void {
  localConfig.value.input_mappings.push({ card_field: '', source: '' })
  updateConfig()
}

function removeInputMapping(index: number): void {
  localConfig.value.input_mappings.splice(index, 1)
  updateConfig()
}

function handleSourceTypeChange(index: number, type: FieldType | undefined): void {
  if (type && localConfig.value.input_mappings[index]) {
    localConfig.value.input_mappings[index].type = type
    updateConfig()
  }
}

// 从子视图 end 节点读取 output_variables，展示为输出预览
const subViewEndOutputs = computed(() => {
  const prefix = `${props.currentNodeId}__`
  const endNode = store.nodes.find(n => n.id.startsWith(prefix) && n.type === 'end')
  if (!endNode) return []
  const config = endNode.data?.config as Record<string, unknown> | undefined
  const outputVars = (config?.output_variables || []) as Array<{
    name: string
    type?: string
    source?: string
  }>
  return outputVars.filter(v => v.name)
})

const nodeKey = computed(() => {
  const node = store.nodes.find(n => n.id === props.currentNodeId)
  return node?.data?.node_key || props.currentNodeId
})
</script>

<template>
  <div class="loop-config">
    <div class="config-section">
      <div class="section-title">
        <span>子流程</span>
        <el-button type="primary" size="small" @click="enterSubView">编辑子流程</el-button>
      </div>
      <div class="sub-flow-hint">
        <el-text size="small" type="info">
          点击"编辑子流程"进入循环体编辑。子流程中可使用循环变量。
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">循环模式</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="模式">
          <el-radio-group v-model="localConfig.loop_mode" @change="updateConfig">
            <el-radio value="count">固定次数</el-radio>
            <el-radio value="condition">条件表达式</el-radio>
            <el-radio value="for_each">数组遍历</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="localConfig.loop_mode === 'count'" label="循环次数">
          <el-input-number
            v-model="localConfig.max_count"
            :min="1"
            :max="1000"
            @change="updateConfig"
          />
        </el-form-item>

        <el-form-item v-if="localConfig.loop_mode === 'condition'" label="条件表达式">
          <el-input
            v-model="localConfig.condition_expression"
            type="textarea"
            :rows="2"
            placeholder="例如: loop_index < 10"
            @blur="updateConfig"
          />
        </el-form-item>

        <el-form-item v-if="localConfig.loop_mode === 'for_each'" label="数组来源">
          <VariableSelector
            v-model="localConfig.for_each_source"
            :current-node-id="currentNodeId"
            placeholder="选择数组变量"
            @update:model-value="updateConfig"
          />
        </el-form-item>

        <el-form-item v-if="localConfig.loop_mode === 'for_each'" label="元素类型">
          <el-select
            v-model="localConfig.for_each_item_type"
            placeholder="选择元素类型"
            style="width: 100%"
            clearable
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

        <el-form-item label="出错中断">
          <el-switch v-model="localConfig.break_on_error" @change="updateConfig" />
        </el-form-item>

        <el-form-item label="并发数">
          <el-input-number
            v-model="localConfig.concurrency"
            :min="1"
            :max="20"
            @change="updateConfig"
          />
        </el-form-item>
      </el-form>
    </div>

    <div class="config-section">
      <div class="section-title">
        <span>输入映射</span>
        <el-button size="small" @click="addInputMapping">添加映射</el-button>
      </div>
      <div v-if="localConfig.input_mappings.length" class="loop-mappings">
        <div
          v-for="(mapping, index) in localConfig.input_mappings"
          :key="index"
          class="loop-mapping"
        >
          <el-form label-width="70px" size="small">
            <el-form-item label="变量名">
              <el-input
                v-model="mapping.card_field"
                placeholder="子图内变量名"
                @blur="updateConfig"
              />
            </el-form-item>
            <el-form-item label="类型">
              <el-select
                v-model="mapping.type"
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
                v-model="mapping.source"
                :current-node-id="currentNodeId"
                placeholder="选择来源变量"
                @update:model-value="updateConfig"
                @update:type="t => handleSourceTypeChange(index, t)"
              />
            </el-form-item>
          </el-form>
          <el-button
            type="danger"
            size="small"
            :icon="Delete"
            circle
            @click="removeInputMapping(index)"
          />
        </div>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">循环变量</div>
      <div class="loop-vars-hint">
        <div class="var-item">
          <el-tag size="small" type="warning">loop_index</el-tag>
          <span>当前循环索引（从 0 开始）</span>
        </div>
        <div class="var-item">
          <el-tag size="small" type="warning">loop_count</el-tag>
          <span>总循环次数</span>
        </div>
        <div v-if="localConfig.loop_mode === 'for_each'" class="var-item">
          <el-tag size="small" type="warning">loop_item</el-tag>
          <span>当前遍历元素</span>
        </div>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">输出变量</div>
      <div v-if="subViewEndOutputs.length" class="output-preview">
        <div v-for="varItem in subViewEndOutputs" :key="varItem.name" class="output-item">
          <el-tag size="small" type="warning">array&lt;{{ varItem.type || 'string' }}&gt;</el-tag>
          <code class="output-path">{{ varItem.name }}</code>
        </div>
      </div>
      <div v-else class="config-hint">
        <el-text size="small" type="info">
          在子流程的结束节点中配置输出变量，此处自动展示。通过
          <code>nodes.{{ nodeKey }}.变量名</code>
          访问。
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

.sub-flow-hint {
  background: #ecf5ff;
  border-radius: 4px;
  padding: 8px;
}

.loop-vars-hint {
  background: #fdf6ec;
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.var-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #606266;
}

.loop-mappings {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.loop-mapping {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  background: #fafafa;
  border-radius: 6px;
  padding: 10px;
  border: 1px solid #ebeef5;
}

.loop-mapping .el-form {
  flex: 1;
}

.loop-mapping .el-button {
  margin-top: 2px;
  flex-shrink: 0;
}

.mapping-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.mapping-index {
  font-size: 12px;
  font-weight: 500;
  color: #409eff;
}

.config-hint {
  margin-top: 8px;
}

.output-preview {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.output-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 6px 8px;
  background: #f0f9eb;
  border-radius: 4px;
  word-break: break-all;
}

.output-path {
  color: #67c23a;
  font-family: monospace;
  font-size: 12px;
  word-break: break-all;
}

.output-source {
  color: #909399;
  font-size: 11px;
  word-break: break-all;
}
</style>
