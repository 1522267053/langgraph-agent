<script setup lang="ts">
import { ref, watch } from 'vue'
import type { QuestionConfig } from './types'

const props = defineProps<{
  config: QuestionConfig
  nodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: QuestionConfig): void
}>()

function cloneConfig(c: QuestionConfig): QuestionConfig {
  return { description: c.description || '' }
}

const localConfig = ref<QuestionConfig>(cloneConfig(props.config))

watch(
  () => props.config,
  newConfig => {
    localConfig.value = cloneConfig(newConfig)
  },
  { deep: true, immediate: true }
)

function updateConfig(): void {
  emit('update:config', { ...localConfig.value })
}
</script>

<template>
  <div class="question-config">
    <div class="config-section">
      <div class="section-title">问题反问节点配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="节点描述">
          <el-input
            v-model="localConfig.description"
            type="textarea"
            :rows="3"
            placeholder="注入到 LLM system_prompt：说明何时该调用 ask_user_question 工具（可选）"
            @blur="updateConfig"
          />
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          将此节点连接到LLM节点（使用"工具"连接点）后，LLM
          即可调用 ask_user_question 工具抛出 1-4 个结构化选项让用户选择。<br />
          同一LLM节点只允许连接一个 Question 节点（避免工具名冲突）。
          前端会自动追加 "Other" 选项支持自由输入。
        </el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';
</style>
