<script setup lang="ts">
import type { MemoryConfig } from './types'
import { useConfigBase } from '@/composables/useConfigBase'

const props = defineProps<{
  config: MemoryConfig
  nodeId: string
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: MemoryConfig): void
}>()

const { localConfig, updateConfig } = useConfigBase(
  () => props.config,
  (_e, v) => emit('update:config', v)
)

const categoryOptions = [
  { value: 'decision', label: '决策' },
  { value: 'preference', label: '偏好' },
  { value: 'lesson', label: '教训' },
  { value: 'relation', label: '关系' },
  { value: 'event', label: '事件' },
  { value: 'task', label: '任务' },
  { value: 'profile', label: '用户资料' },
  { value: 'knowledge', label: '知识' },
  { value: 'instruction', label: '指令' },
  { value: 'other', label: '其他' }
]
</script>

<template>
  <div class="memory-config">
    <div class="config-section">
      <div class="section-title">工具默认值</div>
      <el-form label-width="90px" size="small">
        <el-form-item label="默认分类">
          <el-select
            v-model="localConfig.default_category"
            style="width: 100%"
            @change="updateConfig"
          >
            <el-option
              v-for="item in categoryOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="默认重要度">
          <el-input-number
            v-model="localConfig.default_importance"
            :min="1"
            :max="5"
            @change="updateConfig"
          />
        </el-form-item>
        <el-form-item label="最大返回数">
          <el-input-number
            v-model="localConfig.max_results"
            :min="1"
            :max="20"
            @change="updateConfig"
          />
          <span class="unit-label">条</span>
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          当LLM调用记忆工具时未指定参数，将使用以上默认值。
          <br />
          重要程度≥5的记忆会自动标记为热记忆（常驻索引），3-4为温记忆，1-2为冷记忆。
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">热记忆索引</div>
      <el-form label-width="90px" size="small">
        <el-form-item label="最大行数">
          <el-input-number
            v-model="localConfig.max_index_lines"
            :min="20"
            :max="500"
            @change="updateConfig"
          />
          <span class="unit-label">行</span>
        </el-form-item>
        <el-form-item label="最大字节">
          <el-input-number
            v-model="localConfig.max_index_bytes"
            :min="5000"
            :max="50000"
            :step="1000"
            @change="updateConfig"
          />
          <span class="unit-label">B</span>
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          热记忆索引每次对话都会注入到LLM上下文中，仅包含指针摘要（标题+重要程度）。
          <br />
          超出限制时自动截断并提示LLM使用搜索工具获取完整记忆。
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">自动管理</div>
      <el-form label-width="90px" size="small">
        <el-form-item label="升温阈值">
          <el-input-number
            v-model="localConfig.auto_promote_threshold"
            :min="1"
            :max="20"
            @change="updateConfig"
          />
          <span class="unit-label">次</span>
        </el-form-item>
        <el-form-item label="整理阈值">
          <el-input-number
            v-model="localConfig.consolidate_threshold"
            :min="10"
            :max="200"
            @change="updateConfig"
          />
          <span class="unit-label">条</span>
        </el-form-item>
        <el-form-item label="整理间隔">
          <el-input-number
            v-model="localConfig.consolidate_interval_days"
            :min="0"
            :max="90"
            @change="updateConfig"
          />
          <span class="unit-label">天</span>
        </el-form-item>
        <el-form-item label="热记忆衰减">
          <el-input-number
            v-model="localConfig.hot_decay_days"
            :min="7"
            :max="365"
            @change="updateConfig"
          />
          <span class="unit-label">天</span>
        </el-form-item>
        <el-form-item label="温记忆衰减">
          <el-input-number
            v-model="localConfig.warm_decay_days"
            :min="14"
            :max="365"
            @change="updateConfig"
          />
          <span class="unit-label">天</span>
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          记忆被搜索命中时访问计数+1，达到升温阈值后自动升温（cold→warm→hot）。
          <br />
          热记忆超过整理阈值时，保存新热记忆会自动触发 AI 总结整理，合并重复、压缩冗余。
          <br />
          久未访问的记忆会自动降温（hot→warm→cold），重要程度越高衰减越慢。
          <br />
          整理间隔设为 0 表示仅按数量触发整理，不按时间触发。
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="config-hint">
        <el-text size="small" type="info">
          记忆节点需通过工具连接到LLM节点使用。
          <br />
          LLM将获得 memory_save、memory_search、memory_list、memory_delete 工具。
          <br />
          温度管理完全自动（升温+降温+整理），无需LLM手动干预。
        </el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';
</style>
