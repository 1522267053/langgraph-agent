<script setup lang="ts">
import type { TodoItem } from '@/types/segment'

withDefaults(
  defineProps<{
    items: TodoItem[]
    showPriority?: boolean
  }>(),
  { showPriority: true }
)
</script>

<template>
  <div class="todo-list">
    <div v-for="(item, idx) in items" :key="idx" :class="['todo-item', item.status]">
      <span class="todo-status-icon">
        <span v-if="item.status === 'completed'" class="icon-completed">&#10003;</span>
        <span v-else-if="item.status === 'in_progress'" class="icon-progress">&#9679;</span>
        <span v-else-if="item.status === 'cancelled'" class="icon-cancelled">&#10007;</span>
        <span v-else class="icon-pending" />
      </span>
      <span class="todo-content">{{ item.content }}</span>
      <el-tag
        v-if="showPriority && item.priority === 'high'"
        size="small"
        type="danger"
        class="todo-priority"
      >
        高
      </el-tag>
      <span
        v-else-if="showPriority && item.priority === 'medium'"
        class="priority-tag priority-medium"
      >
        中
      </span>
    </div>
  </div>
</template>

<style scoped>
.todo-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.todo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
  color: #606266;
}

.todo-item.completed .todo-content {
  text-decoration: line-through;
  color: #c0c4cc;
}

.todo-item.completed {
  opacity: 0.6;
}

.todo-item.cancelled .todo-content {
  text-decoration: line-through;
  color: #c0c4cc;
}

.todo-item.cancelled {
  opacity: 0.5;
}

.todo-item.in_progress .todo-content {
  color: #e6a23c;
  font-weight: 500;
}

.todo-status-icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
}

.icon-completed {
  color: #67c23a;
  font-weight: bold;
}

.icon-progress {
  color: #e6a23c;
}

.icon-cancelled {
  color: #c0c4cc;
}

.icon-pending {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 1.5px solid #c0c4cc;
  border-radius: 2px;
}

.todo-content {
  flex: 1;
  min-width: 0;
  word-break: break-word;
}

.todo-priority {
  flex-shrink: 0;
}

.priority-tag {
  flex-shrink: 0;
  padding: 2px 8px;
  background: #fffbeb;
  color: #d97706;
  font-size: 10px;
  font-weight: 700;
  border-radius: 4px;
  border: 1px solid #fde68a;
}
</style>
