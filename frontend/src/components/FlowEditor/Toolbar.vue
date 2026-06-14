<script setup lang="ts">
import { computed } from 'vue'
import { ArrowDown, ArrowLeft, CaretRight, FolderChecked, VideoPlay } from '@element-plus/icons-vue'
import { useFlowStore } from '@/stores/flowStore'
import { useRouter, useRoute } from 'vue-router'

const props = defineProps<{
  isAgent?: boolean
}>()

const store = useFlowStore()
const router = useRouter()
const route = useRoute()

const emit = defineEmits<{
  (e: 'save'): void
  (e: 'execute'): void
  (e: 'showHistory'): void
  (e: 'showSnapshot'): void
}>()

const statusLabel = computed(() => {
  const status = store.flowInfo?.status
  if (status === 1) return '已发布'
  if (status === 2) return '已停用'
  return '草稿'
})

const statusClass = computed(() => {
  const status = store.flowInfo?.status
  if (status === 1) return 'status-published-tag'
  return 'status-draft-tag'
})

const lastSaveTime = computed(() => {
  if (!store.flowInfo?.modify_time) return ''
  return String(store.flowInfo.modify_time).replace('T', ' ').substring(0, 16)
})

function handleSave() {
  emit('save')
}

function handleExecute() {
  emit('execute')
}

function handleShowHistory() {
  emit('showHistory')
}

function handleShowSnapshot() {
  emit('showSnapshot')
}

function handleBack() {
  router.push('/flow')
}

function handleOpenFiles() {
  const base = route.path.startsWith('/agent') ? '/agent' : '/flow'
  if (store.flowInfo?.id) {
    router.push(`${base}/files/${store.flowInfo.id}`)
  }
}

function handleMobileCommand(command: string) {
  switch (command) {
    case 'save':
      handleSave()
      break
    case 'execute':
      handleExecute()
      break
    case 'history':
      handleShowHistory()
      break
    case 'snapshot':
      handleShowSnapshot()
      break
    case 'files':
      handleOpenFiles()
      break
  }
}
</script>

<template>
  <header class="toolbar glass-panel">
    <div class="toolbar-left">
      <a class="back-link" @click="handleBack">
        <el-icon size="18"><ArrowLeft /></el-icon>
        <span>返回列表</span>
      </a>
      <div class="divider"></div>
      <div class="flow-info">
        <div class="flow-icon">
          <el-icon size="18"><VideoPlay /></el-icon>
        </div>
        <div class="flow-meta">
          <div class="flow-title-row">
            <span class="flow-name">
              {{ store.flowInfo?.name || (props.isAgent ? '未命名智能体' : '未命名流程') }}
            </span>
            <span v-if="store.flowInfo?.id" class="flow-status-tag" :class="statusClass">
              {{ statusLabel }}
            </span>
          </div>
          <p v-if="store.flowInfo?.id" class="flow-subtitle">
            ID: {{ store.flowInfo.id }} | 最后保存: {{ lastSaveTime }}
          </p>
        </div>
      </div>
    </div>
    <div class="toolbar-right">
      <div class="desktop-actions">
        <div v-if="store.flowInfo?.id" class="toolbar-tab-group">
          <button class="tab-btn" @click="handleShowHistory">执行历史</button>
          <button class="tab-btn" @click="handleShowSnapshot">版本快照</button>
          <button class="tab-btn" @click="handleOpenFiles">
            {{ props.isAgent ? '智能体文件资源' : '流程文件资源' }}
          </button>
        </div>
        <div class="toolbar-actions">
          <el-button class="action-save" :loading="store.saving" @click="handleSave">
            <el-icon class="el-icon--left"><FolderChecked /></el-icon>
            保存
          </el-button>
          <button class="action-execute" @click="handleExecute">
            <el-icon size="18"><CaretRight /></el-icon>
            {{ props.isAgent ? '执行智能体' : '执行流程' }}
          </button>
        </div>
      </div>
      <el-dropdown
        v-if="store.flowInfo?.id"
        class="mobile-actions"
        trigger="click"
        placement="bottom-end"
        @command="handleMobileCommand"
      >
        <button class="mobile-more-btn">
          <span>操作</span>
          <el-icon size="14"><ArrowDown /></el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="save" :icon="FolderChecked">保存</el-dropdown-item>
            <el-dropdown-item command="execute" :icon="CaretRight">
              {{ props.isAgent ? '执行智能体' : '执行流程' }}
            </el-dropdown-item>
            <el-dropdown-item command="history" divided>执行历史</el-dropdown-item>
            <el-dropdown-item command="snapshot">版本快照</el-dropdown-item>
            <el-dropdown-item command="files">
              {{ props.isAgent ? '智能体文件资源' : '流程文件资源' }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<style scoped>
.toolbar {
  height: 64px;
  flex-shrink: 0;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  z-index: 50;
  border-bottom: 1px solid #e2e8f0;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 24px;
}

.back-link {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #64748b;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  text-decoration: none;
  transition: color 0.2s;
}

.back-link:hover {
  color: #0f172a;
}

.back-link:hover .el-icon {
  transform: translateX(-2px);
}

.back-link .el-icon {
  transition: transform 0.2s;
}

.divider {
  width: 1px;
  height: 24px;
  background: #e2e8f0;
}

.flow-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.flow-icon {
  width: 32px;
  height: 32px;
  background: #2563eb;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  box-shadow: 0 4px 6px -1px rgba(37, 99, 235, 0.3);
}

.flow-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.flow-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.flow-name {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
}

.flow-status-tag {
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 700;
  border-radius: 4px;
  line-height: 1.4;
}

.status-draft-tag {
  background: #fffbeb;
  color: #d97706;
  border: 1px solid #fef3c7;
}

.status-published-tag {
  background: #ecfdf5;
  color: #059669;
  border: 1px solid #d1fae5;
}

.flow-subtitle {
  font-size: 10px;
  color: #94a3b8;
  font-family: 'Courier New', monospace;
  margin: 0;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.toolbar-tab-group {
  display: flex;
  align-items: center;
  background: #f1f5f9;
  border-radius: 8px;
  padding: 4px;
}

.tab-btn {
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 500;
  color: #475569;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  transition: color 0.2s;
}

.tab-btn:hover {
  color: #2563eb;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.desktop-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.mobile-actions {
  display: none;
}

.mobile-more-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  background: #2563eb;
  border: none;
  border-radius: 10px;
  cursor: pointer;
  outline: none;
  box-shadow: 0 4px 14px -3px rgba(37, 99, 235, 0.4);
}

.mobile-more-btn:active {
  transform: scale(0.96);
}

.action-save {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px !important;
  height: 40px !important;
  font-size: 14px !important;
  font-weight: 700 !important;
  color: #334155 !important;
  background: #fff;
  border: 1px solid #e2e8f0 !important;
  border-radius: 12px !important;
  cursor: pointer;
  transition: all 0.2s;
  box-shadow: 0 4px 14px -3px rgba(0, 0, 0, 0.06);
}

.action-save:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
  transform: translateY(-1px);
}

.action-save:active {
  transform: translateY(0);
}

.action-execute {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px;
  font-size: 14px;
  font-weight: 700;
  color: #fff;
  background: #2563eb;
  border: none;
  border-radius: 12px;
  cursor: pointer;
  box-shadow: 0 4px 14px -3px rgba(37, 99, 235, 0.4);
  transition: all 0.2s;
}

.action-execute:hover {
  background: #1d4ed8;
  transform: translateY(-1px);
  box-shadow: 0 6px 20px -3px rgba(37, 99, 235, 0.5);
}

.action-execute:active {
  transform: translateY(0);
}

@media (max-width: 768px) {
  .toolbar {
    height: 52px;
    padding: 0 12px;
  }

  .back-link span {
    display: none;
  }

  .toolbar-left {
    gap: 12px;
  }

  .divider {
    display: none;
  }

  .flow-icon {
    width: 28px;
    height: 28px;
  }

  .flow-subtitle {
    display: none;
  }

  .toolbar-right {
    gap: 8px;
  }

  .desktop-actions {
    display: none;
  }

  .mobile-actions {
    display: block;
  }
}
</style>
