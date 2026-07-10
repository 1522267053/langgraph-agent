<script setup lang="ts">
import { computed } from 'vue'
import { useToolOutputStore } from '@/stores/toolOutput'
import { useIsMobile } from '@/composables/useIsMobile'
import TaskOutputCard from '@/components/AgentChat/TaskOutputCard.vue'

const store = useToolOutputStore()
const { isMobile } = useIsMobile()

const drawerSize = computed(() => (isMobile.value ? '100%' : '500px'))

const visible = computed({
  get: () => store.drawerVisible,
  set: (v: boolean) => {
    if (!v) {
      store.closeDrawer()
    } else {
      store.drawerVisible = true
    }
  }
})

const tools = computed(() => {
  void store._reactivityTrigger.value
  return store.toolList
})
</script>

<template>
  <el-drawer v-model="visible" direction="rtl" :size="drawerSize">
    <template #header>
      <div class="drawer-header">
        <span>后台任务</span>
        <span v-if="store.runningCount > 0" class="drawer-count">
          {{ store.runningCount }} 个运行中
        </span>
      </div>
    </template>
    <div class="drawer-content">
      <div v-if="tools.length === 0" class="empty-state">暂无后台任务</div>
      <TaskOutputCard v-for="tool in tools" :key="tool.task_id" :task="tool" />
    </div>
  </el-drawer>
</template>

<style scoped>
.drawer-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  overflow-y: auto;
}

.drawer-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.drawer-count {
  font-size: 13px;
  color: #f59e0b;
  font-weight: 600;
}

.empty-state {
  text-align: center;
  color: #94a3b8;
  padding: 40px 0;
  font-size: 14px;
}
</style>
