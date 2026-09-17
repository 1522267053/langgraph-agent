<script setup lang="ts">
/**
 * Python 允许导入模块展示条（可折叠）
 *
 * 数据源：GET /api/debug/python/allowed-modules（后端 ALLOWED_MODULES 单一事实源）
 * 使用位置：
 * - PythonConfig.vue：Python代码配置区块下方
 * - PythonDebugPanel.vue：试运行面板底部（编辑 + 试运行 dialog 右栏）
 */
import { ref, onMounted } from 'vue'
import { debugApi } from '@/api/debug'

const modules = ref<string[]>([])
const collapsed = ref(true)

onMounted(async () => {
  try {
    const res = await debugApi.getAllowedModules()
    if (res.data.code === 1 && Array.isArray(res.data.data)) {
      modules.value = res.data.data
    }
  } catch {
    // 拉取失败静默降级：不显示该区块内容
  }
})
</script>

<template>
  <div class="allowed-modules">
    <div class="modules-header" @click="collapsed = !collapsed">
      <el-text size="small" type="info" class="modules-toggle">
        允许导入的模块（{{ modules.length }}）{{ collapsed ? ' ▸' : ' ▾' }}
      </el-text>
    </div>
    <div v-if="!collapsed && modules.length > 0" class="modules-tags">
      <el-tag v-for="m in modules" :key="m" size="small" type="info" class="module-tag">
        {{ m }}
      </el-tag>
    </div>
  </div>
</template>

<style scoped>
.allowed-modules {
  margin-top: 8px;
}
.modules-header {
  cursor: pointer;
  user-select: none;
}
.modules-toggle:hover {
  color: var(--el-color-primary);
}
.modules-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.module-tag {
  font-family: 'Consolas', 'Monaco', monospace;
}
</style>
