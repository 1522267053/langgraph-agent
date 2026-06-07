<script setup lang="ts">
import type { TodoItem } from '@/composables/useStreamingMessage'
import { Close } from '@element-plus/icons-vue'
import { computed } from 'vue'
import TodoList from '@/components/common/TodoList.vue'

const props = withDefaults(
  defineProps<{
    todos: TodoItem[]
    visible: boolean
  }>(),
  { visible: true }
)

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

const completedCount = computed(() => props.todos.filter(t => t.status === 'completed').length)

const totalCount = computed(() => props.todos.length)

const progressPercent = computed(() => {
  if (totalCount.value === 0) return 0
  return Math.round((completedCount.value / totalCount.value) * 100)
})

const inProgressCount = computed(() => props.todos.filter(t => t.status === 'in_progress').length)

function handleClose(): void {
  emit('update:visible', false)
}
</script>

<template>
  <transition name="slide-left">
    <div v-if="visible && todos.length > 0" class="todo-panel">
      <div class="todo-panel-header">
        <span class="todo-panel-title">任务计划</span>
        <el-button :icon="Close" link size="small" @click="handleClose" />
      </div>

      <div class="todo-panel-progress">
        <el-progress
          :percentage="progressPercent"
          :stroke-width="6"
          :show-text="false"
          status="success"
        />
        <div class="progress-stats">
          <span>{{ completedCount }}/{{ totalCount }} 完成</span>
          <span v-if="inProgressCount > 0" class="in-progress-count">
            {{ inProgressCount }} 进行中
          </span>
        </div>
      </div>

      <TodoList :items="todos" />
    </div>
  </transition>
</template>

<style scoped>
.todo-panel {
  width: 280px;
  border-left: 1px solid #e4e7ed;
  background: #fafbfc;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.todo-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #e4e7ed;
  background: #fff;
}

.todo-panel-title {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
}

.todo-panel-progress {
  padding: 12px 16px;
  border-bottom: 1px solid #ebeef5;
}

.progress-stats {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

.in-progress-count {
  color: #e6a23c;
}
</style>
