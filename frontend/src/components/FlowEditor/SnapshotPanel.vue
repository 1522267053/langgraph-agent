<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshLeft, Delete, Plus, Star, StarFilled, InfoFilled } from '@element-plus/icons-vue'
import { flowApi } from '@/api/flow'
import { useIsMobile } from '@/composables/useIsMobile'
import type { FlowSnapshot } from '@/types/flow'

const props = defineProps<{
  visible: boolean
  flowId: number | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void
  (e: 'restored'): void
}>()

const snapshots = ref<FlowSnapshot[]>([])
const loading = ref(false)
const showCreateDialog = ref(false)
const newName = ref('')
const newDescription = ref('')

const { isMobile } = useIsMobile()
const drawerSize = computed(() => (isMobile.value ? '100%' : '480px'))

async function loadSnapshots() {
  if (!props.flowId) return
  loading.value = true
  try {
    const res = await flowApi.listSnapshots(props.flowId)
    if (res.data.code === 1) {
      snapshots.value = res.data.data || []
    }
  } finally {
    loading.value = false
  }
}

watch(
  () => props.visible,
  val => {
    if (val && props.flowId) {
      loadSnapshots()
    }
  }
)

function handleClose() {
  emit('update:visible', false)
}

async function handleCreate() {
  if (!props.flowId || !newName.value.trim()) return
  try {
    const res = await flowApi.createSnapshot(props.flowId, {
      name: newName.value.trim(),
      description: newDescription.value.trim() || undefined
    })
    if (res.data.code === 1) {
      ElMessage.success('快照已创建')
      showCreateDialog.value = false
      newName.value = ''
      newDescription.value = ''
      await loadSnapshots()
    }
  } catch {
    // error handled by interceptor
  }
}

async function handleRestore(snapshot: FlowSnapshot) {
  try {
    await ElMessageBox.confirm(
      `恢复快照「${snapshot.snapshot_name}」将覆盖当前流程配置，是否继续？`,
      '确认恢复',
      {
        confirmButtonText: '确定恢复',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    const res = await flowApi.restoreSnapshot(snapshot.id!)
    if (res.data.code === 1) {
      ElMessage.success('恢复成功')
      emit('restored')
      handleClose()
    }
  } catch {
    // cancelled or error
  }
}

async function handleDelete(snapshot: FlowSnapshot) {
  try {
    await ElMessageBox.confirm(`确定删除快照「${snapshot.snapshot_name}」吗？`, '确认删除', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await flowApi.deleteSnapshot(snapshot.id!)
    ElMessage.success('删除成功')
    await loadSnapshots()
  } catch {
    // cancelled
  }
}

async function handleTogglePin(snapshot: FlowSnapshot) {
  await flowApi.pinSnapshot(snapshot.id!)
  await loadSnapshots()
}
</script>

<template>
  <el-drawer
    :model-value="visible"
    title="版本快照"
    direction="rtl"
    :size="drawerSize"
    @update:model-value="handleClose"
  >
    <div class="snapshot-panel">
      <div class="panel-header">
        <el-button :icon="Plus" type="primary" size="small" @click="showCreateDialog = true">
          创建快照
        </el-button>
        <el-button size="small" :loading="loading" @click="loadSnapshots">刷新</el-button>
      </div>

      <div v-loading="loading" class="snapshot-list">
        <div v-for="snapshot in snapshots" :key="snapshot.id" class="snapshot-item">
          <div class="snapshot-info">
            <div class="snapshot-name-row">
              <span class="snapshot-name">{{ snapshot.snapshot_name }}</span>
              <el-tag v-if="snapshot.snapshot_type === 'auto'" size="small" type="info">
                自动
              </el-tag>
              <el-tag v-else size="small" type="success">手动</el-tag>
              <el-icon
                v-if="snapshot.is_pinned === 1"
                class="pin-icon pinned"
                @click="handleTogglePin(snapshot)"
              >
                <StarFilled />
              </el-icon>
              <el-icon v-else class="pin-icon" @click="handleTogglePin(snapshot)">
                <Star />
              </el-icon>
            </div>
            <p v-if="snapshot.snapshot_description" class="snapshot-desc">
              {{ snapshot.snapshot_description }}
            </p>
            <span class="snapshot-time">{{ snapshot.create_time }}</span>
          </div>
          <div class="snapshot-actions">
            <el-button
              :icon="RefreshLeft"
              size="small"
              type="primary"
              text
              @click="handleRestore(snapshot)"
            >
              恢复
            </el-button>
            <el-button
              :icon="Delete"
              size="small"
              type="danger"
              text
              @click="handleDelete(snapshot)"
            />
          </div>
        </div>

        <el-empty
          v-if="!loading && snapshots.length === 0"
          description="暂无快照"
          :image-size="80"
        />
      </div>

      <div class="panel-tip">
        <el-icon><InfoFilled /></el-icon>
        <span>每次保存流程前自动创建快照，自动快照保留最近 20 个。手动创建的快照需手动删除。</span>
      </div>
    </div>

    <!-- 手动创建快照弹窗 -->
    <el-dialog v-model="showCreateDialog" title="创建快照" width="420px" append-to-body>
      <el-form label-position="top">
        <el-form-item label="快照名称">
          <el-input v-model="newName" placeholder="请输入快照名称" />
        </el-form-item>
        <el-form-item label="描述（可选）">
          <el-input v-model="newDescription" type="textarea" :rows="3" placeholder="快照描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="handleCreate">确定</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<style scoped>
.snapshot-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.panel-header {
  display: flex;
  gap: 8px;
  padding-bottom: 16px;
}

.snapshot-list {
  flex: 1;
  overflow-y: auto;
}

.snapshot-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin-bottom: 10px;
  transition: border-color 0.2s;
}

.snapshot-item:hover {
  border-color: #3b82f6;
}

.snapshot-info {
  flex: 1;
  min-width: 0;
}

.snapshot-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.snapshot-name {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pin-icon {
  cursor: pointer;
  color: #cbd5e1;
  font-size: 16px;
  transition: color 0.2s;
}

.pin-icon:hover {
  color: #f59e0b;
}

.pin-icon.pinned {
  color: #f59e0b;
}

.snapshot-desc {
  font-size: 12px;
  color: #64748b;
  margin: 6px 0 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.snapshot-time {
  font-size: 12px;
  color: #94a3b8;
}

.snapshot-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.panel-tip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px;
  margin-top: 12px;
  background: #f1f5f9;
  border-radius: 8px;
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
}
</style>
