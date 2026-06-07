<script setup lang="ts">
/**
 * 思考过程显示组件
 * @description 用于显示LLM的思考过程（reasoning/thinking）
 */
import { ref, computed } from 'vue'
import { CaretRight, View, Hide } from '@element-plus/icons-vue'

const props = defineProps<{
  /** 思考内容 */
  content: string
  /** 是否默认展开 */
  defaultExpanded?: boolean
  /** 最大高度（px） */
  maxHeight?: number
}>()

/** 是否展开 */
const isExpanded = ref(props.defaultExpanded ?? false)

/** 切换展开状态 */
function toggleExpanded() {
  isExpanded.value = !isExpanded.value
}

/** 内容样式 */
const contentStyle = computed(() => ({
  maxHeight: props.maxHeight ? `${props.maxHeight}px` : '400px'
}))
</script>

<template>
  <div class="thinking-block">
    <!-- 头部 -->
    <div class="thinking-header" @click="toggleExpanded">
      <el-icon class="toggle-icon" :class="{ 'is-expanded': isExpanded }">
        <CaretRight />
      </el-icon>
      <span class="thinking-label">思考过程</span>
      <el-icon class="visibility-icon">
        <component :is="isExpanded ? Hide : View" />
      </el-icon>
    </div>

    <!-- 内容区域 -->
    <el-collapse-transition>
      <div v-show="isExpanded" class="thinking-content" :style="contentStyle">
        <pre>{{ content }}</pre>
      </div>
    </el-collapse-transition>
  </div>
</template>

<style scoped lang="scss">
.thinking-block {
  background: linear-gradient(135deg, #f5f7fa 0%, #f0f2f5 100%);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  margin: 8px 0;
  overflow: hidden;
}

.thinking-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s;

  &:hover {
    background-color: var(--el-fill-color);
  }
}

.toggle-icon {
  transition: transform 0.3s;

  &.is-expanded {
    transform: rotate(90deg);
  }
}

.thinking-label {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
}

.visibility-icon {
  color: var(--el-text-color-placeholder);
}

.thinking-content {
  padding: 0 12px 12px;
  overflow-y: auto;

  pre {
    margin: 0;
    font-size: 13px;
    line-height: 1.6;
    color: var(--el-text-color-regular);
    white-space: pre-wrap;
    word-break: break-word;
    font-family: inherit;
  }
}
</style>
