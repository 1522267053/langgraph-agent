<template>
  <!--
    文件变更 Diff 面板（侧栏/抽屉）
    - 列表：当前会话所有未回退的文件变更（按文件路径聚合）
    - 详情：选中一条时显示 backup → current 的 line-level diff
    - 操作：单条「撤销此变更」按钮（已撤销项置灰）
  -->
  <div class="file-change-panel">
    <div class="panel-header">
      <span class="title">
        <el-icon><Document /></el-icon>
        文件变更
        <span v-if="fileChanges.length > 0" class="badge">{{ fileChanges.length }}</span>
      </span>
      <el-button
        link
        :icon="Refresh"
        :loading="fileChangesLoading"
        title="刷新"
        @click="refresh"
      />
    </div>

    <div v-if="fileChanges.length === 0" class="panel-empty">
      <el-empty description="当前会话暂无文件变更" :image-size="80" />
    </div>

    <ul v-else class="change-list">
      <li
        v-for="item in fileChanges"
        :key="item.id"
        :class="['change-item', { active: activeFileChangeId === item.id, reverted: item.is_reverted }]"
        @click="onItemClick(item)"
      >
        <div class="change-item-main">
          <el-tag
            :type="tagType(item.change_type)"
            size="small"
            effect="plain"
            class="change-type-tag"
          >
            {{ changeTypeLabel(item.change_type) }}
          </el-tag>
          <span class="path" :title="item.file_path">{{ basename(item.file_path) }}</span>
          <el-tooltip :content="item.tool_name" placement="top">
            <span class="tool">{{ item.tool_name }}</span>
          </el-tooltip>
        </div>
        <div class="change-item-meta">
          <span class="time">{{ formatTime(item.create_time) }}</span>
          <el-button
            v-if="!item.is_reverted && activeFileChangeId === item.id"
            type="danger"
            link
            size="small"
            :loading="reverting"
            @click.stop="onRevert(item)"
          >
            撤销此变更
          </el-button>
          <el-tag v-else-if="item.is_reverted" size="small" type="info">已撤销</el-tag>
        </div>
      </li>
    </ul>

    <!-- Diff 详情抽屉/弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="80%"
      top="5vh"
      destroy-on-close
      append-to-body
      class="file-diff-dialog"
      @close="onClose"
    >
      <div class="dialog-diff-body">
        <div v-if="activeFileChangeDiffLoading" class="loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>加载 diff 中…</span>
        </div>
        <DiffViewer
          v-else-if="activeFileChangeDiff"
          :backup-content="activeFileChangeDiff.backup_content"
          :current-content="activeFileChangeDiff.current_content"
          :is-binary="activeFileChangeDiff.is_binary"
          :backup-missing="activeFileChangeDiff.backup_missing"
          :change-type="activeFileChangeDiff.change_type"
        />
        <div v-else class="loading">
          <el-empty description="暂无 diff 数据" :image-size="60" />
        </div>
      </div>
      <template #footer>
        <el-button @click="dialogVisible = false">关闭</el-button>
        <el-button
          v-if="activeItem && !activeItem.is_reverted"
          type="danger"
          :loading="reverting"
          @click="onRevert(activeItem)"
        >
          撤销此变更
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { Document, Refresh, Loading } from '@element-plus/icons-vue'
import { useAgentStore } from '@/stores/agentOptimized'
import type { AgentFileChangeBase } from '@/types/agent'
import DiffViewer from './DiffViewer.vue'

const store = useAgentStore()
const {
  fileChanges,
  fileChangesLoading,
  activeFileChangeId,
  activeFileChangeDiff,
  activeFileChangeDiffLoading
} = storeToRefs(store)

const dialogVisible = ref(false)
const reverting = ref(false)

const activeItem = computed(() =>
  fileChanges.value.find(c => c.id === activeFileChangeId.value) || null
)

const dialogTitle = computed(() => {
  if (!activeItem.value) return '文件 Diff'
  const tag = changeTypeLabel(activeItem.value.change_type)
  return `[${tag}] ${basename(activeItem.value.file_path)}`
})

watch(activeFileChangeId, (val) => {
  if (val != null) dialogVisible.value = true
})

function onClose() {
  store.closeFileChangeDiff()
}

function refresh() {
  store.fetchFileChanges()
}

function onItemClick(item: AgentFileChangeBase) {
  if (item.is_reverted) {
    // 已撤销的项允许查看但不可操作
    store.openFileChangeDiff(item.id)
    return
  }
  store.openFileChangeDiff(item.id)
}

async function onRevert(item: AgentFileChangeBase) {
  reverting.value = true
  try {
    await store.revertFileChangeById(item.id)
  } finally {
    reverting.value = false
  }
}

function basename(p: string): string {
  if (!p) return ''
  const idx = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'))
  return idx >= 0 ? p.slice(idx + 1) : p
}

function changeTypeLabel(t: string): string {
  if (t === 'create') return '新建'
  if (t === 'modify') return '修改'
  if (t === 'delete') return '删除'
  return t
}

function tagType(t: string): 'success' | 'warning' | 'danger' | 'info' {
  if (t === 'create') return 'success'
  if (t === 'modify') return 'warning'
  if (t === 'delete') return 'danger'
  return 'info'
}

function formatTime(iso?: string | null): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    if (diffMs < 60_000) return '刚刚'
    if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)} 分钟前`
    if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)} 小时前`
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ''
  }
}
</script>

<style scoped lang="scss">
.file-change-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--el-bg-color);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);

  .title {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .badge {
    background: var(--el-color-primary-light-9);
    color: var(--el-color-primary);
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 999px;
  }
}

.panel-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.change-list {
  flex: 1;
  list-style: none;
  margin: 0;
  padding: 4px 0;
  overflow-y: auto;
}

.change-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  cursor: pointer;
  border-bottom: 1px dashed var(--el-border-color-lighter);
  transition: background 0.15s ease;

  &:hover {
    background: var(--el-fill-color-light);
  }

  &.active {
    background: var(--el-color-primary-light-9);
  }

  &.reverted {
    opacity: 0.55;
  }

  .change-item-main {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    min-width: 0;

    .change-type-tag {
      flex-shrink: 0;
    }

    .path {
      font-family: ui-monospace, Menlo, Consolas, monospace;
      font-size: 12.5px;
      color: var(--el-text-color-regular);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex: 1;
      min-width: 0;
    }

    .tool {
      flex-shrink: 0;
      font-size: 11px;
      color: var(--el-text-color-secondary);
      padding: 1px 6px;
      background: var(--el-fill-color);
      border-radius: 3px;
    }
  }

  .change-item-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    margin-left: 8px;

    .time {
      font-size: 11px;
      color: var(--el-text-color-placeholder);
    }
  }
}

.loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 0;
  color: var(--el-text-color-secondary);
}

/* Diff 弹窗：限制高度 + body 内部滚动，避免长 diff 撑爆视口 */
.file-diff-dialog :deep(.el-dialog__body) {
  /* 减去 header 与 footer 高度后的可用空间；
     用 flex 1 让 .dialog-diff-body 占满剩余空间，再内部滚动 */
  display: flex;
  flex-direction: column;
  padding: 12px 20px;
  min-height: 0;
}

.dialog-diff-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* 定位祖先：d2h 行号是 position:absolute，包含块必须落在滚动容器内，
     否则行号不受裁剪/不占滚动高度，会飘到弹窗外面（diff2html#381） */
  position: relative;
  /* 让 DiffViewer 内部高度不撑开 body */
  display: flex;
  flex-direction: column;
  height: 75vh;
}

.dialog-diff-body > :deep(.diff-viewer) {
  flex-shrink: 0;
}
</style>