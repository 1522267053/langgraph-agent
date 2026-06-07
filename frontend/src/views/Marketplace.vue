<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Download, Refresh, Shop } from '@element-plus/icons-vue'
import { marketplaceApi } from '@/api/marketplace'
import { useMarketplaceStore } from '@/stores/marketplaceStore'

const store = useMarketplaceStore()
const statusLoaded = ref(false)

const activeType = ref('flow')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(12)
const resources = ref<any[]>([])
const total = ref(0)
const loading = ref(false)
const importing = ref<number | null>(null)

const typeTabs = [
  { label: '流程/Agent', key: 'flow' },
  { label: 'Skill', key: 'skill' }
]

onMounted(async () => {
  await store.loadStatus()
  statusLoaded.value = true
  if (store.connected) {
    await loadResources()
  }
})

async function loadResources() {
  loading.value = true
  try {
    const res = await marketplaceApi.listResources({
      resource_type: activeType.value,
      keyword: keyword.value || undefined,
      page: page.value,
      page_size: pageSize.value
    })
    const data = res.data.data
    resources.value = data?.items || []
    total.value = data?.total || 0
  } finally {
    loading.value = false
  }
}

function handleTypeChange() {
  page.value = 1
  keyword.value = ''
  loadResources()
}

function handleSearch() {
  page.value = 1
  loadResources()
}

function handlePageChange(p: number) {
  page.value = p
  loadResources()
}

function handleRefresh() {
  loadResources()
}

function formatFileSize(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

async function handleImport(resource: any) {
  await ElMessageBox.confirm(`确定要导入「${resource.name}」吗？`, '导入资源', { type: 'info' })
  importing.value = resource.id
  try {
    const res = await marketplaceApi.importResource(resource.id)
    const data = res.data.data
    if (data?.success) {
      if (data.warnings?.length) {
        let msgHtml = `<div style="max-height:300px;overflow-y:auto"><p style="font-weight:600;margin:0 0 8px">${data.message}</p><ul style="margin:0;padding-left:16px">`
        for (const w of data.warnings) {
          msgHtml += `<li style="color:#e6a23c;margin-bottom:4px">${w}</li>`
        }
        msgHtml += '</ul></div>'
        ElMessageBox.alert(msgHtml, '导入完成（有警告）', {
          dangerouslyUseHTMLString: true,
          confirmButtonText: '确定'
        })
      } else {
        ElMessage.success(data.message || '导入成功')
      }
    } else {
      ElMessage.error(data?.message || '导入失败')
    }
  } catch {
    ElMessage.error('导入失败')
  } finally {
    importing.value = null
  }
}

const isImporting = (id: number) => importing.value === id
</script>

<template>
  <div class="marketplace-page page">
    <div
      v-if="!statusLoaded"
      v-loading="true"
      class="disconnected-state"
      element-loading-text="正在连接市场..."
    ></div>

    <div v-else-if="!store.connected" class="disconnected-state">
      <div class="disconnected-card">
        <el-icon :size="48" color="#94a3b8"><Shop /></el-icon>
        <h2>资源市场未连接</h2>
        <p>请先在系统设置中配置市场服务器地址</p>
        <el-button type="primary" size="large" @click="$router.push('/settings')">
          前往设置
        </el-button>
      </div>
    </div>

    <template v-else>
      <div class="page-header">
        <h1 class="page-title">资源市场</h1>
        <el-button :icon="Refresh" :loading="loading" @click="handleRefresh">刷新</el-button>
      </div>

      <el-tabs v-model="activeType" class="marketplace-tabs" @tab-change="handleTypeChange">
        <el-tab-pane v-for="tab in typeTabs" :key="tab.key" :label="tab.label" :name="tab.key">
          <el-form
            inline
            class="search-bar"
            style="margin-top: 20px"
            @submit.prevent="handleSearch"
          >
            <el-form-item>
              <el-input
                v-model="keyword"
                placeholder="搜索资源名称..."
                clearable
                :prefix-icon="Search"
                style="width: 220px"
                @keyup.enter="handleSearch"
                @clear="handleSearch"
              />
            </el-form-item>
            <el-form-item>
              <el-button class="btn-search" :icon="Search" @click="handleSearch">搜索</el-button>
            </el-form-item>
          </el-form>

          <div v-loading="loading" class="card-panel card-panel-marketplace marketplace-container">
            <div class="resource-grid">
              <div v-for="item in resources" :key="item.id" class="resource-card">
                <div class="card-top">
                  <el-tag size="small" effect="dark" class="type-tag">
                    {{ item.resource_type }}
                  </el-tag>
                  <span v-if="item.category" class="card-category">{{ item.category }}</span>
                </div>
                <div class="card-middle">
                  <h3 class="card-title">{{ item.name }}</h3>
                  <p class="card-desc">{{ item.description || '暂无描述' }}</p>
                </div>
                <div class="card-bottom">
                  <div class="card-meta">
                    <span>{{ formatFileSize(item.file_size) }}</span>
                    <span>{{ item.download_count }} 次下载</span>
                  </div>
                  <el-button
                    type="primary"
                    size="small"
                    :icon="Download"
                    :loading="isImporting(item.id)"
                    @click="handleImport(item)"
                  >
                    导入
                  </el-button>
                </div>
              </div>
            </div>

            <el-empty
              v-if="!loading && resources.length === 0"
              description="暂无资源"
              class="empty-state"
            />

            <div v-if="total > 0" class="pagination">
              <el-pagination
                v-model:current-page="page"
                :page-size="pageSize"
                :total="total"
                layout="total, prev, pager, next"
                @current-change="handlePageChange"
              />
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<style scoped>
.disconnected-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.disconnected-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  text-align: center;
  padding: 48px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 20px;
  box-shadow:
    0 1px 3px rgba(0, 0, 0, 0.04),
    0 4px 12px rgba(0, 0, 0, 0.03);
}

.disconnected-card h2 {
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
  margin: 0;
}

.disconnected-card p {
  font-size: 14px;
  color: #64748b;
  margin: 0;
}

.marketplace-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.marketplace-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
  flex-shrink: 0;
}

.marketplace-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
  background: #e2e8f0;
}

.marketplace-tabs :deep(.el-tabs__item) {
  font-size: 14px;
  font-weight: 500;
  color: #64748b;
  height: 42px;
  line-height: 42px;
  padding: 0 20px;
  transition: color 0.2s;
}

.marketplace-tabs :deep(.el-tabs__item:hover) {
  color: #334155;
}

.marketplace-tabs :deep(.el-tabs__item.is-active) {
  color: #2563eb;
  font-weight: 600;
}

.marketplace-tabs :deep(.el-tabs__active-bar) {
  background-color: #2563eb;
  height: 2px;
  border-radius: 1px;
}

.marketplace-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.marketplace-container {
  flex: 1;
  min-height: calc(100vh - 280px);
  display: flex;
  flex-direction: column;
}

.resource-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  align-items: start;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.resource-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 16px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  flex-direction: column;
}

.resource-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 24px -8px rgba(226, 232, 240, 0.4);
  border-color: #cbd5e1;
}

.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.type-tag {
  background: #1e293b;
  border: none;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.card-category {
  font-size: 12px;
  color: #94a3b8;
  font-weight: 500;
}

.card-middle {
  flex: 1;
  margin-bottom: 14px;
}

.card-title {
  font-size: 15px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 6px;
  word-break: break-all;
  line-height: 1.4;
}

.card-desc {
  font-size: 13px;
  color: #64748b;
  margin: 0;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 12px;
  border-top: 1px solid #f1f5f9;
}

.card-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #94a3b8;
  font-weight: 500;
}

.pagination {
  padding: 16px 0 0;
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 768px) {
  .resource-grid {
    grid-template-columns: 1fr;
  }
}
</style>
