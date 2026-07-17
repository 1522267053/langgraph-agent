# LangGraph Agent Frontend

基于 Vue 3 + TypeScript 的智能体流程编排平台前端，支持可视化流程设计、Agent 对话、流式推理展示。

## 功能特性

- **可视化流程编辑** - 基于 Vue Flow 的拖拽式流程设计，支持 19 种节点类型
- **双模式编辑** - Flow（工作流编排）和 Agent（对话式助手）共用编辑器，按模式过滤可用节点
- **Agent 对话** - 完整的会话管理、SSE 流式对话、Human-in-the-loop 交互恢复
- **流式推理展示** - 实时显示 LLM 思维链（DeepSeek thinking / Anthropic thinking）、内容输出、工具调用
- **子视图编辑** - 循环体和卡片子流程的独立子视图，支持节点面板自动过滤
- **能力卡片** - 可复用的流程封装，支持跨流程引用和输入输出变量映射
- **知识库管理** - 双面板布局（知识库 + 文档），上传文档、查看分段、触发向量化
- **MCP 服务器管理** - 配置 stdio/SSE/streamable-http 传输，测试连接、查看工具列表
- **Skill 管理** - 上传 Skill ZIP 包、查看内容、批量重载
- **执行追踪** - 节点级执行时间线、流式内容回放、历史执行记录浏览
- **文件管理** - 流程级和全局文件上传/下载/预览，支持图片/视频/音频预览
- **快捷键** - Ctrl+C/V 复制粘贴节点、Ctrl+Z/Y 撤销重做、Delete 删除

## 技术栈

| 类别 | 技术 |
|------|------|
| 框架 | Vue 3 (Composition API + `<script setup>`) |
| 语言 | TypeScript (strict mode) |
| 构建 | Vite 7 |
| 状态管理 | Pinia (组合式 API) |
| 路由 | Vue Router (hash 模式，全部懒加载) |
| UI 组件 | Element Plus (按需自动导入) |
| 流程编辑 | Vue Flow (@vue-flow/core + background + controls + minimap) |
| HTTP 客户端 | Axios |
| 流式通信 | 原生 fetch + ReadableStream (POST-based SSE) |
| Markdown | vue-markdown-render + highlight.js |
| 代码规范 | ESLint + Prettier |

## 环境要求

- Node.js >= 18
- npm >= 9

## 快速开始

```bash
npm install && npm run dev    # 安装依赖并启动（端口 3000）
npm run build                 # 生产构建
npm run lint                  # ESLint 检查（自动修复）
npm run format                # Prettier 格式化
```

开发环境下，`/api` 请求通过 Vite 代理转发到 `http://127.0.0.1:8000`。

**注意**: 当前项目未配置测试框架，修改代码后请运行 `npm run lint` 验证。

## 页面路由

| 路径 | 页面 | 说明 |
|------|------|------|
| `/chat` / `/chat/:id` | AgentChat | AI 助手 / 智能体对话 |
| `/flow` | FlowList | 流程和智能体列表管理 |
| `/flow/create` / `/flow/edit/:id` | FlowEdit | 创建/编辑流程 |
| `/flow/files/:id` | FlowFiles | 流程文件管理 |
| `/agent/create` / `/agent/edit/:id` | FlowEdit | 创建/编辑智能体 |
| `/agent/files/:id` | FlowFiles | 智能体文件管理 |
| `/execution` | ExecutionList | 执行记录 |
| `/mcp-server` | McpServerList | MCP 服务器管理 |
| `/skill-list` | SkillList | Skill 管理 |
| `/knowledge` | KnowledgeList | 知识库管理 |
| `/files` | FileList | 全局文件管理 |
| `/scheduled-task` | ScheduledTaskList | 定时任务管理 |
| `/agenda` | AgendaList | 日程管理 |
| `/ws-gateway` | WsGatewayList | WebSocket 网关管理 |
| `/statistics` | TokenStatistics | Token 统计 |
| `/marketplace` | Marketplace | 资源市场 |
| `/setup` | SetupWizard | 初始化配置向导 |
| `/settings` | Settings | 系统设置 |
| `/login` | Login | 登录页 |

## 项目结构

```
src/
├── api/                    # API 请求封装
│   ├── index.ts            #   Axios 实例 + get/post/put/del 工具函数
│   ├── flow.ts             #   流程/节点/边 CRUD
│   ├── agent.ts            #   Agent 会话/SSE 对话
│   ├── execution.ts        #   执行记录/SSE 执行流
│   ├── skill.ts            #   Skill 管理
│   ├── mcpServer.ts        #   MCP 服务器管理
│   ├── knowledge.ts        #   知识库/文档管理
│   ├── file.ts             #   文件上传/下载
│   └── ai_provider.ts      #   AI Provider 列表
├── components/
│   ├── FlowEditor/         # 流程编辑器
│   │   ├── nodeRegistry.ts  #   节点注册表（自动发现 + 元数据 + hook，唯一数据源）
│   │   ├── nodes/          #   节点组件（19 种 + BaseNode，import.meta.glob 自动注册）
│   │   ├── config/         #   节点配置面板（19 种 + ApiFileItem，import.meta.glob 自动注册）
│   │   ├── FlowCanvas.vue  #   Vue Flow 画布（快捷键、拖拽、连接）
│   │   ├── ConfigPanel.vue #   右侧配置面板（component :is 动态渲染）
│   │   ├── NodePanel.vue   #   左侧节点面板（从注册表派生节点列表）
│   │   ├── Toolbar.vue     #   顶部工具栏
│   │   ├── ExecutionPanel.vue  # 执行结果面板
│   │   ├── HumanInputDialog.vue # 人工交互对话框
│   │   ├── FileUpload.vue  #   文件上传组件
│   │   └── components/     #   编辑器内部组件（VariableSelector 等）
│   ├── AgentChat/          # Agent 对话组件
│   │   ├── SessionSidebar.vue  # 会话列表侧栏
│   │   ├── ChatInput.vue   #   聊天输入（文件上传、Human 恢复）
│   │   ├── MessageItem.vue #   消息渲染（流式段落、工具调用、重试）
│   │   ├── TodoPanel.vue   # 任务进度面板
│   │   └── DisplayToggle.vue   # 显示设置（思维链/工具调用开关）
│   ├── common/             # 通用组件
│   │   ├── AIMessageContent.vue    # AI 消息渲染（多段落类型）
│   │   ├── ThinkingBlock.vue       # 思维链折叠展示
│   │   └── ExecutionResultContent.vue  # 执行结果渲染
│   └── MarkdownRenderer.vue  # Markdown 渲染（GFM + 代码高亮）
├── composables/            # 组合式函数
│   ├── useSSE.ts           #   SSE 连接管理（POST + ReadableStream）
│   ├── useStreamingMessage.ts  # 流式消息处理（思维/内容/工具/待办段落）
│   ├── useListPage.ts      #   通用分页列表
│   ├── useConfigBase.ts    #   配置面板基类（深拷贝 + watch）
│   ├── useAvailableVariables.ts  # 上游变量可用性计算
│   └── useInputVariables.ts #   输入变量管理
├── constants/              # 常量配置
│   ├── nodeTypes.ts        #   节点类型常量（从 nodeRegistry 派生）
│   ├── variable.ts         #   变量路径前缀和引用规范
│   ├── status.ts           #   流程/执行/节点状态枚举和文本映射
│   └── operators.ts        #   12 种条件运算符（比较/字符串/存在性）
├── stores/                 # Pinia 状态仓库
│   ├── flowStore.ts        #   流程编辑器状态（节点/边/选中/子视图/保存）
│   └── agentOptimized.ts   #   Agent 对话状态（会话/消息/SSE 流式）
├── types/                  # TypeScript 类型定义
│   ├── common.ts           #   ApiResponse、分页、BaseEntity
│   ├── flow.ts             #   Flow/FlowNode/FlowEdge/FlowIO 类型
│   ├── agent.ts            #   Agent/Session/Message 类型
│   ├── execution.ts        #   Execution/NodeExecution 类型
│   ├── sse.ts              #   SSE 事件类型、FlowSSEHandlers 回调
│   ├── skill.ts            #   Skill 类型
│   ├── mcpServer.ts        #   MCP Server/Tool 类型
│   └── knowledge.ts        #   KnowledgeBase/Document/Segment 类型
├── utils/                  # 工具函数
│   ├── sse.ts              #   SSE 连接（fetch + ReadableStream 事件解析）
│   ├── format.ts           #   日期/文件大小/JSON/工具参数格式化
│   └── flowTransform.ts    #   后端 ↔ Vue Flow 数据转换
├── views/                  # 页面组件（17 个）
├── router/                 # 路由配置（hash 模式，全部懒加载）
├── App.vue                 # 根组件（侧边栏导航）
├── main.ts                 # 应用入口
└── style.css               # 全局样式
```

## 节点类型

流程编辑器支持 19 种节点，分为 4 个类别：

| 类别 | 节点 | Handle 模式 |
|------|------|------------|
| **基础** | start, end, condition, loop, intent_router | 标准 I/O |
| **LLM** | llm | 标准 I/O + 工具输入（orange） |
| **工具** | mcp, api, skill, knowledge, python, shell, memory, todo, sub_agent, agenda | 纯工具输出（green→tools） |
| **交互** | human, card | 标准 I/O + 工具输出 |

Handle 颜色约定：green=输入、blue=输出、orange=工具、red=假分支

## 流式 SSE 架构

不使用 EventSource（仅支持 GET），而是基于 POST + ReadableStream 实现双向 SSE：

```
客户端 POST /api/xxx/stream
    ↓
ReadableStream 读取 SSE 事件流
    ↓
useStreamingMessage 解析事件类型
    ├── thinking    → ThinkingBlock（折叠展示）
    ├── content     → Markdown 渲染
    ├── tool_call   → AIMessageContent 内置 tool block（加载/成功/失败）
    ├── todo        → TodoPanel（任务进度）
    └── media       → 图片/音频/视频播放
```

## 开发指南

详细的代码风格和开发规范请参考 [AGENTS.md](./AGENTS.md)。

### 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 组件文件 | PascalCase | `FlowList.vue`, `LlmNode.vue` |
| 组合式函数 | use 前缀 | `useListPage`, `useSSE` |
| Store | use + Store 后缀 | `useFlowStore`, `useAgentStore` |
| API 模块 | xxxApi | `flowApi`, `agentApi` |
| API 数据属性 | snake_case | `flow_type`, `node_key`, `is_enabled` |

### 添加新节点类型

1. `src/types/flow.ts` — 添加 `CardNodeType` 联合类型成员
2. `src/components/FlowEditor/nodeRegistry.ts` — 添加 entry（label/icon/category/defaultConfig + 可选 hook）
3. `src/components/FlowEditor/nodes/` — 创建节点组件（继承 BaseNode，放入目录即自动注册）
4. `src/components/FlowEditor/config/` — 创建配置面板组件（放入目录即自动注册）

> 组件由 `nodeRegistry.ts` 的 `import.meta.glob` 自动发现，无需手动 import。
> 标准节点（无特殊逻辑）仅需一个 registry entry，ConfigPanel 和 NodePanel 自动渲染。

### 添加新页面

1. `src/types/` — TypeScript 类型定义
2. `src/api/` — API 请求函数
3. `src/views/` — 页面组件（PascalCase）
4. `src/router/index.ts` — 懒加载路由配置

## 许可证

MIT
