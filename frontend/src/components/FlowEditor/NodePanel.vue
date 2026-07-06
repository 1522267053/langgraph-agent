<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useVueFlow } from '@vue-flow/core'
import { useFlowStore } from '@/stores/flowStore'
import { flowApi } from '@/api/flow'
import type { NodeType, CardNodeType, Flow, FlowDetail } from '@/types/flow'
import {
  DArrowRight,
  DArrowLeft,
  Postcard,
  Plus,
  Search,
  CircleClose,
  Operation,
  MagicStick
} from '@element-plus/icons-vue'
import { getBasicPanelNodes, getCardPanelNodes } from './nodeRegistry'

const store = useFlowStore()
const { project } = useVueFlow()

const props = defineProps<{
  collapsed?: boolean
  isAgent?: boolean
  mobileOpen?: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
  (e: 'closeMobile'): void
}>()

// 从注册表获取节点列表
const basicNodes = getBasicPanelNodes()
const cardNodes = getCardPanelNodes()

// Agent 模式排除的节点类型
const agentExcludeTypes = new Set(['loop', 'human'])

// Flow 模式排除的工具类型（仅 Agent 模式显示）
const flowExcludeTypes = new Set(['memory', 'agenda'])

const filteredBasicNodes = computed(() => {
  if (props.isAgent) {
    return basicNodes.filter(item => !agentExcludeTypes.has(item.type))
  }
  if (store.isInSubView && store.subViewParentType === 'loop') {
    return basicNodes.filter(item => item.type !== 'loop')
  }
  return basicNodes
})

const filteredCardNodes = computed(() => {
  if (props.isAgent) {
    return cardNodes.filter(item => !agentExcludeTypes.has(item.type))
  }
  // Flow 模式：排除 memory 和 agenda
  return cardNodes.filter(item => !flowExcludeTypes.has(item.type))
})

const showFlowCardDialog = ref(false)
const flowCardSearch = ref('')
const loadingFlowCard = ref(false)

const filteredFlowCards = computed(() => {
  if (!flowCardSearch.value) return store.flowCards
  return store.flowCards.filter(f =>
    f.name?.toLowerCase().includes(flowCardSearch.value.toLowerCase())
  )
})

function onDragStart(event: DragEvent, type: NodeType | CardNodeType) {
  if (event.dataTransfer) {
    event.dataTransfer.setData('application/vueflow', type)
    event.dataTransfer.effectAllowed = 'move'
  }
}

function onClickToAdd(type: NodeType | CardNodeType) {
  const position = project({ x: 400, y: 300 })
  if (store.isInSubView) {
    store.addSubViewNode(type, position)
  } else {
    store.addNode(type, position)
  }
}

function openFlowCardDialog() {
  showFlowCardDialog.value = true
}

async function selectFlowCard(flow: Flow) {
  loadingFlowCard.value = true
  try {
    const res = await flowApi.get(flow.id!)
    if (res.data.code === 1 && res.data.data) {
      const flowDetail: FlowDetail = res.data.data
      const position = { x: 200, y: 200 }
      store.addFlowCardNode(flowDetail, position, store.subViewParentId)
      showFlowCardDialog.value = false
    }
  } catch {
    // error handled by interceptor
  } finally {
    loadingFlowCard.value = false
  }
}

function handleToggle() {
  emit('toggle')
}

onMounted(() => {
  store.loadFlowCards(store.flowInfo?.id)
})
</script>

<template>
  <div class="node-panel" :class="{ collapsed, 'mobile-open': mobileOpen }">
    <div v-if="!collapsed || mobileOpen" class="panel-content">
      <div v-if="mobileOpen" class="mobile-panel-header">
        <h3 class="mobile-panel-title">添加节点</h3>
        <button class="mobile-close-btn" @click="emit('closeMobile')">
          <el-icon size="18"><CircleClose /></el-icon>
        </button>
      </div>
      <div class="panel-section">
        <h3 class="section-title">
          <el-icon size="14"><Operation /></el-icon>
          基础节点
        </h3>
        <div class="basic-grid">
          <div
            v-for="item in filteredBasicNodes"
            :key="item.type"
            class="basic-node-card"
            draggable="true"
            @dragstart="onDragStart($event, item.type as NodeType)"
            @click="onClickToAdd(item.type as NodeType)"
          >
            <el-icon size="20" :style="{ color: item.iconColor }">
              <component :is="item.icon" />
            </el-icon>
            <span>{{ item.label }}</span>
          </div>
        </div>
      </div>

      <div class="panel-section">
        <h3 class="section-title">
          <el-icon size="14"><MagicStick /></el-icon>
          能力卡片
        </h3>
        <div class="card-list">
          <div
            v-for="item in filteredCardNodes"
            :key="item.type"
            class="card-node-item"
            draggable="true"
            @dragstart="onDragStart($event, item.type as CardNodeType)"
            @click="onClickToAdd(item.type as CardNodeType)"
          >
            <div
              class="card-node-icon"
              :style="{ background: item.iconBgColor, color: item.iconColor }"
            >
              <el-icon size="16">
                <component :is="item.icon" />
              </el-icon>
            </div>
            <span>{{ item.label }}</span>
          </div>
        </div>
      </div>

      <div v-if="!isAgent" class="panel-section">
        <h3 class="section-title">
          <el-icon size="14"><Postcard /></el-icon>
          其他能力卡片
        </h3>
        <div class="card-list">
          <div class="card-node-item flow-card-btn" @click="openFlowCardDialog">
            <div class="card-node-icon" style="background: #ecfdf5; color: #10b981">
              <el-icon size="16">
                <Postcard />
              </el-icon>
            </div>
            <span>添加能力卡片</span>
          </div>
        </div>
      </div>

      <div class="panel-hint">点击或拖拽组件至画布以添加步骤</div>
    </div>

    <div class="toggle-btn" @click="handleToggle">
      <el-icon>
        <DArrowRight v-if="collapsed" />
        <DArrowLeft v-else />
      </el-icon>
    </div>

    <el-dialog v-model="showFlowCardDialog" title="选择能力卡片" width="500px">
      <el-input
        v-model="flowCardSearch"
        placeholder="搜索能力卡片"
        clearable
        style="margin-bottom: 16px"
      >
        <template #prefix>
          <el-icon>
            <Search />
          </el-icon>
        </template>
      </el-input>
      <div v-if="filteredFlowCards.length === 0" class="empty-flow-cards">
        <span>暂无可用的能力卡片</span>
        <span class="hint">请先将其他流程保存为能力卡片</span>
      </div>
      <div v-else class="flow-card-list">
        <div
          v-for="flow in filteredFlowCards"
          :key="flow.id"
          class="flow-card-item"
          @click="selectFlowCard(flow)"
        >
          <div class="flow-info">
            <span class="flow-name">{{ flow.name }}</span>
            <span class="flow-desc">{{ flow.description || '暂无描述' }}</span>
          </div>
          <el-icon class="add-icon">
            <Plus />
          </el-icon>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.node-panel {
  width: 288px;
  background: #fff;
  border-right: 1px solid #e2e8f0;
  display: flex;
  position: relative;
  height: 100%;
  flex-shrink: 0;
  min-height: 0;
}

.node-panel.collapsed {
  width: 24px;
}

.panel-content {
  flex: 1;
  padding: 16px;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.toggle-btn {
  position: absolute;
  right: -12px;
  top: 50%;
  transform: translateY(-50%);
  width: 24px;
  height: 48px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 0 8px 8px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 100;
  transition: background 0.2s;
}

.toggle-btn:hover {
  background: #f8fafc;
}

.toggle-btn .el-icon {
  font-size: 12px;
  color: #94a3b8;
}

.panel-section {
  margin-bottom: 24px;
}

.section-title {
  font-size: 10px;
  font-weight: 700;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.basic-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.basic-node-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  cursor: grab;
  transition: all 0.2s;
  font-size: 11px;
  font-weight: 700;
  color: #334155;
  user-select: none;
}

.basic-node-card:hover {
  border-color: #3b82f6;
  background: rgba(59, 130, 246, 0.05);
}

.basic-node-card:active {
  cursor: grabbing;
}

.card-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-node-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  cursor: grab;
  transition: all 0.2s;
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  user-select: none;
}

.card-node-item:hover {
  border-color: #3b82f6;
  background: rgba(59, 130, 246, 0.05);
}

.card-node-item:active {
  cursor: grabbing;
}

.flow-card-btn {
  cursor: pointer;
}

.card-node-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.panel-hint {
  margin-top: auto;
  padding: 12px 0;
  font-size: 10px;
  color: #94a3b8;
  text-align: center;
  font-style: italic;
  border-top: 1px solid #f1f5f9;
}

.empty-flow-cards {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: #94a3b8;
}

.empty-flow-cards .hint {
  font-size: 12px;
  margin-top: 8px;
  color: #cbd5e1;
}

.flow-card-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 400px;
  overflow-y: auto;
}

.flow-card-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #f8fafc;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  cursor: pointer;
  transition: all 0.2s;
}

.flow-card-item:hover {
  border-color: #3b82f6;
  background: rgba(59, 130, 246, 0.05);
}

.flow-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.flow-name {
  font-weight: 600;
  color: #1e293b;
}

.flow-desc {
  font-size: 12px;
  color: #94a3b8;
}

.add-icon {
  color: #3b82f6;
  font-size: 18px;
}

.mobile-panel-header {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid #e2e8f0;
  margin-bottom: 16px;
}

.mobile-panel-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  margin: 0;
}

.mobile-close-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: #f1f5f9;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #64748b;
}

@media (max-width: 768px) {
  .node-panel {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    width: 80vw;
    max-width: 320px;
    z-index: 200;
    transform: translateX(-100%);
    transition: transform 0.3s ease;
    box-shadow: none;
  }

  .node-panel.mobile-open {
    transform: translateX(0);
    box-shadow: 4px 0 24px rgba(0, 0, 0, 0.15);
  }

  .node-panel.collapsed {
    width: 80vw;
    max-width: 320px;
  }

  .toggle-btn {
    display: none;
  }

  .mobile-panel-header {
    display: flex;
  }
}
</style>
