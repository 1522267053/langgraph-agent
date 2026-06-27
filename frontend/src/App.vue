<script setup lang="ts">
import { ref, computed, watch, provide, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Setting,
  DataLine,
  FolderOpened,
  Connection,
  List,
  Folder,
  Timer,
  Menu,
  ChatDotRound,
  Shop,
  Plus,
  Search,
  Delete,
  Star,
  StarFilled,
  SwitchButton,
  User,
  TrendCharts,
  ChatLineSquare,
  Calendar
} from '@element-plus/icons-vue'
import { ElMessageBox, ElMessage, ElNotification, ElButton } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useAgentStore } from '@/stores'
import { authApi } from '@/api/auth'
import { configApi, type UpdateCheckResult } from '@/api/config'
import { connectWebSocket } from '@/composables/useWebSocket'
import { agentApi } from '@/api/agent'
import { agendaApi } from '@/api/agenda'
import { requestPermission as requestNotifyPermission, isDenied, isPywebview } from '@/composables/useBrowserNotification'

const route = useRoute()
const router = useRouter()
const store = useAgentStore()

// ---- 路由切换加载动画 ----
const routeLoading = ref(false)

router.beforeEach((to, from, next) => {
  if (to.path !== from.path) {
    routeLoading.value = true
  }
  next()
})

router.afterEach(() => {
  routeLoading.value = false
})

const DEFAULT_AGENT_KEY = 'default_agent_id'

const builtinAgentId = ref<number | null>(null)
const defaultAgentId = ref<number | null>(null)
const currentUser = ref<string | null>(null)
const forceUpgradeInfo = ref<UpdateCheckResult | null>(null)
provide('forceUpgradeInfo', forceUpgradeInfo)

function loadDefaultAgentId(): number | null {
  const val = localStorage.getItem(DEFAULT_AGENT_KEY)
  return val ? parseInt(val) : null
}

async function loadCurrentUser(): Promise<void> {
  try {
    const res = await authApi.check()
    currentUser.value = res.data.data?.username ?? null
  } catch {
    currentUser.value = null
  }
}

async function checkAppUpdate(): Promise<void> {
  try {
    const res = await configApi.checkUpdate()
    const data = res.data.data
    if (data?.force_upgrade && data.has_update) {
      forceUpgradeInfo.value = data
    } else {
      forceUpgradeInfo.value = null
    }
  } catch {
    // 静默失败
  }
}

let updateChecked = false
let notifyPermissionRequested = false
let incompleteNotified = false

/** 进入应用时查询未完成日程并弹窗提示（每会话仅一次） */
async function checkIncompleteAgendas(): Promise<void> {
  try {
    const res = await agendaApi.tabCounts()
    const count = res.data.data?.incomplete ?? 0
    if (count > 0) {
      const instance = ElNotification({
        type: 'warning',
        title: '未完成日程提醒',
        message: h('div', { style: 'display:flex; align-items:center; gap:12px' }, [
          h('span', `您有 ${count > 99 ? '99+' : count} 项未完成的日程`),
          h(
            ElButton,
            {
              type: 'primary',
              size: 'small',
              onClick: () => {
                instance.close()
                window.location.hash = '#/agenda?tab=incomplete'
              }
            },
            () => '查看'
          )
        ]),
        duration: 0,
        position: 'top-right'
      })
    }
  } catch {
    // 静默失败（未登录等）
  }
}

function handleAppClick() {
  if (isPywebview()) return
  if (notifyPermissionRequested) return
  notifyPermissionRequested = true
  requestNotifyPermission().then(granted => {
    if (!granted && isDenied()) {
      ElMessage.warning('浏览器通知权限已被拒绝，请在浏览器设置中允许通知')
    }
  })
}

// 初始化 WebSocket 连接（全局通知）
connectWebSocket()

async function handleLogout(): Promise<void> {
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await authApi.logout()
    currentUser.value = null
    localStorage.removeItem('auto_login')
    localStorage.removeItem('saved_password_hash')
    localStorage.removeItem('saved_password')
    localStorage.removeItem('saved_username')
    window.location.hash = '#/login'
  } catch {
    // cancelled
  }
}

watch(
  () => route.path,
  path => {
    if (/^\/chat/.test(path)) {
      store
        .loadAgents()
        .then(() => {
          const builtin = store.agents.find((a: { is_builtin?: number }) => a.is_builtin === 1)
          builtinAgentId.value = builtin?.id ?? null
          defaultAgentId.value = loadDefaultAgentId()
        })
        .catch(() => {})
    }
    loadCurrentUser()
    if (!updateChecked) {
      updateChecked = true
      checkAppUpdate()
    }
    // 进入应用时提示未完成日程（每会话仅一次，排除 login/setup 等公开页）
    if (!incompleteNotified && !route.meta?.public) {
      incompleteNotified = true
      checkIncompleteAgendas()
    }
  },
  { immediate: true }
)

const isEditorPage = ref(true)

watch(
  () => route.name,
  name => {
    if (!name) return // 路由未解析，保持默认值 true，避免闪烁
    isEditorPage.value = [
      'FlowCreate',
      'FlowEdit',
      'AgentCreate',
      'AgentEdit',
      'SetupWizard',
      'Login'
    ].includes(name as string)
  },
  { immediate: true }
)

const isChatPage = computed(() => route.path === '/chat' || /^\/chat\/\d+/.test(route.path))

const chatAgentId = computed(() => {
  if (route.params.id) {
    return parseInt(route.params.id as string)
  }
  // 优先级：localStorage 默认 > 内置 Agent
  if (defaultAgentId.value) return defaultAgentId.value
  return builtinAgentId.value
})

const sidebarVisible = ref(false)
const searchKeyword = ref('')

// ---- 对话历史搜索 ----
interface SearchResultSession {
  id: number
  title: string
  create_time: string
}
interface SearchResultMessage {
  id: number
  session_id: number
  session_title: string
  role: string
  content_preview: string
  create_time: string
}
const searchResults = ref<{
  sessions: SearchResultSession[]
  messages: SearchResultMessage[]
}>({ sessions: [], messages: [] })
const searching = ref(false)

let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(searchKeyword, val => {
  if (searchTimer) clearTimeout(searchTimer)
  if (!val.trim() || !chatAgentId.value) {
    searchResults.value = { sessions: [], messages: [] }
    return
  }
  searchTimer = setTimeout(async () => {
    searching.value = true
    try {
      const res = await agentApi.search(chatAgentId.value!, val.trim())
      searchResults.value = res.data.data || { sessions: [], messages: [] }
    } catch {
      searchResults.value = { sessions: [], messages: [] }
    } finally {
      searching.value = false
    }
  }, 300)
})

const isSearchMode = computed(() => searchKeyword.value.trim().length > 0)

async function handleSearchResultClick(sessionId: number) {
  if (!chatAgentId.value) return
  const session = store.sessions.find(s => s.id === sessionId)
  if (session) {
    await handleSelectSession(session)
  } else {
    // 会话不在当前页，手动切换
    const fakeSession = {
      id: sessionId,
      title: '',
      flow_id: chatAgentId.value
    } as (typeof store.sessions)[0]
    await store.selectSession(chatAgentId.value, fakeSession)
    sidebarVisible.value = false
  }
  searchKeyword.value = ''
}

const agentList = computed(() => store.agents.filter(a => a.flow_type === 'agent'))

const selectedAgentId = computed({
  get: () => chatAgentId.value,
  set: (val: number | null) => {
    if (!val) return
    // 有默认 agent 时所有选择都走 /chat/{id}，否则内置走 /chat
    if (val === builtinAgentId.value && !defaultAgentId.value) {
      router.push('/chat')
    } else {
      router.push(`/chat/${val}`)
    }
  }
})

function toggleDefaultAgent(agentId: number) {
  if (defaultAgentId.value === agentId) {
    localStorage.removeItem(DEFAULT_AGENT_KEY)
    defaultAgentId.value = null
    // 取消默认后若当前在 /chat（无 id），跳回内置 agent
    if (!route.params.id) {
      router.push('/chat')
    }
  } else {
    localStorage.setItem(DEFAULT_AGENT_KEY, String(agentId))
    defaultAgentId.value = agentId
    // 设为默认后自动跳转到该 agent
    if (!route.params.id || parseInt(route.params.id as string) !== agentId) {
      router.push(`/chat/${agentId}`)
    }
  }
}

const menuItems = [
  { path: '/chat', title: 'AI 助手', icon: ChatDotRound },
  { path: '/flow', title: '流程和智能体', icon: DataLine },
  { path: '/knowledge', title: '知识库', icon: FolderOpened },
  { path: '/mcp-server', title: 'MCP 服务器', icon: Connection },
  { path: '/skill-list', title: 'Skill 管理', icon: List },
  { path: '/execution', title: '执行记录', icon: Timer },
  { path: '/statistics', title: 'Token 统计', icon: TrendCharts },
  { path: '/scheduled-task', title: '定时任务', icon: Timer },
  { path: '/agenda', title: '日程管理', icon: Calendar },
  { path: '/webhook', title: 'Webhook', icon: Connection },
  { path: '/files', title: '文件管理', icon: Folder },
  { path: '/marketplace', title: '资源市场', icon: Shop },
  { path: '/settings', title: '系统设置', icon: Setting }
]

const activeIndex = computed(() => {
  if (/^\/chat\/\d+/.test(route.path)) return '/chat'
  return route.path
})

function navigateTo(path: string) {
  sidebarVisible.value = false
  if (route.path !== path) {
    router.push(path)
  }
}

async function handleNewSession(): Promise<void> {
  if (!chatAgentId.value) return
  const session = await store.createSession(chatAgentId.value)
  if (session) {
    await store.selectSession(chatAgentId.value, session)
  }
}

async function handleSelectSession(session: (typeof store.sessions)[0]): Promise<void> {
  if (!chatAgentId.value) return
  await store.selectSession(chatAgentId.value, session)
  sidebarVisible.value = false
}

async function handleDeleteSession(session: (typeof store.sessions)[0]): Promise<void> {
  if (!chatAgentId.value) return
  try {
    await ElMessageBox.confirm('确定删除该会话吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await store.deleteSession(chatAgentId.value, session.id)
  } catch {
    // cancelled
  }
}

function handleSessionPageChange(page: number): void {
  if (!chatAgentId.value) return
  store.loadSessions(chatAgentId.value, page)
}

function openDownloadUrl(): void {
  if (forceUpgradeInfo.value?.download_url) {
    window.open(forceUpgradeInfo.value.download_url, '_blank')
  }
}
</script>

<template>
  <el-config-provider :locale="zhCn">
    <div class="app-container" @click="handleAppClick">
      <div v-if="forceUpgradeInfo" class="force-upgrade-banner">
        <span class="banner-text">
          发现新版本 {{ forceUpgradeInfo.latest_version }}，当前版本过低，请尽快升级
        </span>
        <el-button type="warning" size="small" @click="openDownloadUrl">前往下载</el-button>
      </div>
      <div class="app-body">
        <template v-if="!isEditorPage">
          <aside class="app-sidebar">
            <div class="sidebar-logo">
              <div class="logo-icon">
                <img src="/logo.ico" alt="logo" />
              </div>
              <span class="logo-text">AI Agent OS</span>
            </div>

            <template v-if="isChatPage">
              <div v-if="agentList.length > 0" class="agent-selector">
                <el-select
                  v-model="selectedAgentId"
                  size="small"
                  class="agent-select"
                  placeholder="选择 Agent"
                >
                  <el-option
                    v-for="agent in agentList"
                    :key="agent.id"
                    :label="agent.name"
                    :value="agent.id"
                  >
                    <div class="agent-option">
                      <span class="agent-option-name">{{ agent.name }}</span>
                      <el-icon
                        :class="['agent-star-btn', { active: defaultAgentId === agent.id }]"
                        @click.stop.prevent="toggleDefaultAgent(agent.id)"
                      >
                        <StarFilled v-if="defaultAgentId === agent.id" />
                        <Star v-else />
                      </el-icon>
                    </div>
                  </el-option>
                </el-select>
              </div>

              <template v-if="chatAgentId">
                <div class="session-actions">
                  <button class="new-session-btn" @click="handleNewSession">
                    <el-icon><Plus /></el-icon>
                    <span>新建会话</span>
                  </button>
                </div>
              </template>

              <div class="session-search">
                <el-icon class="search-icon"><Search /></el-icon>
                <input
                  v-model="searchKeyword"
                  class="search-input"
                  placeholder="搜索历史对话..."
                  type="text"
                />
              </div>

              <div class="session-list">
                <!-- 搜索结果 -->
                <template v-if="isSearchMode">
                  <div v-if="searching" class="search-loading">
                    <span>搜索中...</span>
                  </div>
                  <template v-else>
                    <div
                      v-if="
                        searchResults.sessions.length === 0 && searchResults.messages.length === 0
                      "
                      class="search-empty"
                    >
                      未找到匹配结果
                    </div>
                    <div
                      v-for="s in searchResults.sessions"
                      :key="'s' + s.id"
                      class="session-item search-result-item"
                      @click="handleSearchResultClick(s.id)"
                    >
                      <el-icon class="session-icon"><ChatDotRound /></el-icon>
                      <div class="session-info">
                        <div class="session-title">{{ s.title || '新会话' }}</div>
                        <div class="session-time">{{ s.create_time }}</div>
                      </div>
                    </div>
                    <div
                      v-for="m in searchResults.messages"
                      :key="'m' + m.id"
                      class="message-result-item"
                      @click="handleSearchResultClick(m.session_id)"
                    >
                      <el-icon class="message-result-icon"><ChatLineSquare /></el-icon>
                      <div class="message-result-info">
                        <div class="message-result-session">{{ m.session_title }}</div>
                        <div class="message-result-content">{{ m.content_preview }}</div>
                      </div>
                    </div>
                  </template>
                </template>
                <!-- 正常会话列表 -->
                <template v-else>
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
                </template>
              </div>

              <div v-if="store.sessionTotal > store.sessionPageSize" class="session-pagination">
                <el-pagination
                  v-model:current-page="store.sessionPage"
                  :page-size="store.sessionPageSize"
                  :total="store.sessionTotal"
                  layout="prev, pager, next"
                  size="small"
                  @current-change="handleSessionPageChange"
                />
              </div>
            </template>

            <nav class="sidebar-nav">
              <a
                v-for="item in menuItems"
                :key="item.path"
                :class="['nav-item', { active: activeIndex === item.path }]"
                @click="navigateTo(item.path)"
              >
                <el-icon :size="16"><component :is="item.icon" /></el-icon>
                <span>{{ item.title }}</span>
              </a>
            </nav>

            <div v-if="currentUser" class="sidebar-user">
              <div class="user-info">
                <el-icon :size="14" class="user-avatar"><User /></el-icon>
                <span class="user-name">{{ currentUser }}</span>
              </div>
              <button class="logout-btn" @click="handleLogout">
                <el-icon :size="14"><SwitchButton /></el-icon>
              </button>
            </div>
          </aside>

          <main class="app-main">
            <div v-if="routeLoading" class="route-loading-bar"></div>
            <div class="mobile-header">
              <el-icon class="mobile-menu-btn" :size="20" @click="sidebarVisible = true">
                <Menu />
              </el-icon>
              <span class="mobile-title">{{ route.meta?.title || 'AI Agent OS' }}</span>
            </div>
            <router-view />
          </main>

          <el-drawer
            v-model="sidebarVisible"
            direction="ltr"
            size="280px"
            :show-close="false"
            :with-header="false"
            class="mobile-sidebar-drawer"
          >
            <div class="mobile-drawer-logo">
              <div class="logo-icon">
                <img src="/logo.ico" alt="logo" />
              </div>
              <span class="logo-text">AI Agent OS</span>
            </div>

            <template v-if="isChatPage">
              <div v-if="agentList.length > 0" class="agent-selector">
                <el-select
                  v-model="selectedAgentId"
                  size="small"
                  class="agent-select"
                  placeholder="选择 Agent"
                >
                  <el-option
                    v-for="agent in agentList"
                    :key="agent.id"
                    :label="agent.name"
                    :value="agent.id"
                  >
                    <div class="agent-option">
                      <span class="agent-option-name">{{ agent.name }}</span>
                      <el-icon
                        :class="['agent-star-btn', { active: defaultAgentId === agent.id }]"
                        @click.stop.prevent="toggleDefaultAgent(agent.id)"
                      >
                        <StarFilled v-if="defaultAgentId === agent.id" />
                        <Star v-else />
                      </el-icon>
                    </div>
                  </el-option>
                </el-select>
              </div>

              <template v-if="chatAgentId">
                <div class="drawer-session-actions">
                  <button class="new-session-btn" @click="handleNewSession">
                    <el-icon><Plus /></el-icon>
                    <span>新建会话</span>
                  </button>
                </div>

                <div class="drawer-session-search">
                  <el-icon class="search-icon"><Search /></el-icon>
                  <input
                    v-model="searchKeyword"
                    class="search-input"
                    placeholder="搜索历史对话..."
                    type="text"
                  />
                </div>

                <div class="drawer-session-list">
                  <div
                    v-for="session in store.sessions"
                    :key="session.id"
                    :class="[
                      'drawer-session-item',
                      { active: store.currentSession?.id === session.id }
                    ]"
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

                <div
                  v-if="store.sessionTotal > store.sessionPageSize"
                  class="drawer-session-pagination"
                >
                  <el-pagination
                    v-model:current-page="store.sessionPage"
                    :page-size="store.sessionPageSize"
                    :total="store.sessionTotal"
                    layout="prev, pager, next"
                    size="small"
                    @current-change="handleSessionPageChange"
                  />
                </div>
              </template>
            </template>

            <nav :class="['drawer-nav', { 'drawer-nav-full': !isChatPage }]">
              <a
                v-for="item in menuItems"
                :key="item.path"
                :class="['drawer-nav-item', { active: activeIndex === item.path }]"
                @click="navigateTo(item.path)"
              >
                <el-icon :size="16"><component :is="item.icon" /></el-icon>
                <span>{{ item.title }}</span>
              </a>
            </nav>

            <div v-if="currentUser" class="drawer-user">
              <div class="drawer-user-info">
                <el-icon :size="14" class="user-avatar"><User /></el-icon>
                <span class="drawer-user-name">{{ currentUser }}</span>
              </div>
              <button class="drawer-logout-btn" @click="handleLogout">
                <el-icon :size="14"><SwitchButton /></el-icon>
              </button>
            </div>
          </el-drawer>
        </template>

        <template v-else>
          <div v-if="routeLoading" class="route-loading-bar"></div>
          <router-view class="app-main-full" />
        </template>
      </div>
    </div>
  </el-config-provider>
</template>

<style scoped>
.app-container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: #f1f5f9;
}

.route-loading-bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #3b82f6, #60a5fa, #93c5fd, #60a5fa, #3b82f6);
  background-size: 200% 100%;
  animation: route-loading-slide 1s ease-in-out infinite;
  z-index: 9999;
  pointer-events: none;
}

@keyframes route-loading-slide {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

.app-body {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.force-upgrade-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 8px 16px;
  background: #dc2626;
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  flex-shrink: 0;
  z-index: 9999;
}

.banner-text {
  line-height: 1.4;
}

.app-main-full {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

/* ---- Sidebar ---- */
.app-sidebar {
  width: 260px;
  flex-shrink: 0;
  background: #1a1a2e;
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
}

.logo-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  overflow: hidden;
  flex-shrink: 0;
}

.logo-icon img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.logo-text {
  font-size: 15px;
  font-weight: 700;
  color: #e2e8f0;
  letter-spacing: -0.01em;
}

/* ---- Agent Selector ---- */
.agent-selector {
  padding: 12px 12px 0;
  flex-shrink: 0;
}

.agent-select {
  width: 100%;
}

.agent-select :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.1) inset;
}

.agent-select :deep(.el-input__inner) {
  color: #cbd5e1;
}

.agent-select :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #60a5fa inset;
}

.agent-select :deep(.el-input__wrapper.is-focus) {
  box-shadow:
    0 0 0 1px #60a5fa inset,
    0 0 0 3px rgba(96, 165, 250, 0.12);
}

.agent-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.agent-option-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-star-btn {
  color: #64748b;
  cursor: pointer;
  font-size: 14px;
  flex-shrink: 0;
  margin-left: 8px;
  transition: color 0.2s;
}

.agent-star-btn:hover {
  color: #fbbf24;
}

.agent-star-btn.active {
  color: #fbbf24;
}

/* ---- Session Management ---- */
.session-actions {
  padding: 12px 12px 0;
  flex-shrink: 0;
}

.new-session-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  justify-content: center;
  padding: 7px 12px;
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(59, 130, 246, 0.4);
  transition: all 0.2s;
}

.new-session-btn:hover {
  background: #2563eb;
}

.session-search {
  position: relative;
  padding: 8px 12px;
  flex-shrink: 0;
}

.search-icon {
  position: absolute;
  left: 24px;
  top: 50%;
  transform: translateY(-50%);
  color: #64748b;
  font-size: 14px;
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 8px 12px 8px 32px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  font-size: 12px;
  color: #cbd5e1;
  outline: none;
  transition: all 0.2s;
}

.search-input::placeholder {
  color: #64748b;
}

.search-input:focus {
  border-color: #60a5fa;
  box-shadow: 0 0 0 2px rgba(96, 165, 250, 0.1);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
  min-height: 0;
}

.session-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px;
  cursor: pointer;
  border-left: 3px solid transparent;
  border-radius: 0 6px 6px 0;
  transition: all 0.2s;
}

.session-item:hover {
  background: rgba(255, 255, 255, 0.05);
}

.session-item.active {
  background: rgba(59, 130, 246, 0.1);
  border-left-color: #3b82f6;
}

.session-icon {
  font-size: 18px;
  color: #64748b;
  margin-top: 1px;
  flex-shrink: 0;
}

.session-item.active .session-icon {
  color: #60a5fa;
}

.session-item:hover .session-icon {
  color: #94a3b8;
}

.session-info {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.session-title {
  font-size: 12px;
  font-weight: 600;
  color: #e2e8f0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-item:not(.active) .session-title {
  font-weight: 500;
  color: #94a3b8;
}

.session-time {
  font-size: 11px;
  color: #64748b;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.delete-btn {
  opacity: 0;
  transition: opacity 0.2s;
  color: #64748b !important;
  flex-shrink: 0;
  margin-top: 1px;
}

.session-item:hover .delete-btn {
  opacity: 1;
}

/* ---- Search Results ---- */
.search-loading,
.search-empty {
  padding: 20px 12px;
  text-align: center;
  font-size: 12px;
  color: #64748b;
}

.search-result-item {
  border-left: 3px solid #22c55e;
}

.message-result-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  cursor: pointer;
  border-radius: 0 6px 6px 0;
  transition: all 0.2s;
}

.message-result-item:hover {
  background: rgba(255, 255, 255, 0.05);
}

.message-result-icon {
  font-size: 14px;
  color: #64748b;
  margin-top: 2px;
  flex-shrink: 0;
}

.message-result-info {
  flex: 1;
  min-width: 0;
}

.message-result-session {
  font-size: 11px;
  font-weight: 600;
  color: #60a5fa;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message-result-content {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.session-pagination {
  padding: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  justify-content: center;
  flex-shrink: 0;
}

.session-pagination :deep(.el-pager li),
.session-pagination :deep(.btn-prev),
.session-pagination :deep(.btn-next) {
  background: transparent;
  color: #94a3b8;
}

.session-pagination :deep(.el-pager li.is-active) {
  background: #3b82f6;
  color: #fff;
}

.session-pagination :deep(.btn-prev:disabled),
.session-pagination :deep(.btn-next:disabled) {
  color: #475569;
}

/* ---- Nav ---- */
.sidebar-nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.nav-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.06);
  margin: 0 8px 8px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: 8px;
  cursor: pointer;
  color: #94a3b8;
  font-size: 13px;
  font-weight: 500;
  transition: all 0.15s;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.05);
  color: #e2e8f0;
}

.nav-item.active {
  background: rgba(59, 130, 246, 0.12);
  color: #60a5fa;
  font-weight: 600;
}

/* ---- Main ---- */
.app-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.mobile-header {
  display: none;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.mobile-menu-btn {
  cursor: pointer;
  color: #334155;
}

.mobile-title {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
}

/* ---- Mobile Drawer ---- */
:deep(.mobile-sidebar-drawer .el-drawer__body) {
  padding: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #1a1a2e;
}

:deep(.mobile-sidebar-drawer .el-drawer__header) {
  background: #1a1a2e;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.mobile-drawer-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
}

.mobile-drawer-logo .logo-text {
  color: #e2e8f0;
}

.drawer-session-actions {
  padding: 12px 12px 0;
  flex-shrink: 0;
}

.drawer-session-search {
  position: relative;
  padding: 8px 12px;
  flex-shrink: 0;
}

.drawer-session-search .search-icon {
  left: 24px;
}

.drawer-session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
  min-height: 0;
}

.drawer-session-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px;
  cursor: pointer;
  border-left: 3px solid transparent;
  border-radius: 0 6px 6px 0;
  transition: all 0.2s;
}

.drawer-session-item:hover {
  background: rgba(255, 255, 255, 0.05);
}

.drawer-session-item.active {
  background: rgba(59, 130, 246, 0.1);
  border-left-color: #3b82f6;
}

.drawer-session-item .session-title {
  color: #e2e8f0;
}

.drawer-session-item:not(.active) .session-title {
  color: #94a3b8;
}

.drawer-session-item .session-time {
  color: #64748b;
}

.drawer-session-item:hover .delete-btn {
  opacity: 1;
}

.drawer-session-pagination {
  padding: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  justify-content: center;
  flex-shrink: 0;
}

.drawer-session-pagination :deep(.el-pager li),
.drawer-session-pagination :deep(.btn-prev),
.drawer-session-pagination :deep(.btn-next) {
  background: transparent;
  color: #94a3b8;
}

.drawer-session-pagination :deep(.el-pager li.is-active) {
  background: #3b82f6;
  color: #fff;
}

.drawer-session-pagination :deep(.btn-prev:disabled),
.drawer-session-pagination :deep(.btn-next:disabled) {
  color: #475569;
}

.drawer-nav {
  flex-shrink: 1;
  padding: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  overflow-y: auto;
  max-height: 40vh;
}

.drawer-nav-full {
  flex: 1;
  max-height: none;
}

.drawer-nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  color: #94a3b8;
  font-size: 13px;
  font-weight: 500;
  transition: all 0.15s;
}

.drawer-nav-item:hover {
  background: rgba(255, 255, 255, 0.05);
  color: #e2e8f0;
}

.drawer-nav-item.active {
  background: rgba(59, 130, 246, 0.12);
  color: #60a5fa;
  font-weight: 600;
}

/* ---- Sidebar User ---- */
.sidebar-user {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.user-avatar {
  color: #60a5fa;
  flex-shrink: 0;
}

.user-name {
  font-size: 13px;
  color: #cbd5e1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 6px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}

.logout-btn:hover {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.1);
}

/* ---- Drawer User ---- */
.drawer-user {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.drawer-user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.drawer-user-name {
  font-size: 13px;
  color: #cbd5e1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drawer-logout-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 6px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}

.drawer-logout-btn:hover {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.1);
}

@media (max-width: 768px) {
  .app-sidebar {
    display: none;
  }

  .mobile-header {
    display: flex;
  }

  .app-main {
    overflow-y: auto;
  }
}
</style>
