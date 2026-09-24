<script setup lang="ts">
import type { WaitConfig } from './types'
import { useConfigBase } from '@/composables/useConfigBase'

const props = defineProps<{
  config: WaitConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: WaitConfig): void
}>()

const { localConfig, updateConfig } = useConfigBase(() => props.config, emit)
</script>

<template>
  <div class="wait-config">
    <div class="config-section">
      <div class="section-title">延时设置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="等待时间">
          <el-input-number
            v-model="localConfig.wait_seconds"
            :min="0"
            :max="3600"
            :step="1"
            style="width: 100%"
            @change="updateConfig"
          />
        </el-form-item>
        <el-form-item>
          <el-text size="small" type="info">
            等待指定秒数后继续执行后续节点（0-3600 秒）。适用于宽限期等待、
            重试退避、流程节奏控制。
          </el-text>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';
</style>
