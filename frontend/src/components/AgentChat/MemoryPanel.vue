<script setup lang="ts">
import { ref, watch, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Download, Upload, Search, CircleClose } from '@element-plus/icons-vue'
import { memoryApi } from '@/api/memory'
import { configApi } from '@/api/config'
import type { Memory, MemoryExportItem, MemorySearchHit } from '@/api/memory'

const props = defineProps<{
  agentId: number | null
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

const memories = ref<Memory[]>([])
const loading = ref(false)
const activeType = ref('all')
const currentPage = ref(1)
const pageSize = 20
const total = ref(0)
const selectedIds = ref<number[]>([])
const revectorizing = ref(false)
const exporting = ref(false)
const importing = ref(false)
const importFileRef = ref<HTMLInputElement | null>(null)
const embeddingAvailable = ref(true)
let _fetchEmbeddingDone = false

const searchQuery = ref('')
const searchResults = ref<MemorySearchHit[]>([])
const isSearchMode = computed(() => searchQuery.value.trim().length > 0)
const expandedIds = ref<Set<number>>(new Set())

async function checkEmbeddingConfig(): Promise<void> {
  if (_fetchEmbeddingDone) return
  _fetchEmbeddingDone = true
  try {
    const res = await configApi.getConfig()
    embeddingAvailable.value = !!res.data.data?.embedding_api_key_masked
  } catch {
    embeddingAvailable.value = false
  }
}

onMounted(() => {
  checkEmbeddingConfig()
})

const tierOptions = [
  { value: 'all', label: '全部', color: '#409eff' },
  { value: 'hot', label: '热', color: '#f56c6c' },
  { value: 'warm', label: '温', color: '#e6a23c' },
  { value: 'cold', label: '冷', color: '#909399' }
]

const categoryLabels: Record<string, string> = {
  decision: '决策',
  preference: '偏好',
  lesson: '教训',
  relation: '关系',
  event: '事件',
  task: '任务',
  profile: '用户资料',
  knowledge: '知识',
  instruction: '指令',
  other: '其他'
}

const tierStats = ref({ hot: 0, warm: 0, cold: 0 })

const displayList = computed(() => {
  if (isSearchMode.value) {
    return searchResults.value.map(hit => ({ memory: hit.memory, score: hit.score }))
  }
  return memories.value.map(m => ({ memory: m, score: 0 }))
})

async function loadStats(): Promise<void> {
  if (!props.agentId) return
  try {
    const res = await memoryApi.getStats(props.agentId)
    if (res.data.code === 1 && res.data.data) {
      tierStats.value = res.data.data
    }
  } catch {
    // ignore
  }
}

const isAllSelected = computed(() => {
  return memories.value.length > 0 && selectedIds.value.length === memories.value.length
})

function handleSelectAll(): void {
  if (isAllSelected.value) {
    selectedIds.value = []
  } else {
    selectedIds.value = memories.value.map(m => m.id)
  }
}

function handleSelectOne(id: number): void {
  const idx = selectedIds.value.indexOf(id)
  if (idx >= 0) {
    selectedIds.value.splice(idx, 1)
  } else {
    selectedIds.value.push(id)
  }
}

async function loadMemories(): Promise<void> {
  if (!props.agentId) return
  loading.value = true
  selectedIds.value = []
  try {
    const condition: Record<string, unknown> = { agent_id: props.agentId }
    if (activeType.value !== 'all') {
      condition.memory_type = activeType.value
    }
    const res = await memoryApi.page({
      page: currentPage.value,
      page_size: pageSize,
      condition
    })
    if (res.data.code === 1 && res.data.data) {
      memories.value = res.data.data.items || []
      total.value = res.data.data.total || 0
    }
  } catch {
    memories.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function handleTypeChange(type: string): void {
  activeType.value = type
  currentPage.value = 1
  if (isSearchMode.value) {
    doSearch()
  } else {
    loadMemories()
  }
}

async function doSearch(): Promise<void> {
  if (!props.agentId || !searchQuery.value.trim()) return
  loading.value = true
  selectedIds.value = []
  try {
    const params: Record<string, unknown> = {
      agent_id: props.agentId,
      query: searchQuery.value.trim(),
      max_results: 50
    }
    if (activeType.value !== 'all') {
      params.tier = activeType.value
    }
    const res = await memoryApi.search(params as Parameters<typeof memoryApi.search>[0])
    if (res.data.code === 1 && res.data.data) {
      searchResults.value = res.data.data.items || []
    }
  } catch {
    searchResults.value = []
  } finally {
    loading.value = false
  }
}

function handleSearch(): void {
  if (isSearchMode.value) {
    doSearch()
  }
}

function handleSearchClear(): void {
  searchQuery.value = ''
  searchResults.value = []
  loadMemories()
}

function handlePageChange(page: number): void {
  currentPage.value = page
  loadMemories()
}

function getTierLabel(type: string): string {
  return tierOptions.find(t => t.value === type)?.label || type
}

function getTierColor(type: string): string {
  return tierOptions.find(t => t.value === type)?.color || '#909399'
}

function getCategoryLabel(cat: string): string {
  return categoryLabels[cat] || cat
}

function toggleExpand(id: number): void {
  const s = new Set(expandedIds.value)
  if (s.has(id)) {
    s.delete(id)
  } else {
    s.add(id)
  }
  expandedIds.value = s
}

function importanceStars(importance: number): string {
  return '★'.repeat(importance) + '☆'.repeat(5 - importance)
}

async function handleDeleteSelected(): Promise<void> {
  if (selectedIds.value.length === 0) return
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${selectedIds.value.length} 条记忆？`, '批量删除', {
      type: 'warning'
    })
    await memoryApi.deleteBatch(selectedIds.value)
    ElMessage.success(`已删除 ${selectedIds.value.length} 条记忆`)
    if (isSearchMode.value) {
      doSearch()
    } else {
      loadMemories()
    }
    loadStats()
  } catch {
    // cancelled
  }
}

async function handleRevectorizeSelected(): Promise<void> {
  if (selectedIds.value.length === 0 || !props.agentId) return
  revectorizing.value = true
  try {
    const res = await memoryApi.revectorize(props.agentId, selectedIds.value)
    if (res.data.code === 1 && res.data.data) {
      const { success, failed } = res.data.data
      ElMessage.success(`向量化完成：成功 ${success} 条${failed > 0 ? `，失败 ${failed} 条` : ''}`)
      loadMemories()
    }
  } catch {
    // error handled by interceptor
  } finally {
    revectorizing.value = false
  }
}

async function handleExport(): Promise<void> {
  if (!props.agentId) return
  exporting.value = true
  try {
    const params: { agent_id: number; ids?: number[]; tier?: string } = {
      agent_id: props.agentId
    }
    if (selectedIds.value.length > 0) {
      params.ids = selectedIds.value
    }
    if (activeType.value !== 'all') {
      params.tier = activeType.value
    }
    const res = await memoryApi.exportMemory(params)
    if (res.data.code === 1 && res.data.data) {
      const { export_time, total, memories } = res.data.data
      const blob = new Blob([JSON.stringify({ export_time, total, memories }, null, 2)], {
        type: 'application/json'
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const date = new Date().toISOString().slice(0, 10)
      a.download = `memory_agent_${props.agentId}_${date}.json`
      a.click()
      URL.revokeObjectURL(url)
      ElMessage.success(`已导出 ${total} 条记忆`)
    }
  } catch (e) {
    console.error('导出失败详情:', e)
  } finally {
    exporting.value = false
  }
}

function handleImportClick(): void {
  importFileRef.value?.click()
}

async function handleImportFile(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !props.agentId) return
  importing.value = true
  try {
    const text = await file.text()
    const data = JSON.parse(text)
    if (!data.memories || !Array.isArray(data.memories)) {
      ElMessage.error('无效的导入文件格式：缺少 memories 数组')
      return
    }
    const items: MemoryExportItem[] = data.memories
    if (items.length === 0) {
      ElMessage.warning('导入文件没有包含记忆数据')
      return
    }
    if (!items.every((m: Record<string, unknown>) => m.title && m.content)) {
      ElMessage.error('无效的导入文件格式：每条记忆需要 title 和 content')
      return
    }
    const res = await memoryApi.importMemory({ agent_id: props.agentId, memories: items })
    if (res.data.code === 1 && res.data.data) {
      const { imported, failed, errors } = res.data.data
      if (failed > 0) {
        ElMessage.warning(`导入 ${imported} 条，${failed} 条失败`)
        if (errors.length > 0) {
          console.error('导入失败详情:', errors)
        }
      } else {
        ElMessage.success(`成功导入 ${imported} 条记忆`)
      }
      loadMemories()
      loadStats()
    }
  } catch (e) {
    if (e instanceof SyntaxError) {
      ElMessage.error('文件格式错误，不是有效的 JSON')
    }
    console.error('导入失败详情:', e)
  } finally {
    importing.value = false
    input.value = ''
  }
}

async function handleDelete(memory: Memory): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除记忆「${memory.title}」？`, '提示', { type: 'warning' })
    await memoryApi.delete(memory.id)
    ElMessage.success('已删除')
    if (isSearchMode.value) {
      doSearch()
    } else {
      loadMemories()
    }
    loadStats()
  } catch {
    // cancelled
  }
}

function handleClose(): void {
  emit('update:visible', false)
}

watch(
  () => props.visible,
  visible => {
    if (visible) {
      currentPage.value = 1
      activeType.value = 'all'
      searchQuery.value = ''
      searchResults.value = []
      loadMemories()
      loadStats()
    }
  }
)

watch(
  () => props.agentId,
  () => {
    if (props.visible) {
      if (isSearchMode.value) {
        doSearch()
      } else {
        loadMemories()
      }
    }
  }
)
</script>

<template>
  <el-drawer
    :model-value="visible"
    title="记忆管理"
    direction="rtl"
    size="340px"
    :close-on-click-modal="true"
    @update:model-value="handleClose"
  >
    <div class="memory-panel">
      <div class="tier-actions">
        <el-button size="small" :icon="Download" :loading="exporting" @click="handleExport">
          导出
        </el-button>
        <el-button size="small" :icon="Upload" :loading="importing" @click="handleImportClick">
          导入
        </el-button>
      </div>
      <div class="tier-bar">
        <div class="tier-tabs">
          <div
            v-for="tier in tierOptions"
            :key="tier.value"
            :class="['tier-tab', { active: activeType === tier.value }]"
            :style="
              activeType === tier.value ? { color: tier.color, borderBottomColor: tier.color } : {}
            "
            @click="handleTypeChange(tier.value)"
          >
            {{ tier.label }}
            <span
              v-if="tier.value !== 'all' && tierStats[tier.value as 'hot' | 'warm' | 'cold']"
              class="tier-count"
            >
              {{ tierStats[tier.value as 'hot' | 'warm' | 'cold'] }}
            </span>
          </div>
        </div>
      </div>

      <div class="search-bar">
        <el-icon class="search-icon"><Search /></el-icon>
        <input
          v-model="searchQuery"
          class="search-input"
          placeholder="搜索记忆..."
          type="text"
          @keyup.enter="handleSearch"
        />
        <el-icon v-if="searchQuery" class="search-clear" @click="handleSearchClear">
          <CircleClose />
        </el-icon>
        <el-button size="small" type="primary" :loading="loading" @click="handleSearch">
          搜索
        </el-button>
      </div>

      <el-alert
        v-if="!embeddingAvailable"
        title="向量模型未配置，搜索功能降级为关键词匹配。请在设置中配置向量模型以启用语义搜索"
        type="warning"
        :closable="false"
        show-icon
      />

      <div v-if="memories.length > 0" class="batch-bar">
        <el-checkbox :model-value="isAllSelected" @change="handleSelectAll">全选</el-checkbox>
        <div class="batch-actions">
          <el-button
            type="primary"
            size="small"
            plain
            :disabled="selectedIds.length === 0"
            :loading="revectorizing"
            @click="handleRevectorizeSelected"
          >
            向量化 ({{ selectedIds.length }})
          </el-button>
          <el-button
            type="danger"
            size="small"
            :disabled="selectedIds.length === 0"
            @click="handleDeleteSelected"
          >
            删除 ({{ selectedIds.length }})
          </el-button>
        </div>
      </div>

      <div v-loading="loading" class="memory-list">
        <div v-if="displayList.length === 0 && !loading" class="empty-state">
          <span>{{ isSearchMode ? '未找到匹配的记忆' : '暂无记忆' }}</span>
        </div>
        <div
          v-for="item in displayList"
          :key="item.memory.id"
          :class="['memory-item', { selected: selectedIds.includes(item.memory.id) }]"
        >
          <div class="memory-header">
            <el-checkbox
              :model-value="selectedIds.includes(item.memory.id)"
              @change="handleSelectOne(item.memory.id)"
            />
            <el-tag
              :color="getTierColor(item.memory.memory_type)"
              effect="dark"
              size="small"
              style="border: none; min-width: 28px; text-align: center"
            >
              {{ getTierLabel(item.memory.memory_type) }}
            </el-tag>
            <span class="memory-title">{{ item.memory.title }}</span>
            <span v-if="item.score > 0" class="score-badge">
              {{ (item.score * 100).toFixed(0) }}%
            </span>
            <el-button
              :icon="Delete"
              link
              size="small"
              class="delete-btn"
              @click="handleDelete(item.memory)"
            />
          </div>
          <div :class="['memory-content', { collapsed: !expandedIds.has(item.memory.id) }]">
            {{ item.memory.content }}
          </div>
          <div class="expand-row" @click="toggleExpand(item.memory.id)">
            {{ expandedIds.has(item.memory.id) ? '收起' : '展开' }}
          </div>
          <div class="memory-meta">
            <span class="importance">{{ importanceStars(item.memory.importance) }}</span>
            <el-tag size="small" type="info" style="border: none">
              {{ getCategoryLabel(item.memory.category) }}
            </el-tag>
            <span v-if="item.memory.access_count > 0" class="access-count">
              访问 {{ item.memory.access_count }}
            </span>
          </div>
        </div>
      </div>

      <div v-if="!isSearchMode && total > pageSize" class="memory-pagination">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          size="small"
          @current-change="handlePageChange"
        />
      </div>
      <input ref="importFileRef" type="file" accept=".json" hidden @change="handleImportFile" />
    </div>
  </el-drawer>
</template>

<style scoped>
.memory-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.tier-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #ebeef5;
}

.tier-tabs {
  display: flex;
  gap: 4px;
}

.tier-actions {
  display: flex;
  gap: 6px;
  padding-right: 4px;
}

.tier-tab {
  padding: 8px 16px;
  font-size: 14px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  gap: 4px;
  user-select: none;
}

.tier-tab:hover {
  background: #f5f7fa;
}

.tier-tab.active {
  font-weight: 600;
}

.tier-count {
  font-size: 12px;
  opacity: 0.8;
}

.batch-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #ebeef5;
}

.search-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 12px;
  margin-top: 20px;
  border-bottom: 1px solid #ebeef5;
}

.search-icon {
  color: #a8abb2;
  font-size: 14px;
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  border: none;
  outline: none;
  font-size: 14px;
  padding: 0 4px;
  background: transparent;
  color: #303133;
  min-width: 0;
}

.search-input::placeholder {
  color: #c0c4cc;
}

.search-clear {
  color: #a8abb2;
  font-size: 14px;
  cursor: pointer;
  flex-shrink: 0;
}

.search-clear:hover {
  color: #909399;
}

.score-badge {
  font-size: 11px;
  color: #409eff;
  background: #ecf5ff;
  padding: 1px 6px;
  border-radius: 10px;
  flex-shrink: 0;
}

.batch-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
}

.memory-list {
  flex: 1;
  overflow-y: auto;
}

.empty-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
  color: #c0c4cc;
  font-size: 14px;
}

.memory-item {
  padding: 12px;
  border-bottom: 1px solid #f0f0f0;
  transition: background 0.15s;
}

.memory-item:last-child {
  border-bottom: none;
}

.memory-item.selected {
  background: #ecf5ff;
}

.memory-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.memory-title {
  flex: 1;
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.memory-content {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin-bottom: 6px;
  word-break: break-all;
}

.memory-content.collapsed {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.expand-row {
  font-size: 12px;
  color: #409eff;
  cursor: pointer;
  user-select: none;
  margin-bottom: 6px;
}

.expand-row:hover {
  color: #66b1ff;
}

.memory-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #909399;
}

.importance {
  color: #e6a23c;
  letter-spacing: 1px;
}

.access-count {
  margin-left: auto;
}

.delete-btn {
  opacity: 0;
  transition: opacity 0.2s;
  color: #f56c6c;
}

.memory-item:hover .delete-btn {
  opacity: 1;
}

.memory-pagination {
  padding: 12px 0;
  display: flex;
  justify-content: center;
}
</style>
