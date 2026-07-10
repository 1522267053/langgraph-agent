<script setup lang="ts">
import { computed } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { useToolOutputStore } from '@/stores/toolOutput'

const store = useToolOutputStore()

const visible = computed(() => store.toolList.length > 0)
const count = computed(() => store.runningCount)

function handleClick() {
  store.drawerVisible = true
}
</script>

<template>
  <button
    v-show="visible"
    class="header-action-btn running-tool-badge"
    :class="{ pulsing: count > 0 }"
    @click="handleClick"
  >
    <el-icon :size="18" :class="{ 'is-loading': count > 0 }">
      <Loading />
    </el-icon>
    <span>后台({{ count }})</span>
  </button>
</template>

<style scoped>
.running-tool-badge {
  position: relative;
}

.pulsing::after {
  content: '';
  position: absolute;
  top: 8px;
  right: 10px;
  width: 6px;
  height: 6px;
  background: #f59e0b;
  border-radius: 50%;
  animation: pulse-dot 1.5s ease-in-out infinite;
}

@keyframes pulse-dot {
  0%,
  100% {
    opacity: 0.4;
    transform: scale(0.8);
  }
  50% {
    opacity: 1;
    transform: scale(1.2);
  }
}
</style>
