<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Plus, ChatDotRound, Delete, Search } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { useAgentStore } from '@/stores'

const props = withDefaults(
  defineProps<{
    agentId: number | null
    showBack?: boolean
  }>(),
  { showBack: true }
)

const emit = defineEmits<{
  (e: 'session-selected'): void
}>()

const store = useAgentStore()
const router = useRouter()
const searchKeyword = ref('')

async function handleNewSession(): Promise<void> {
  if (!props.agentId) return
  const session = await store.createSession(props.agentId)
  if (session) {
    await store.selectSession(props.agentId, session)
    emit('session-selected')
  }
}

async function handleSelectSession(session: (typeof store.sessions)[0]): Promise<void> {
  if (!props.agentId) return
  await store.selectSession(props.agentId, session)
  emit('session-selected')
}

async function handleDeleteSession(session: (typeof store.sessions)[0]): Promise<void> {
  if (!props.agentId) return

  try {
    await ElMessageBox.confirm('确定删除该会话吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await store.deleteSession(props.agentId, session.id)
  } catch {
    // cancelled
  }
}

function handlePageChange(page: number): void {
  if (!props.agentId) return
  store.loadSessions(props.agentId, page)
}

function handleBack(): void {
  store.resetState()
  router.back()
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <template v-if="showBack">
        <a class="back-link" @click="handleBack">
          <el-icon class="back-icon"><ArrowLeft /></el-icon>
          <span>返回</span>
        </a>
        <div class="header-divider"></div>
      </template>
      <button class="new-session-btn" @click="handleNewSession">
        <el-icon><Plus /></el-icon>
        <span>新建会话</span>
      </button>
    </div>

    <div class="search-box">
      <el-icon class="search-icon"><Search /></el-icon>
      <input
        v-model="searchKeyword"
        class="search-input"
        placeholder="搜索历史对话..."
        type="text"
      />
    </div>

    <div class="session-list">
      <div
        v-for="session in store.sessions"
        :key="session.id"
        :class="['session-item', { active: store.currentSession?.id === session.id }]"
        @click="handleSelectSession(session)"
      >
        <el-icon class="session-icon">
          <ChatDotRound />
        </el-icon>
        <div class="session-info">
          <div class="session-title">{{ session.title || '新会话' }}</div>
          <div class="session-time">{{ session.create_time || '' }}</div>
        </div>
        <el-button
          :icon="Delete"
          link
          size="small"
          class="delete-btn"
          @click.stop="handleDeleteSession(session)"
        />
      </div>
    </div>

    <div v-if="store.sessionTotal > store.sessionPageSize" class="session-pagination">
      <el-pagination
        v-model:current-page="store.sessionPage"
        :page-size="store.sessionPageSize"
        :total="store.sessionTotal"
        layout="prev, pager, next"
        size="small"
        @current-change="handlePageChange"
      />
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 256px;
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  padding: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.back-link {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #64748b;
  text-decoration: none;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: color 0.2s;
}

.back-link:hover {
  color: #1e293b;
}

.back-icon {
  font-size: 18px;
}

.header-divider {
  width: 1px;
  height: 16px;
  background: #e2e8f0;
}

.new-session-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  background: #2563eb;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(37, 99, 235, 0.3);
  transition: all 0.2s;
}

.new-session-btn:hover {
  background: #1d4ed8;
}

.search-box {
  position: relative;
  padding: 0 12px 8px;
}

.search-icon {
  position: absolute;
  left: 24px;
  top: 50%;
  transform: translateY(-50%);
  color: #94a3b8;
  font-size: 14px;
}

.search-input {
  width: 100%;
  padding: 8px 12px 8px 32px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 12px;
  color: #334155;
  outline: none;
  transition: all 0.2s;
}

.search-input::placeholder {
  color: #94a3b8;
}

.search-input:focus {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.08);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
}

.session-pagination {
  padding: 8px;
  border-top: 1px solid #f1f5f9;
  display: flex;
  justify-content: center;
}

.session-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px 12px;
  cursor: pointer;
  border-left: 3px solid transparent;
  transition: all 0.2s;
}

.session-item:hover {
  background: #f8fafc;
}

.session-item.active {
  background: #f1f5f9;
  border-left-color: #3b82f6;
}

.session-icon {
  font-size: 18px;
  color: #94a3b8;
  margin-top: 1px;
  flex-shrink: 0;
}

.session-item.active .session-icon {
  color: #3b82f6;
}

.session-item:hover .session-icon {
  color: #3b82f6;
}

.session-info {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.session-title {
  font-size: 12px;
  font-weight: 600;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-item:not(.active) .session-title {
  font-weight: 500;
  color: #334155;
}

.session-time {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.delete-btn {
  opacity: 0;
  transition: opacity 0.2s;
  color: #94a3b8 !important;
  flex-shrink: 0;
  margin-top: 1px;
}

.session-item:hover .delete-btn {
  opacity: 1;
}
</style>
