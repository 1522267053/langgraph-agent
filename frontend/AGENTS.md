# 前端开发指南

基于 Vue 3 + TypeScript 的智能体流程编排平台前端。
- **技术栈**: Vue 3 | TypeScript | Vite | Pinia | Vue Router | Element Plus | Vue Flow | Axios

## 构建与运行命令

```bash
npm install && npm run dev         # 安装依赖并启动（端口 3000）
npm run build                      # 生产构建（vite build，不含 vue-tsc）
npm run lint                       # ESLint 检查并自动修复
npm run lint:check                 # ESLint 仅检查（不修复）
npm run format                     # Prettier 格式化
npm run format:check               # Prettier 仅检查（不修复）
```
- 无测试框架，修改后请运行 `npm run lint` 验证
- `npm run build` 只运行 `vite build`，不包含类型检查

## 项目结构

```
src/
├── api/                  # API 请求封装（基于 axios，导出 get/post/put/del 方法）
├── assets/               # 静态资源
├── theme/                # Element Plus 主题覆盖
├── components/
│   ├── FlowEditor/       #   流程编辑器
│   │   ├── nodes/        #     17 种节点组件（markRaw 包装）
│   │   ├── config/       #     17 种节点配置组件
│   │   ├── components/   #     编辑器内部组件（VariableSelector）
│   │   ├── FlowCanvas.vue      画布
│   │   ├── ConfigPanel.vue    配置面板
│   │   ├── NodePanel.vue      节点面板
│   │   ├── Toolbar.vue        工具栏
│   │   ├── ExecutionPanel.vue 执行面板
│   │   ├── HumanInputDialog.vue / FlowQuickExecute.vue / ExecuteFlowDialog.vue / CreateFlowDialog.vue
│   │   └── FileUpload.vue
│   ├── AgentChat/        #   Agent 对话组件（ChatInput, MessageItem, SessionSidebar, MemoryPanel, TodoPanel, DisplayToggle）
│   └── common/           #   共享组件（AIMessageContent, ThinkingBlock, ExecutionResultContent, FlowInputForm, FilePickerDialog, ActionColumn, FilePreviewer, TodoList）
├── composables/          # 组合式函数（useSSE, useSSEHandlers, useStreamingMessage, useFlowExecution, useInputVariables, useConfigBase, useIsMobile, useSegmentBuilder, useAvailableVariables, useNodeSchema, useAutoScroll, useListPage）
├── constants/            # 静态常量（nodeTypes, llm, operators, status）
├── router/               # Vue Router 配置（hash 模式，全部懒加载，18 条路由）
├── stores/               # Pinia 状态仓库（useFlowStore, useAgentStore 组合式 API 风格）
├── types/                # TypeScript 类型定义
├── utils/                # 工具函数（sse, flowTransform, format）
├── views/                # 14 个页面组件
├── App.vue               # 根组件（含侧边栏导航，isEditorPage 控制全屏页）
├── main.ts               # 应用入口
└── style.css             # 全局样式
```

## 关键注意事项

1. **路径别名**: `@/` 代表 `src/`
2. **API 代理**: `/api` 代理到 `http://127.0.0.1:8000`（vite.config.ts）
3. **类型导入**: 使用 `import type` 导入纯类型
4. **Vue Flow 节点**: 必须用 `markRaw()` 包装，handles 的 `id` 字段用于命名连接点
5. **Element Plus 组件**: 通过 `unplugin-vue-components` 按需自动导入，**不需要**手动 import
6. **Element Plus 图标**: 每个组件文件显式 import，**不在 main.ts 全局注册**
7. **Element Plus 功能 API CSS**: `ElMessage`/`ElMessageBox`/`ElNotification`/`ElLoading`/`ElImageViewer` 的样式需在 `main.ts` 手动导入
8. **SSE 流式**: 使用原生 `fetch` + `ReadableStream`，POST-based（**非 EventSource**）
9. **API 响应格式**: `{ code: 1, msg: '成功', data: T }`，axios 拦截器自动处理 `code !== 1` 的错误弹窗，401 跳转登录页
10. **API 数据属性**: 使用 snake_case 与后端保持一致（`flow_type`、`node_key`、`is_enabled`）
11. **路由**: 全部懒加载，hash 模式（`createWebHashHistory`），`FlowEdit.vue` 同时用于流程和智能体的创建/编辑
12. **App.vue 全屏页**: `isEditorPage` 是 `ref(true)` + `watch(route.name)`，默认 `true` 避免侧边栏闪烁。全屏页路由名：FlowCreate、FlowEdit、AgentCreate、AgentEdit、SetupWizard、Login
13. **Handle 颜色约定**: green=输入, blue=输出, orange=工具, red=假分支
14. **三种节点 Handle 模式**: 标准 I/O、工具启用型（LLM，有橙色工具输入）、纯工具提供者（MCP/Skill/Memory，只有绿色工具输出）
15. **文件选择**: 所有需要选择文件的场景必须使用 `FilePickerDialog` 组件（`common/FilePickerDialog.vue`），**禁止**使用 `FileUpload` 组件。通过弹窗从已有文件中选择，支持 `accept`（类型过滤）、`maxSize`（MB 限制）、`multiple`（单/多选）

## 代码规范

### 导入顺序

```typescript
// 1. Vue 核心  2. 第三方库  3. 本地模块（@ 别名）
import { ref, computed, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { Flow } from '@/types/flow'
import { flowApi } from '@/api/flow'
```

### TypeScript

- 严格模式: `"strict": true`, `"noUnusedLocals": true`, `"noUnusedParameters": true`
- 对象形状使用 `interface`，联合类型使用 `type`
- 仅导入类型时使用 `import type`
- 避免 `any`，使用 `unknown` 替代（`no-explicit-any` 为 warn）

### Vue 组件

```vue
<script setup lang="ts">
const props = defineProps<{ id: string; data: { label?: string } }>()
const emit = defineEmits<{ (e: 'update', value: string): void }>()
</script>
```
- 始终使用 `<script setup lang="ts">`
- 使用 TypeScript 泛型定义 props/emits
- 组件文件名使用 PascalCase：`FlowList.vue`, `LlmNode.vue`
- `vue/multi-word-component-names` 规则已关闭（允许 `BaseNode.vue` 等单字组件名）

### API 封装

```typescript
import request, { get, post, del } from '@/api/index'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '@/types/common'

export const flowApi = {
  page(params: PaginationParams<Flow>) {
    return post<ApiResponse<PaginatedResponse<Flow>>>('/flow/page', params)
  },
  get(id: number) {
    return get<ApiResponse<Flow>>(`/flow/get/${id}`)
  },
  delete(id: number) {
    return del<ApiResponse>(`/flow/delete/${id}`)
  }
}
// DELETE 方法命名为 del()（避免 JS 保留字）
```

### Vue Flow 节点

```typescript
// nodes/index.ts — 所有节点必须 markRaw() 包装
export const nodeTypes = { start: markRaw(StartNode), llm: markRaw(LlmNode) }

// handles 定义（id 字段对命名 handle 关键）
const handles = [
  { type: 'target' as const, position: 'top' as const, id: 'tools', color: 'orange' as const }
]
```

### Pinia Store

- 组合式 API 风格: `defineStore(id, () => {})`
- 错误通过 `ElMessage.error()` 显示

### 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 组件文件 | PascalCase | `FlowList.vue`, `LlmNode.vue` |
| 组合式函数文件 | camelCase | `useSse.ts`, `useListPage.ts` |
| 组合式函数 | use 前缀 | `useListPage`, `useSSE` |
| Store | use + Store 后缀 | `useFlowStore`, `useAgentStore` |
| API 模块 | xxxApi | `flowApi`, `agentApi` |
| 类型/接口 | PascalCase | `FlowExecution`, `FlowSSEHandlers` |

### 注释规范

- 复杂的 composable/store 函数加注释说明用途和关键逻辑
- 核心业务逻辑处加行内注释（如条件分支、状态流转、SSE 处理）
- 组件模板中复杂的 `v-if`/`v-for` 条件加注释说明
- 不写废话注释（如 `// 获取数据` 放在 `loadData()` 上方）

### ESLint 规则

- `@typescript-eslint/no-unused-vars`: error（`_` 前缀参数忽略）
- `@typescript-eslint/no-explicit-any`: warn
- `vue/multi-word-component-names`: off
- `no-console` / `no-debugger`: off

### Prettier 配置

不使用分号 | 单引号 | 2空格缩进 | 100字符行宽 | 无 trailing 逗号 | 单参数箭头函数省略括号

## 添加新模块清单

1. `src/types/` — TypeScript 类型（interface 用于对象，type 用于联合类型）
2. `src/api/` — API 请求函数（使用 `get`/`post`/`put`/`del` 导出方法）
3. `src/stores/` — Pinia store（可选）
4. `src/composables/` — 组合式函数（如需复用逻辑）
5. `src/components/` — Vue 组件
6. `src/views/` — 页面组件
7. `src/router/index.ts` — 路由配置（`() => import('@/views/...')` 懒加载）
