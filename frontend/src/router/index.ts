import { createRouter, createWebHashHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { configApi } from '@/api/config'
import { authApi } from '@/api/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/chat'
  },
  {
    path: '/setup',
    name: 'SetupWizard',
    component: () => import('@/views/SetupWizard.vue'),
    meta: { title: '初始配置', public: true }
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', public: true }
  },
  {
    path: '/chat',
    name: 'HomeChat',
    component: () => import('@/views/AgentChat.vue'),
    meta: { title: 'AI 助手' }
  },
  {
    path: '/chat/:id',
    name: 'AgentChat',
    component: () => import('@/views/AgentChat.vue'),
    meta: { title: '智能体对话' }
  },
  {
    path: '/flow',
    name: 'FlowList',
    component: () => import('@/views/FlowList.vue'),
    meta: { title: '流程和智能体管理' }
  },
  {
    path: '/flow/create',
    name: 'FlowCreate',
    component: () => import('@/views/FlowEdit.vue'),
    meta: { title: '创建流程' }
  },
  {
    path: '/flow/edit/:id',
    name: 'FlowEdit',
    component: () => import('@/views/FlowEdit.vue'),
    meta: { title: '编辑流程' }
  },
  {
    path: '/flow/files/:id',
    name: 'FlowFiles',
    component: () => import('@/views/FlowFiles.vue'),
    meta: { title: '流程文件管理' }
  },
  {
    path: '/agent/create',
    name: 'AgentCreate',
    component: () => import('@/views/FlowEdit.vue'),
    meta: { title: '创建智能体' }
  },
  {
    path: '/agent/edit/:id',
    name: 'AgentEdit',
    component: () => import('@/views/FlowEdit.vue'),
    meta: { title: '编辑智能体' }
  },
  {
    path: '/agent/files/:id',
    name: 'AgentFiles',
    component: () => import('@/views/FlowFiles.vue'),
    meta: { title: '智能体文件管理' }
  },
  {
    path: '/execution',
    name: 'ExecutionList',
    component: () => import('@/views/ExecutionList.vue'),
    meta: { title: '执行记录' }
  },
  {
    path: '/mcp-server',
    name: 'McpServerList',
    component: () => import('@/views/McpServerList.vue'),
    meta: { title: 'MCP服务器管理' }
  },
  {
    path: '/skill-list',
    name: 'SkillList',
    component: () => import('@/views/SkillList.vue'),
    meta: { title: 'skill管理' }
  },
  {
    path: '/knowledge',
    name: 'KnowledgeList',
    component: () => import('@/views/KnowledgeList.vue'),
    meta: { title: '知识库管理' }
  },
  {
    path: '/files',
    name: 'FileList',
    component: () => import('@/views/FileList.vue'),
    meta: { title: '文件管理' }
  },
  {
    path: '/scheduled-task',
    name: 'ScheduledTaskList',
    component: () => import('@/views/ScheduledTaskList.vue'),
    meta: { title: '定时任务管理' }
  },
  {
    path: '/agenda',
    name: 'AgendaList',
    component: () => import('@/views/AgendaList.vue'),
    meta: { title: '日程管理' }
  },
  {
    path: '/ws-gateway',
    name: 'WsGatewayList',
    component: () => import('@/views/WsGatewayList.vue'),
    meta: { title: 'WebSocket 网关' }
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/views/Settings.vue'),
    meta: { title: '系统设置' }
  },
  {
    path: '/statistics',
    name: 'TokenStatistics',
    component: () => import('@/views/TokenStatistics.vue'),
    meta: { title: 'Token 统计' }
  },
  {
    path: '/marketplace',
    name: 'Marketplace',
    component: () => import('@/views/Marketplace.vue'),
    meta: { title: '市场' }
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

let initChecked = false
let authChecked = false
let needAuth = false

async function checkAuth(): Promise<{ needLogin: boolean; authenticated: boolean }> {
  try {
    const res = await authApi.check()
    const data = res.data.data
    return {
      needLogin: data?.need_login ?? false,
      authenticated: data?.authenticated ?? false
    }
  } catch {
    return { needLogin: false, authenticated: false }
  }
}

router.beforeEach(async (to, _from, next) => {
  document.title = (to.meta?.title as string) || 'AI Agent OS'

  // 访问 setup 页面时，如果已初始化则跳转首页
  if (to.path === '/setup') {
    if (!initChecked) {
      try {
        const res = await configApi.checkInitialized()
        initChecked = res.data.data?.initialized === true
      } catch {
        initChecked = false
      }
    }
    if (initChecked) {
      next('/chat')
      return
    }
    next()
    return
  }

  // login 等其他 public 页面直接放行
  if (to.meta?.public) {
    next()
    return
  }

  // 首次加载时检查初始化状态（结果缓存，不重复请求）
  if (!initChecked) {
    try {
      const res = await configApi.checkInitialized()
      initChecked = res.data.data?.initialized === true
    } catch {
      initChecked = false
    }
  }

  if (!initChecked && to.path !== '/setup') {
    next('/setup')
    return
  }

  // 检查是否需要登录
  if (!authChecked) {
    const result = await checkAuth()
    needAuth = result.needLogin
    authChecked = true
    if (needAuth && !result.authenticated) {
      next('/login?redirect=' + encodeURIComponent(to.fullPath))
      return
    }
  } else if (needAuth) {
    const result = await checkAuth()
    if (!result.authenticated) {
      next('/login?redirect=' + encodeURIComponent(to.fullPath))
      return
    }
  }

  next()
})

export default router
