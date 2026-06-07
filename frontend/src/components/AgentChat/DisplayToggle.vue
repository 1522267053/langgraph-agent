<script setup lang="ts">
import { View } from '@element-plus/icons-vue'

const autoScroll = defineModel<boolean>('autoScroll', { default: true })
const showThinking = defineModel<boolean>('showThinking', { default: true })
const showToolCalls = defineModel<boolean>('showToolCalls', { default: true })
</script>

<template>
  <el-popover placement="bottom-end" :width="150" trigger="click">
    <template #reference>
      <button class="header-action-btn">
        <el-icon :size="18"><View /></el-icon>
        <span>展示</span>
      </button>
    </template>
    <div class="display-toggle-menu">
      <div class="display-toggle-item" @click="autoScroll = !autoScroll">
        <el-checkbox :model-value="autoScroll" @click.prevent />
        <span>自动滚动</span>
      </div>
      <div class="display-toggle-item" @click="showThinking = !showThinking">
        <el-checkbox :model-value="showThinking" @click.prevent />
        <span>思考过程</span>
      </div>
      <div class="display-toggle-item" @click="showToolCalls = !showToolCalls">
        <el-checkbox :model-value="showToolCalls" @click.prevent />
        <span>工具调用</span>
      </div>
    </div>
  </el-popover>
</template>

<style scoped>
.header-action-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  background: none;
  border: none;
  color: #64748b;
  cursor: pointer;
  border-radius: 8px;
  transition: all 0.2s;
  font-size: 10px;
  gap: 2px;
}

.header-action-btn:hover {
  color: #2563eb;
  background: #f8fafc;
}

.display-toggle-menu {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.display-toggle-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  cursor: pointer;
  font-size: 13px;
  color: #334155;
}

.display-toggle-item:hover {
  color: #2563eb;
}
</style>
