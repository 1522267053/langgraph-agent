<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAgentStore } from '@/stores'
import { ElMessage, ElMessageBox, ElImageViewer } from 'element-plus'
import { Operation, Bottom, Notebook, Warning } from '@element-plus/icons-vue'
import { agentApi } from '@/api/agent'
import type { FlowIOField } from '@/types/flow'
import DisplayToggle from '@/components/AgentChat/DisplayToggle.vue'
import MemoryPanel from '@/components/AgentChat/MemoryPanel.vue'
import MessageItem from '@/components/AgentChat/MessageItem.vue'
import RunningToolBadge from '@/components/AgentChat/RunningToolBadge.vue'
import ToolOutputDrawer from '@/components/AgentChat/ToolOutputDrawer.vue'
import WelcomePage from '@/components/AgentChat/WelcomePage.vue'
import type { ImagePreviewData } from '@/components/common/FilePreviewer.vue'
import ChatInput from '@/components/AgentChat/ChatInput.vue'
import FlowPreviewCard from '@/components/common/FlowPreviewCard.vue'
import { useToolOutputStore } from '@/stores/toolOutput'
import { useAutoScroll } from '@/composables/useAutoScroll'

import 'highlight.js/styles/vs2015.css'

const route = useRoute()
const store = useAgentStore()
const toolOutputStore = useToolOutputStore()

const messagesContainer = ref<HTMLElement | null>(null)

const { autoScroll, isAtBottom, scrollToBottom, handleScroll, userScrolledUp } = useAutoScroll(messagesContainer, [
  () => store.chatMessages.length,
  () => store.textContent,
  () => store.thinkingContent,
  () => store.chatMessages.reduce((n, m) => n + m.segments.length, 0),
  () =>
    store.chatMessages.reduce(
      (n, m) =>
        n + m.segments.filter(s => s.type === 'tool' && s.tool?.status !== 'running').length,
      0
    ),
  () => store.isStreaming,
  () => store.todos.length
])

const humanInputValue = ref('')

const imagePreviewVisible = ref(false)
const imagePreviewUrl = ref('')
const imagePreviewUrls = ref<string[]>([])
const imagePreviewIndex = ref(0)

function handleImagePreview(data: ImagePreviewData) {
  imagePreviewUrl.value = data.url
  imagePreviewUrls.value = data.urls
  imagePreviewIndex.value = data.index
  imagePreviewVisible.value = true
}

function closeImagePreview() {
  imagePreviewVisible.value = false
}

function handlePreviewSwitch(index: number) {
  imagePreviewIndex.value = index
  imagePreviewUrl.value = imagePreviewUrls.value[index]
}

const agentId = ref<number | null>(null)

const isWelcomeMode = computed(
  () => !store.messagesLoading && store.chatMessages.length === 0
)

function handleSuggestedPrompt(prompt: string) {
  inputMessage.value = prompt
  handleChatSend({}, [], prompt)
  inputMessage.value = ''
}

const STORAGE_KEY = 'agent-chat-display'

function loadDisplayPrefs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const prefs = JSON.parse(raw)
      if (typeof prefs.autoScroll === 'boolean') autoScroll.value = prefs.autoScroll
      if (typeof prefs.showThinking === 'boolean') showThinking.value = prefs.showThinking
      if (typeof prefs.showToolCalls === 'boolean') showToolCalls.value = prefs.showToolCalls
    }
  } catch {
    // ignore
  }
}

function saveDisplayPrefs() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      autoScroll: autoScroll.value,
      showThinking: showThinking.value,
      showToolCalls: showToolCalls.value
    })
  )
}

const showThinking = ref(true)
const showToolCalls = ref(true)

loadDisplayPrefs()

watch([autoScroll, showThinking, showToolCalls], saveDisplayPrefs)

watch(
  () => route.params.id,
  async newId => {
    const id = newId ? parseInt(newId as string) : null
    if (id === agentId.value) return

    let targetId = id
    if (!targetId) {
      // 优先级：上次使用(需验证仍存在) > localStorage 默认 > 内置 Agent
      let resolved = false
      if (store.lastUsedAgentId) {
        if (store.agents.length === 0) await store.loadAgents()
        const exists = store.agents.some(a => a.id === store.lastUsedAgentId)
        if (exists) {
          targetId = store.lastUsedAgentId
          resolved = true
        } else {
          store.lastUsedAgentId = null
        }
      }
      if (!resolved) {
        const storedDefault = localStorage.getItem('default_agent_id')
        if (storedDefault) {
          targetId = parseInt(storedDefault)
        } else {
          if (store.agents.length === 0) await store.loadAgents()
          const builtin = store.agents.find((a: { is_builtin?: number }) => a.is_builtin === 1)
          targetId = builtin?.id ?? null
        }
      }
      if (!targetId) {
        agentId.value = null
        store.chatMessages = []
        store.currentSession = null
        return
      }
    }

    agentId.value = targetId
    store.cancelStream()
    store.sessionsLoading = true
    try {
      await store.loadAgent(targetId)
      store.lastUsedAgentId = targetId
      await store.loadSessions(targetId)
      if (store.sessions.length > 0) {
        await store.selectSession(targetId, store.sessions[0])
      } else {
        store.chatMessages = []
        store.currentSession = null
      }
    } finally {
      if (store.sessionsLoading) store.sessionsLoading = false
    }
  }
)

const isLoadingMore = ref(false)
const loadMoreSentinel = ref<HTMLElement | null>(null)
let loadMoreObserver: IntersectionObserver | null = null

const dynamicFields = computed<FlowIOField[]>(() => {
  const fields = store.currentAgent?.input_schema?.fields || []
  return fields.filter(f => f.name && f.name !== 'message')
})

const inputMessage = ref('')
const showMemory = ref(false)

onMounted(async () => {
  toolOutputStore.registerWsHandler()
  toolOutputStore.loadRunning()
  const id = route.params.id as string
  const sessionId = route.query.sessionId as string
  try {
    if (id) {
      agentId.value = parseInt(id)
    } else {
      // 优先级：上次使用(需验证仍存在) > localStorage 默认 > 内置 Agent
      let resolved = false
      if (store.lastUsedAgentId) {
        if (store.agents.length === 0) await store.loadAgents()
        const exists = store.agents.some(a => a.id === store.lastUsedAgentId)
        if (exists) {
          agentId.value = store.lastUsedAgentId
          resolved = true
        } else {
          // 上次使用的 Agent 已被删除，清除记忆
          store.lastUsedAgentId = null
        }
      }
      if (!resolved) {
        const storedDefault = localStorage.getItem('default_agent_id')
        if (storedDefault) {
          agentId.value = parseInt(storedDefault)
        } else {
          const res = await agentApi.list()
          const agents = res.data.data?.list || []
          const builtin = agents.find((a: { is_builtin?: number }) => a.is_builtin === 1)
          if (!builtin) {
            ElMessage.error('内置 Agent 不存在')
            return
          }
          agentId.value = builtin.id
        }
      }
    }
    store.sessionsLoading = true
    await store.loadAgent(agentId.value)
    store.lastUsedAgentId = agentId.value
    await store.loadSessions(agentId.value)
    if (sessionId) {
      const target = store.sessions.find(s => s.id === parseInt(sessionId))
      if (target) {
        await store.selectSession(agentId.value, target)
      } else {
        await store.selectSession(agentId.value, {
          id: parseInt(sessionId)
        } as (typeof store.sessions)[0])
      }
    } else if (store.sessions.length > 0) {
      const session = store.sessions[0]
      if (session) await store.selectSession(agentId.value, session)
    }
  } catch {
    // error handled by interceptor
  } finally {
    if (store.sessionsLoading) store.sessionsLoading = false
    await nextTick()
    initLoadMoreObserver()
  }
})

onUnmounted(() => {
  store.cancelStream()
  store.resetState()
  store.stopCompressPolling()
  store.stopSavePolling()
  toolOutputStore.stopPolling()
  toolOutputStore.unregisterWsHandler()
  loadMoreObserver?.disconnect()
  loadMoreObserver = null
})

watch(
  () => store.messagesLoading,
  async (loading, wasLoading) => {
    if (wasLoading && !loading) {
      await nextTick()
      initLoadMoreObserver()
    }
  }
)

// hasMoreMessages 从 false→true 时（如 onFlowDone 后 sentinel 重建），重新初始化观察器
watch(
  () => store.hasMoreMessages,
  async hasMore => {
    if (hasMore) {
      await nextTick()
      initLoadMoreObserver()
    }
  }
)

watch(
  () => store.currentSession?.id,
  newId => {
    store.stopCompressPolling()
    if (newId && agentId.value) {
      store.startCompressPolling(agentId.value, newId)
    }
  }
)

function initLoadMoreObserver() {
  if (!messagesContainer.value || !loadMoreSentinel.value) return
  loadMoreObserver?.disconnect()
  let firstCallback = true
  loadMoreObserver = new IntersectionObserver(
    entries => {
      // observe() 后的首个回调是元素当前状态，非用户滚动，跳过避免误触发
      if (firstCallback) {
        firstCallback = false
        return
      }
      // 非用户手动上滑且内容超出视口时跳过，避免布局变化导致误触发加载更多
      const canScroll =
        messagesContainer.value &&
        messagesContainer.value.scrollHeight > messagesContainer.value.clientHeight
      if (!userScrolledUp.value && canScroll) return
      if (
        entries[0].isIntersecting &&
        !isLoadingMore.value &&
        store.hasMoreMessages &&
        !store.messagesLoading
      ) {
        handleLoadMore()
      }
    },
    { root: messagesContainer.value }
  )
  loadMoreObserver.observe(loadMoreSentinel.value)
}

async function handleLoadMore() {
  if (!agentId.value || isLoadingMore.value) return
  isLoadingMore.value = true
  // 加载期间取消观察，避免重复触发
  if (loadMoreObserver && loadMoreSentinel.value) {
    loadMoreObserver.unobserve(loadMoreSentinel.value)
  }
  try {
    const prevScrollHeight = messagesContainer.value?.scrollHeight || 0
    await store.loadMoreMessages(agentId.value)
    await nextTick()
    const newHeight = messagesContainer.value?.scrollHeight || 0
    if (newHeight > prevScrollHeight && messagesContainer.value) {
      messagesContainer.value.scrollTop = newHeight - prevScrollHeight
    }
  } finally {
    isLoadingMore.value = false
    // 加载完毕后重新观察
    if (loadMoreObserver && loadMoreSentinel.value) {
      loadMoreObserver.observe(loadMoreSentinel.value)
    }
  }
}

async function handleChatSend(
  params: Record<string, unknown>,
  attachedFiles: Array<{ id: number; original_name: string; mime_type: string }>,
  message: string
) {
  if (!store.currentSession) {
    const session = await store.createSession(agentId.value!)
    if (!session) return
    await store.selectSession(agentId.value!, session)
  }
  store.sendMessage(message, params, attachedFiles)
  await nextTick()
  scrollToBottom()
}

function handleStop() {
  store.interruptExecution()
}

function handleHumanInputSubmit() {
  if (!humanInputValue.value.trim()) {
    ElMessage.warning('请输入内容')
    return
  }
  store.resumeWithInput(humanInputValue.value.trim())
  humanInputValue.value = ''
}

async function handleCompress() {
  if (!agentId.value || !store.currentSession) return
  if (store.isStreaming) {
    ElMessage.warning('请等待回复完成')
    return
  }
  if (store.chatMessages.length === 0) {
    ElMessage.warning('暂无对话记录')
    return
  }
  try {
    await ElMessageBox.confirm('将总结全部对话历史为摘要。此操作不可撤销。', '压缩上下文', {
      type: 'warning'
    })
  } catch {
    return
  }
  try {
    const started = await store.compressSession(agentId.value, store.currentSession.id)
    if (started) {
      ElMessage.info('正在压缩上下文...')
    }
  } catch {
    // store.compressSession handles error internally
  }
}

function handleDeleteMessage(msg: (typeof store.chatMessages)[0]) {
  if (!store.currentSession || !store.currentAgent) return
  if (store.isStreaming) {
    ElMessage.warning('请等待回复完成')
    return
  }
  const match = msg.id.match(/msg-(\d+)/)
  if (!match?.[1]) {
    ElMessage.warning('该消息不支持删除')
    return
  }
  const msgId = parseInt(match[1])

  ElMessageBox.confirm('删除此消息及之后的对话？', '确定', { type: 'warning' })
    .then(async () => {
      const deletedContent = await store.deleteMessagesFrom(msgId)
      if (deletedContent) {
        inputMessage.value = deletedContent
      }
      ElMessage.success('已删除，可重新发送')
    })
    .catch(() => {})
}

function handleRevertFrom(dbMsgId: number) {
  if (store.isStreaming) {
    ElMessage.warning('请等待回复完成')
    return
  }
  ElMessageBox.confirm('将删除此条及之后的所有内容，确定继续？', '确定', { type: 'warning' })
    .then(async () => {
      const deletedContent = await store.deleteMessagesFrom(dbMsgId)
      if (deletedContent) {
        inputMessage.value = deletedContent
      }
      ElMessage.success('已删除')
    })
    .catch(() => {})
}

function formatToolApprovalArgs(args?: Record<string, unknown>): string {
  if (!args) return ''
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return ''
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}:${s.toString().padStart(2, '0')}` : `${s}s`
}

function handleApproveTools() {
  store.approveToolCalls()
}

function handleRejectTools() {
  store.rejectToolCalls()
}
</script>

<template>
  <div class="chat-content" :class="{ 'welcome-mode': isWelcomeMode }">
    <template v-if="isWelcomeMode">
      <div class="welcome-wrapper">
        <WelcomePage
          :agent-name="store.currentAgent?.name || 'AI 助手'"
          :agent-description="store.currentAgent?.description"
          :suggested-prompts="store.currentAgent?.suggested_prompts || []"
          @select-prompt="handleSuggestedPrompt"
        />
        <div class="input-wrapper welcome-input">
          <ChatInput
            v-model:input-message="inputMessage"
            :fields="dynamicFields"
            :is-streaming="store.isStreaming"
            :is-stopping="store.isStopping"
            :is-waiting-human="false"
            :total-tokens="store.totalSessionTokens"
            :latest-prompt-tokens="store.latestPromptTokens"
            @send="handleChatSend"
            @stop="handleStop"
          />
        </div>
      </div>
    </template>

    <template v-else>
      <header class="chat-header glass-blur">
        <div class="header-center">
          <div class="status-dot"></div>
          <h1>{{ store.currentAgent?.name || 'AI 助手' }}</h1>
        </div>
        <div class="header-right">
          <DisplayToggle
            v-model:auto-scroll="autoScroll"
            v-model:show-thinking="showThinking"
            v-model:show-tool-calls="showToolCalls"
          />
          <el-tooltip content="记忆" placement="bottom">
            <button class="header-action-btn" @click="showMemory = true">
              <el-icon :size="18">
                <Notebook />
              </el-icon>
              <span>记忆</span>
            </button>
          </el-tooltip>
          <el-tooltip content="压缩" placement="bottom">
            <button class="header-action-btn" @click="handleCompress">
              <el-icon :size="18" :class="{ 'is-loading': store.isCompressing }">
                <Operation />
              </el-icon>
              <span>压缩</span>
            </button>
          </el-tooltip>
          <RunningToolBadge />
        </div>
      </header>

      <div
        ref="messagesContainer"
        v-loading="store.messagesLoading"
        element-loading-text="加载中..."
        class="messages-container"
        @scroll="handleScroll"
      >
        <div v-show="!store.messagesLoading">
          <div v-if="store.hasMoreMessages" ref="loadMoreSentinel" class="load-more-sentinel">
            <div v-show="isLoadingMore" class="load-more-dots">
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
          <MessageItem
            :messages="store.chatMessages"
            :show-thinking="showThinking"
            :show-tool-calls="showToolCalls"
            :is-streaming="store.isStreaming"
            @delete="handleDeleteMessage"
            @revert="handleRevertFrom"
            @preview="handleImagePreview"
          />
        </div>
      </div>

      <div :class="['scroll-to-bottom', { hidden: isAtBottom }]" @click="scrollToBottom">
        <el-icon :size="16">
          <Bottom />
        </el-icon>
      </div>

      <div v-if="store.isCompressing" class="compress-overlay">
        <div class="compress-overlay-card">
          <el-icon :size="24" class="is-loading">
            <Operation />
          </el-icon>
          <span>正在压缩上下文...</span>
        </div>
      </div>

      <div v-if="store.isWaitingHuman" class="human-input-overlay">
        <el-card class="human-input-card">
          <div class="human-input-question">
            <el-icon style="color: #e6a23c; margin-right: 8px">
              <ChatDotRound />
            </el-icon>
            {{ store.currentWaitData?.question || '请提供输入' }}
          </div>
          <div v-if="store.currentWaitData?.context" class="human-input-context">
            {{ store.currentWaitData.context }}
          </div>
          <el-input
            v-model="humanInputValue"
            type="textarea"
            :rows="3"
            placeholder="请输入您的回答..."
            @keydown.enter.ctrl="handleHumanInputSubmit"
          />
          <template #footer>
            <div style="display: flex; justify-content: space-between; width: 100%">
              <el-button :disabled="store.isStopping" @click="handleStop">取消执行</el-button>
              <el-button type="primary" @click="handleHumanInputSubmit">提交并继续</el-button>
            </div>
          </template>
        </el-card>
      </div>

      <div v-if="store.isWaitingToolApproval" class="tool-approval-overlay">
        <el-card class="tool-approval-card">
          <div class="approval-header">
            <el-icon style="color: #e6a23c; margin-right: 8px">
              <Warning />
            </el-icon>
            <span v-if="store.subAgentApproval?.isSubAgent">
              子Agent「{{ store.subAgentApproval.agentName }}」请求执行以下工具：
            </span>
            <span v-else>请求执行以下工具：</span>
            <span class="approval-countdown">{{ formatCountdown(store.approvalCountdown) }}</span>
          </div>
          <div class="approval-tools">
            <div
              v-for="tc in store.pendingToolCalls"
              :key="tc.id || tc.name"
              class="approval-tool-item"
            >
              <div class="approval-tool-name">
                <el-tag
                  :type="store.pendingApprovalNeeded.includes(tc.name) ? 'danger' : 'info'"
                  size="small"
                  style="margin-right: 6px"
                >
                  {{ store.pendingApprovalNeeded.includes(tc.name) ? '需确认' : '普通' }}
                </el-tag>
                {{ tc.name }}
              </div>
              <pre class="approval-tool-args">{{ formatToolApprovalArgs(tc.args) }}</pre>
            </div>
          </div>
          <template #footer>
            <div style="display: flex; justify-content: space-between; width: 100%">
              <el-button type="danger" @click="handleRejectTools">拒绝并停止</el-button>
              <el-button type="primary" @click="handleApproveTools">批准执行</el-button>
            </div>
          </template>
        </el-card>
      </div>

      <div v-if="store.flowPreview" class="flow-preview-wrapper">
        <FlowPreviewCard
          :flow-id="store.flowPreview.flow_id"
          :flow-name="store.flowPreview.flow_name"
          :nodes="store.flowPreview.nodes"
          :edges="store.flowPreview.edges"
          :deleted="store.flowPreview.deleted"
          @close="store.flowPreview = null"
        />
      </div>

      <div class="input-wrapper">
        <ChatInput
          v-model:input-message="inputMessage"
          :fields="dynamicFields"
          :is-streaming="store.isStreaming"
          :is-stopping="store.isStopping"
          :is-waiting-human="store.isWaitingHuman || store.isWaitingToolApproval"
          :total-tokens="store.totalSessionTokens"
          :latest-prompt-tokens="store.latestPromptTokens"
          @send="handleChatSend"
          @stop="handleStop"
        />
      </div>

      <MemoryPanel v-model:visible="showMemory" :agent-id="agentId" />
      <ToolOutputDrawer />

      <Teleport to="body">
        <el-image-viewer
          v-if="imagePreviewVisible"
          :url-list="imagePreviewUrls"
          :initial-index="imagePreviewIndex"
          @close="closeImagePreview"
          @switch="handlePreviewSwitch"
        />
      </Teleport>
    </template>
  </div>
</template>

<script lang="ts">
import { ChatDotRound } from '@element-plus/icons-vue'
export default {
  components: { ChatDotRound }
}
</script>

<style scoped>
.chat-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  position: relative;
  background: #fff;
}

.chat-content.welcome-mode {
  background: #fafbfc;
}

.welcome-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  overflow-y: auto;
  min-height: 0;
  padding-bottom: 24px;
}

.chat-header {
  height: 56px;
  flex-shrink: 0;
  padding: 0 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  z-index: 50;
}

.header-center {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-center h1 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: -0.01em;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10b981;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.5;
  }
}

.header-right {
  display: flex;
  align-items: center;
  gap: 2px;
}

.header-action-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  background: none;
  border: none;
  color: #64748b;
  cursor: pointer;
  border-radius: 8px;
  transition: all 0.2s;
  font-size: 10px;
  gap: 2px;
}

.header-action-btn:hover {
  color: #2563eb;
  background: #f8fafc;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 32px 24px;
  position: relative;
}

.empty-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
}

.scroll-to-bottom {
  position: absolute;
  bottom: 210px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 50;
}

.scroll-to-bottom:hover {
  background: #eff6ff;
  color: #2563eb;
}

.load-more-sentinel {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 32px;
  padding: 8px 0;
}

.load-more-dots {
  display: flex;
  gap: 6px;
}

.load-more-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #cbd5e1;
  animation: load-more-pulse 1.2s ease-in-out infinite;
}

.load-more-dots span:nth-child(2) {
  animation-delay: 0.2s;
}

.load-more-dots span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes load-more-pulse {
  0%,
  80%,
  100% {
    opacity: 0.3;
    transform: scale(0.8);
  }

  40% {
    opacity: 1;
    transform: scale(1.2);
  }
}

.compress-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255, 255, 255, 0.8);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 99;
}

.compress-overlay-card {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  color: #334155;
  background: #fff;
  padding: 16px 28px;
  border-radius: 12px;
  box-shadow:
    0 2px 15px -3px rgba(0, 0, 0, 0.07),
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
}

.human-input-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 100;
}

.human-input-card {
  width: 500px;
  max-width: 90%;
}

.human-input-card :deep(.el-card__body) {
  padding-bottom: 0;
}

.human-input-question {
  font-size: 16px;
  font-weight: 500;
  margin-bottom: 16px;
  display: flex;
  align-items: flex-start;
}

.human-input-context {
  background: #f8fafc;
  padding: 12px;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 14px;
  white-space: pre-wrap;
  color: #475569;
  max-height: 200px;
  overflow-y: auto;
}

.human-input-card :deep(.el-card__footer) {
  padding-top: 16px;
  text-align: right;
}

.input-wrapper {
  flex-shrink: 0;
  border-top: 1px solid #f1f5f9;
  background: rgba(255, 255, 255, 0.5);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  padding: 24px 24px 16px;
  position: relative;
  z-index: 40;
}

.input-wrapper::before {
  content: '';
  position: absolute;
  bottom: -10px;
  left: 50%;
  transform: translateX(-50%);
  width: 75%;
  height: 40px;
  background: rgba(37, 99, 235, 0.06);
  filter: blur(40px);
  border-radius: 9999px;
  pointer-events: none;
}

.welcome-input {
  border-top: none;
  background: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  max-width: 640px;
  width: 100%;
  margin: 0 auto;
  padding-top: 8px;
}

.welcome-input::before {
  display: none;
}

.tool-approval-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 100;
}

.tool-approval-card {
  width: 560px;
  max-width: 90%;
  max-height: 80vh;
}

.tool-approval-card :deep(.el-card__body) {
  padding-bottom: 0;
  overflow-y: auto;
  max-height: calc(80vh - 120px);
}

.approval-header {
  font-size: 16px;
  font-weight: 500;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
}

.approval-countdown {
  margin-left: auto;
  font-size: 13px;
  color: #94a3b8;
  font-weight: 400;
}

.approval-tools {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.approval-tool-item {
  background: #f8fafc;
  border-radius: 8px;
  padding: 12px;
}

.approval-tool-name {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
}

.approval-tool-args {
  font-size: 12px;
  color: #475569;
  background: #1e293b;
  color: #e2e8f0;
  padding: 10px 12px;
  border-radius: 6px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}

.tool-approval-card :deep(.el-card__footer) {
  padding-top: 16px;
  text-align: right;
}

.flow-preview-wrapper {
  flex-shrink: 0;
  padding: 5px 24px;
  max-height: 250px;
  overflow: hidden;
}

@media (max-width: 768px) {
  .messages-container {
    padding: 20px 16px;
  }

  .input-wrapper {
    padding: 16px;
  }

  .header-center h1 {
    font-size: 14px;
  }

  .welcome-input {
    padding: 8px 16px 16px;
  }
}
</style>
