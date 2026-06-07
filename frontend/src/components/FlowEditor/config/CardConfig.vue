<script setup lang="ts">
import { ref, watch } from 'vue'
import type { CardConfig } from './types'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: CardConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: CardConfig): void
}>()

function cloneConfig(c: CardConfig): CardConfig {
  return {
    ...c,
    input_mappings: [...(c.input_mappings ?? [])],
    output_mappings: [...(c.output_mappings ?? [])]
  }
}

const localConfig = ref<CardConfig>(cloneConfig(props.config))

watch(
  () => props.config,
  newConfig => {
    localConfig.value = cloneConfig(newConfig)
  },
  { deep: true, immediate: true }
)

watch(
  localConfig,
  newValue => {
    emit('update:config', cloneConfig(newValue))
  },
  { deep: true }
)
</script>

<template>
  <div class="card-config">
    <div class="config-section">
      <div class="section-title">引用流程</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="流程ID">
          <el-input :value="localConfig.ref_flow_id" disabled />
        </el-form-item>
      </el-form>
    </div>

    <div v-if="localConfig.input_schema?.fields?.length" class="config-section">
      <div class="section-title">输入参数映射</div>
      <div class="card-mappings">
        <div
          v-for="(mapping, index) in localConfig.input_mappings"
          :key="index"
          class="card-mapping"
        >
          <div class="mapping-header">
            <span class="mapping-index">{{ mapping.card_field }}</span>
            <el-tag size="small" type="info">
              {{ localConfig.input_schema?.fields?.[index]?.type || 'string' }}
            </el-tag>
          </div>
          <el-form label-width="60px" size="small">
            <el-form-item label="来源">
              <VariableSelector
                v-model="mapping.source"
                :current-node-id="currentNodeId"
                placeholder="选择变量来源"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">使用下拉选择器选择变量来源</el-text>
      </div>
    </div>

    <!-- <div v-if="localConfig.output_schema?.fields?.length" class="config-section">
      <div class="section-title">输出参数映射</div>
      <div class="card-mappings">
        <div
          v-for="(mapping, index) in localConfig.output_mappings"
          :key="index"
          class="card-mapping"
        >
          <div class="mapping-header">
            <span class="mapping-index">{{ mapping.card_field }}</span>
            <el-tag size="small" type="success">
              {{ localConfig.output_schema?.fields?.[index]?.type || 'string' }}
            </el-tag>
          </div>
          <el-form label-width="60px" size="small">
            <el-form-item label="输出到">
              <VariableSelector
                v-model="mapping.target_variable"
                :current-node-id="currentNodeId"
                :output-mode="true"
                placeholder="选择输出目标变量（可选）"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">
          可选择输出目标变量；留空则通过 nodes.卡片key.字段名 访问
        </el-text>
      </div>
    </div> -->

    <div class="config-section">
      <div class="section-title">参数预览</div>
      <div class="card-preview">
        <div
          v-if="
            !localConfig.input_schema?.fields?.length && !localConfig.output_schema?.fields?.length
          "
          class="preview-empty"
        >
          <el-text size="small" type="info">该能力卡片未定义输入输出参数</el-text>
        </div>
        <template v-else>
          <div v-if="localConfig.input_schema?.fields?.length" class="preview-group">
            <div class="preview-label">输入:</div>
            <el-tag
              v-for="f in localConfig.input_schema.fields"
              :key="f.name"
              size="small"
              style="margin-right: 4px; margin-bottom: 4px"
            >
              {{ f.name }}
            </el-tag>
          </div>
          <div v-if="localConfig.output_schema?.fields?.length" class="preview-group">
            <div class="preview-label">输出(通过 nodes.卡片key.字段名 访问):</div>
            <el-tag
              v-for="f in localConfig.output_schema.fields"
              :key="f.name"
              size="small"
              type="success"
              style="margin-right: 4px; margin-bottom: 4px"
            >
              {{ f.name }}
            </el-tag>
          </div>
        </template>
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
  margin-top: 12px;
  padding: 8px;
  background: #fdf6ec;
  border-radius: 4px;
}

.card-mappings {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.card-mapping {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
}

.mapping-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.mapping-index {
  font-size: 12px;
  font-weight: 500;
  color: #409eff;
}

.card-preview {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
}

.preview-empty {
  text-align: center;
}

.preview-group {
  margin-bottom: 8px;
}

.preview-group:last-child {
  margin-bottom: 0;
}

.preview-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}
</style>
