<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { CopyDocument, Files, DocumentCopy, Delete } from '@element-plus/icons-vue'

defineProps<{
  x: number
  y: number
  kind: 'node' | 'edge' | 'pane'
}>()

const emit = defineEmits<{
  (e: 'copy'): void
  (e: 'paste'): void
  (e: 'duplicate'): void
  (e: 'delete'): void
  (e: 'close'): void
}>()

const root = ref<HTMLElement | null>(null)

function close() {
  emit('close')
}

function onMouseDown(e: MouseEvent) {
  if (root.value && !root.value.contains(e.target as Node)) {
    close()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    close()
  }
}

onMounted(() => {
  window.addEventListener('mousedown', onMouseDown)
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('mousedown', onMouseDown)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div
    ref="root"
    class="context-menu"
    :style="{ left: `${x}px`, top: `${y}px` }"
    @contextmenu.prevent
  >
    <template v-if="kind === 'node'">
      <div class="menu-item" @click="emit('copy')">
        <el-icon><CopyDocument /></el-icon>
        <span>复制</span>
      </div>
      <div class="menu-item" @click="emit('paste')">
        <el-icon><Files /></el-icon>
        <span>粘贴</span>
      </div>
      <div class="menu-item" @click="emit('duplicate')">
        <el-icon><DocumentCopy /></el-icon>
        <span>复制一份</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item danger" @click="emit('delete')">
        <el-icon><Delete /></el-icon>
        <span>删除</span>
      </div>
    </template>
    <template v-else-if="kind === 'edge'">
      <div class="menu-item danger" @click="emit('delete')">
        <el-icon><Delete /></el-icon>
        <span>删除</span>
      </div>
    </template>
    <template v-else>
      <div class="menu-item" @click="emit('paste')">
        <el-icon><Files /></el-icon>
        <span>粘贴</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.context-menu {
  position: fixed;
  z-index: 9999;
  min-width: 130px;
  padding: 4px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
  user-select: none;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  font-size: 13px;
  color: #303133;
  border-radius: 4px;
  cursor: pointer;
}

.menu-item:hover {
  background: #f5f7fa;
}

.menu-item.danger {
  color: #f56c6c;
}

.menu-item.danger:hover {
  background: #fef0f0;
}

.menu-divider {
  height: 1px;
  margin: 4px 8px;
  background: #e4e7ed;
}
</style>
