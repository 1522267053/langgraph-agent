<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import {
  Plus,
  Upload,
  ArrowLeft,
  View,
  Edit,
  Delete,
  Download,
  Refresh,
  SetUp
} from '@element-plus/icons-vue'
import { knowledgeBaseApi, knowledgeDocumentApi } from '@/api/knowledge'
import { configApi } from '@/api/config'
import ActionColumn from '@/components/common/ActionColumn.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import type {
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeDocumentSegment,
  KnowledgeBaseStatus,
  SegmentSearchResult
} from '@/types/knowledge'
import type { PaginatedResponse } from '@/types/common'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadFiles } from 'element-plus'

const { isMobile } = useIsMobile()

const kbLoading = ref(false)
const docLoading = ref(false)
const uploadLoading = ref(false)
const vectorizeLoading = ref<number | null>(null)
const kbTableData = ref<KnowledgeBase[]>([])
const docTableData = ref<KnowledgeDocument[]>([])
const kbTotal = ref(0)
const docTotal = ref(0)
const selectedKb = ref<KnowledgeBase | null>(null)

const kbQueryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    name: '',
    status: undefined as KnowledgeBaseStatus | undefined
  }
})

const docQueryParams = reactive({
  page: 1,
  page_size: 10,
  condition: {
    knowledge_base_id: undefined as number | undefined,
    title: ''
  }
})

const kbDialogVisible = ref(false)
const uploadDialogVisible = ref(false)
const segmentDialogVisible = ref(false)
const contentDialogVisible = ref(false)
const kbFormRef = ref()
const uploadRef = ref()
const fileList = ref<UploadFiles>([])
const embeddingAvailable = ref(true)
const currentSegments = ref<KnowledgeDocumentSegment[]>([])
const segmentDocTitle = ref('')
const currentContent = ref('')
const contentDocTitle = ref('')
const searchQuery = ref('')
const searchResults = ref<SegmentSearchResult[]>([])
const activeTab = ref('documents')
const searchLoading = ref(false)

const kbForm = reactive({
  id: undefined as number | undefined,
  name: '',
  description: '',
  status: 1 as KnowledgeBaseStatus
})

const statusOptions = [
  { label: '启用', value: 1 },
  { label: '禁用', value: 0 }
]

const acceptFileTypes = '.txt,.md,.docx,.pdf,.xlsx'

const kbRules = {
  name: [{ required: true, message: '请输入知识库名称', trigger: 'blur' }]
}

async function loadKbData() {
  kbLoading.value = true
  try {
    const res = await knowledgeBaseApi.page(kbQueryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<KnowledgeBase>
      kbTableData.value = data.items
      kbTotal.value = data.total
    }
  } finally {
    kbLoading.value = false
  }
}

async function loadDocData() {
  if (!selectedKb.value) {
    docTableData.value = []
    docTotal.value = 0
    return
  }
  docLoading.value = true
  docQueryParams.condition.knowledge_base_id = selectedKb.value.id
  try {
    const res = await knowledgeDocumentApi.page(docQueryParams)
    if (res.data.code === 1) {
      const data = res.data.data as PaginatedResponse<KnowledgeDocument>
      docTableData.value = data.items
      docTotal.value = data.total
    }
  } finally {
    docLoading.value = false
  }
}

function handleKbSearch() {
  kbQueryParams.page = 1
  loadKbData()
}

function handleDocSearch() {
  docQueryParams.page = 1
  loadDocData()
}

function handleKbReset() {
  kbQueryParams.condition.name = ''
  kbQueryParams.condition.status = undefined
  kbQueryParams.page = 1
  loadKbData()
}

function handleDocReset() {
  docQueryParams.condition.title = ''
  docQueryParams.page = 1
  loadDocData()
}

function openKbDetail(row: KnowledgeBase) {
  selectedKb.value = row
  docQueryParams.page = 1
  docQueryParams.condition.title = ''
  activeTab.value = 'documents'
  clearSearch()
  loadDocData()
}

function backToList() {
  selectedKb.value = null
  docTableData.value = []
  docTotal.value = 0
  clearSearch()
}

function handleKbPageChange(page: number) {
  kbQueryParams.page = page
  loadKbData()
}

function handleKbSizeChange(size: number) {
  kbQueryParams.page_size = size
  kbQueryParams.page = 1
  loadKbData()
}

function handleDocPageChange(page: number) {
  docQueryParams.page = page
  loadDocData()
}

function handleDocSizeChange(size: number) {
  docQueryParams.page_size = size
  docQueryParams.page = 1
  loadDocData()
}

function openKbDialog(row?: KnowledgeBase) {
  if (row) {
    kbForm.id = row.id
    kbForm.name = row.name || ''
    kbForm.description = row.description || ''
    kbForm.status = row.status ?? 1
  } else {
    kbForm.id = undefined
    kbForm.name = ''
    kbForm.description = ''
    kbForm.status = 1
  }
  kbDialogVisible.value = true
}

function openUploadDialog() {
  fileList.value = []
  uploadDialogVisible.value = true
}

function handleFileChange(uploadFile: UploadFile, uploadFiles: UploadFiles) {
  fileList.value = uploadFiles
}

function handleFileRemove(uploadFile: UploadFile, uploadFiles: UploadFiles) {
  fileList.value = uploadFiles
}

async function handleUpload() {
  if (fileList.value.length === 0) {
    ElMessage.warning('请选择要上传的文件')
    return
  }

  uploadLoading.value = true
  try {
    let successCount = 0

    for (const uploadFile of fileList.value) {
      if (!uploadFile.raw) {
        ElMessage.warning(`文件 ${uploadFile.name} 无效`)
        continue
      }
      const res = await knowledgeDocumentApi.upload(uploadFile.raw, selectedKb.value!.id!)
      if (res.data.code === 1) {
        successCount++
      }
    }

    ElMessage.success(`已上传 ${successCount} 个文件，后台处理中`)
    uploadRef.value?.clearFiles()
    fileList.value = []
    uploadDialogVisible.value = false
    loadDocData()
  } catch {
    // error handled by interceptor
  } finally {
    uploadLoading.value = false
  }
}

async function viewSegments(row: KnowledgeDocument) {
  segmentDocTitle.value = row.title || ''
  segmentDialogVisible.value = true
  try {
    const res = await knowledgeDocumentApi.getSegments(row.id!)
    if (res.data.code === 1) {
      currentSegments.value = res.data.data || []
    }
  } catch {
    // error handled by interceptor
  }
}

function handleDownload(row: KnowledgeDocument) {
  const url = knowledgeDocumentApi.getDownloadUrl(row.id!)
  window.open(url, '_blank')
}

async function viewContent(row: KnowledgeDocument) {
  contentDocTitle.value = row.title || ''
  contentDialogVisible.value = true
  try {
    const res = await knowledgeDocumentApi.getContent(row.id!)
    if (res.data.code === 1) {
      currentContent.value = res.data.data?.content || ''
    }
  } catch {
    // error handled by interceptor
  }
}

async function handleVectorize(row: KnowledgeDocument) {
  try {
    await ElMessageBox.confirm(
      `确定要重新向量化文档「${row.title}」吗？向量化期间文档不可操作。`,
      '提示',
      {
        type: 'warning'
      }
    )
    vectorizeLoading.value = row.id!
    const res = await knowledgeDocumentApi.vectorizeDocument(row.id!, true)
    if (res.data.code === 1) {
      ElMessage.success('重新向量化任务已提交，后台处理中')
      loadDocData()
    }
  } catch (error) {
    if (error !== 'cancel') {
      // error handled by interceptor
    }
  } finally {
    vectorizeLoading.value = null
  }
}

async function submitKbForm() {
  await kbFormRef.value.validate()
  if (kbForm.id) {
    await knowledgeBaseApi.update({
      id: kbForm.id,
      name: kbForm.name,
      description: kbForm.description,
      status: kbForm.status
    })
    ElMessage.success('更新成功')
  } else {
    await knowledgeBaseApi.create({
      name: kbForm.name,
      description: kbForm.description,
      status: kbForm.status
    })
    ElMessage.success('创建成功')
  }
  kbDialogVisible.value = false
  loadKbData()
}

function handleDeleteKb(row: KnowledgeBase) {
  ElMessageBox.confirm(
    `确定要删除知识库「${row.name}」吗？删除后该知识库下的所有文档也将被删除。`,
    '提示',
    {
      type: 'warning'
    }
  )
    .then(async () => {
      await knowledgeBaseApi.delete(row.id!)
      ElMessage.success('删除成功')
      if (selectedKb.value?.id === row.id) {
        selectedKb.value = null
      }
      loadKbData()
    })
    .catch(() => {})
}

function handleDeleteDoc(row: KnowledgeDocument) {
  ElMessageBox.confirm(`确定要删除文档「${row.title}」吗？`, '提示', {
    type: 'warning'
  })
    .then(async () => {
      await knowledgeDocumentApi.delete(row.id!)
      ElMessage.success('删除成功')
      loadDocData()
    })
    .catch(() => {})
}

async function handleReprocess(row: KnowledgeDocument) {
  try {
    await knowledgeDocumentApi.reprocess(row.id!)
    ElMessage.success('已重置，等待后台处理')
    loadDocData()
  } catch {
    // error handled by interceptor
  }
}

async function handleSearchSegments() {
  if (!searchQuery.value.trim()) return
  searchLoading.value = true
  try {
    const res = await knowledgeDocumentApi.searchSegments({
      knowledge_base_id: selectedKb.value!.id!,
      query: searchQuery.value.trim(),
      top_k: 10
    })
    if (res.data.code === 1) {
      searchResults.value = res.data.data || []
    }
  } catch {
    // error handled by interceptor
  } finally {
    searchLoading.value = false
  }
}

function clearSearch() {
  searchQuery.value = ''
  searchResults.value = []
}

function handleTabChange(tab: string) {
  if (tab !== 'search' && searchQuery.value) {
    clearSearch()
  }
}

function formatScore(score: number): string {
  return (score * 100).toFixed(1) + '%'
}

function getStatusText(status: number | undefined): string {
  return status === 1 ? '启用' : '禁用'
}

function getStatusType(status: number | undefined): '' | 'success' | 'danger' {
  return status === 1 ? 'success' : 'danger'
}

function getFileTypeColor(type: string | undefined): string {
  switch (type) {
    case 'txt':
      return ''
    case 'md':
      return 'success'
    case 'docx':
      return 'primary'
    case 'pdf':
      return 'danger'
    default:
      return ''
  }
}

async function checkEmbedding(): Promise<void> {
  try {
    const res = await configApi.getConfig()
    embeddingAvailable.value = !!res.data.data?.embedding_api_key_masked
  } catch {
    embeddingAvailable.value = false
  }
}

function getKbActions(_row: any) {
  return [
    { key: 'view', label: '查看', icon: View, btnClass: 'action-view' },
    { key: 'edit', label: '编辑', icon: Edit, btnClass: 'action-edit' },
    { key: 'delete', label: '删除', icon: Delete, btnClass: 'action-delete', danger: true }
  ]
}

function onKbAction(row: any, key: string) {
  switch (key) {
    case 'view':
      openKbDetail(row)
      break
    case 'edit':
      openKbDialog(row)
      break
    case 'delete':
      handleDeleteKb(row)
      break
  }
}

function getDocActions(row: any) {
  return [
    { key: 'view', label: '原文', icon: View, btnClass: 'action-view' },
    { key: 'download', label: '下载', icon: Download, btnClass: 'action-download' },
    {
      key: 'retry',
      label: '重试',
      icon: Refresh,
      btnClass: 'action-refresh',
      visible: row.processing_status === 3
    },
    {
      key: 'vectorize',
      label: '向量化',
      icon: SetUp,
      btnClass: 'action-edit',
      disabled: row.processing_status !== 2
    },
    { key: 'delete', label: '删除', icon: Delete, btnClass: 'action-delete', danger: true }
  ]
}

function onDocAction(row: any, key: string) {
  switch (key) {
    case 'view':
      viewContent(row)
      break
    case 'download':
      handleDownload(row)
      break
    case 'retry':
      handleReprocess(row)
      break
    case 'vectorize':
      handleVectorize(row)
      break
    case 'delete':
      handleDeleteDoc(row)
      break
  }
}

onMounted(() => {
  loadKbData()
  checkEmbedding()
})
</script>

<template>
  <div class="knowledge-page page">
    <!-- ---- 知识库列表视图 ---- -->
    <template v-if="!selectedKb">
      <div class="page-header">
        <h1 class="page-title">知识库管理</h1>
        <el-button type="primary" :icon="Plus" @click="openKbDialog()">新建知识库</el-button>
      </div>

      <el-alert
        v-if="!embeddingAvailable"
        title="向量模型未配置，文档无法自动向量化。请在设置中配置向量模型"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />

      <div class="search-bar">
        <el-form inline>
          <el-form-item label="名称">
            <el-input
              v-model="kbQueryParams.condition.name"
              placeholder="搜索知识库"
              clearable
              @keyup.enter="handleKbSearch"
            />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="kbQueryParams.condition.status" placeholder="全部" clearable>
              <el-option label="启用" :value="1" />
              <el-option label="禁用" :value="0" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button class="btn-search" @click="handleKbSearch">查询</el-button>
            <el-button class="btn-reset" @click="handleKbReset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <div v-loading="kbLoading" class="card-panel kb-list-panel">
        <el-table :data="kbTableData" stripe>
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="name" label="名称" min-width="100" show-overflow-tooltip>
            <template #default="{ row }">
              <el-link type="primary" underline="hover" @click="openKbDetail(row)">
                {{ row.name }}
              </el-link>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="150" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="130">
            <template #default="{ row }">
              <el-tag :type="getStatusType(row.status)" size="small">
                {{ getStatusText(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="modify_time" label="更新时间" width="180" />
          <el-table-column label="操作" :width="isMobile ? 60 : 160" fixed="right">
            <template #default="{ row }">
              <ActionColumn :actions="getKbActions(row)" @action="onKbAction(row, $event)" />
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination">
          <el-pagination
            v-model:current-page="kbQueryParams.page"
            v-model:page-size="kbQueryParams.page_size"
            :total="kbTotal"
            :page-sizes="[10, 20, 50]"
            layout="total, sizes, prev, pager, next, jumper"
            @current-change="handleKbPageChange"
            @size-change="handleKbSizeChange"
          />
        </div>
      </div>
    </template>

    <!-- ---- 文档详情视图 ---- -->
    <template v-else>
      <div class="detail-header">
        <div class="detail-breadcrumb">
          <el-link underline="never" @click="backToList">
            <el-icon><ArrowLeft /></el-icon>
            知识库
          </el-link>
          <span class="breadcrumb-sep">/</span>
          <span class="breadcrumb-current">{{ selectedKb.name }}</span>
        </div>
        <div class="detail-actions">
          <el-button type="primary" :icon="Upload" @click="openUploadDialog">上传文档</el-button>
        </div>
      </div>

      <el-alert
        v-if="!embeddingAvailable"
        title="向量模型未配置，文档无法自动向量化。请在设置中配置向量模型"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />

      <div class="card-panel doc-detail-panel">
        <el-tabs v-model="activeTab" class="kb-tabs" @tab-change="handleTabChange">
          <el-tab-pane label="文档列表" name="documents">
            <div class="inner-search-bar">
              <el-form inline>
                <el-form-item label="标题">
                  <el-input
                    v-model="docQueryParams.condition.title"
                    placeholder="搜索文档"
                    clearable
                    @keyup.enter="handleDocSearch"
                  />
                </el-form-item>
                <el-form-item>
                  <el-button class="btn-search" @click="handleDocSearch">查询</el-button>
                  <el-button class="btn-reset" @click="handleDocReset">重置</el-button>
                </el-form-item>
              </el-form>
            </div>

            <div v-loading="docLoading" class="table-container">
              <el-table :data="docTableData" stripe>
                <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
                <el-table-column prop="file_type" label="类型" width="90">
                  <template #default="{ row }">
                    <el-tag :type="getFileTypeColor(row.file_type)" size="small">
                      {{ row.file_type?.toUpperCase() }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="word_count" label="字数" width="90" />
                <el-table-column prop="segment_count" label="分段" width="90">
                  <template #default="{ row }">
                    <el-link
                      v-if="row.segment_count && row.segment_count > 0"
                      type="primary"
                      underline="hover"
                      @click="viewSegments(row)"
                    >
                      {{ row.segment_count }}
                    </el-link>
                    <span v-else>0</span>
                  </template>
                </el-table-column>
                <el-table-column prop="processing_status" label="状态" width="120">
                  <template #default="{ row }">
                    <el-tag v-if="row.processing_status === 2" type="success" size="small">
                      已完成
                    </el-tag>
                    <el-tag v-else-if="row.processing_status === 1" size="small">处理中</el-tag>
                    <el-tag v-else-if="row.processing_status === 4" type="warning" size="small">
                      重新向量化中
                    </el-tag>
                    <el-tooltip
                      v-else-if="row.processing_status === 3"
                      :content="row.error_message || '处理失败'"
                      placement="top"
                    >
                      <el-tag type="danger" size="small">失败</el-tag>
                    </el-tooltip>
                    <el-tag v-else type="info" size="small">待处理</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="create_time" label="创建时间" width="170" />
                <el-table-column label="操作" :width="isMobile ? 60 : 220" fixed="right">
                  <template #default="{ row }">
                    <ActionColumn
                      :actions="getDocActions(row)"
                      @action="onDocAction(row, $event)"
                    />
                  </template>
                </el-table-column>
              </el-table>

              <div class="pagination">
                <el-pagination
                  v-model:current-page="docQueryParams.page"
                  v-model:page-size="docQueryParams.page_size"
                  :total="docTotal"
                  :page-sizes="[10, 20, 50]"
                  layout="total, sizes, prev, pager, next, jumper"
                  @current-change="handleDocPageChange"
                  @size-change="handleDocSizeChange"
                />
              </div>
            </div>
          </el-tab-pane>

          <el-tab-pane label="分段搜索" name="search">
            <div class="inner-search-bar">
              <el-form inline>
                <el-form-item>
                  <el-input
                    v-model="searchQuery"
                    placeholder="输入问题，语义搜索知识库分段"
                    clearable
                    @keyup.enter="handleSearchSegments"
                    @clear="clearSearch"
                  />
                </el-form-item>
                <el-form-item>
                  <el-button
                    class="btn-search"
                    :loading="searchLoading"
                    @click="handleSearchSegments"
                  >
                    搜索
                  </el-button>
                </el-form-item>
              </el-form>
            </div>

            <div v-loading="searchLoading" class="search-results">
              <div v-if="searchResults.length === 0 && !searchLoading" class="empty-state">
                <el-empty description="输入问题进行语义搜索" />
              </div>
              <div v-for="item in searchResults" :key="item.segment_id" class="search-result-item">
                <div class="search-result-header">
                  <span class="search-result-doc">{{ item.document_title }}</span>
                  <span v-if="item.title_text" class="search-result-title">
                    / {{ item.title_text }}
                  </span>
                  <el-tag size="small" type="success" class="search-result-score">
                    {{ formatScore(item.score) }}
                  </el-tag>
                </div>
                <div class="search-result-content">{{ item.content }}</div>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </template>

    <!-- ---- 弹窗 ---- -->

    <el-dialog
      v-model="kbDialogVisible"
      :title="kbForm.id ? '编辑知识库' : '新建知识库'"
      width="500px"
      @keyup.enter="submitKbForm"
    >
      <el-form
        ref="kbFormRef"
        :model="kbForm"
        :rules="kbRules"
        label-width="80px"
        @submit.prevent="submitKbForm"
      >
        <el-form-item label="名称" prop="name">
          <el-input v-model="kbForm.name" placeholder="请输入知识库名称" />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="kbForm.description"
            type="textarea"
            :rows="3"
            placeholder="请输入描述"
          />
        </el-form-item>
        <el-form-item label="状态" prop="status">
          <el-radio-group v-model="kbForm.status">
            <el-radio v-for="item in statusOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="kbDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitKbForm">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="uploadDialogVisible" title="上传文档" width="500px">
      <el-upload
        ref="uploadRef"
        drag
        multiple
        :auto-upload="false"
        :accept="acceptFileTypes"
        :on-change="handleFileChange"
        :on-remove="handleFileRemove"
      >
        <el-icon class="el-icon--upload"><upload /></el-icon>
        <div class="el-upload__text">
          拖拽文件到此处或
          <em>点击上传</em>
        </div>
        <template #tip>
          <div class="el-upload__tip">支持 txt、md、docx、pdf、xlsx 格式文件，可多选</div>
        </template>
      </el-upload>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploadLoading" @click="handleUpload">上传</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="segmentDialogVisible"
      :title="`分段详情 - ${segmentDocTitle}`"
      width="800px"
    >
      <div class="segment-list">
        <div v-for="(segment, index) in currentSegments" :key="segment.id" class="segment-item">
          <div class="segment-header">
            <span class="segment-index">第 {{ index + 1 }} 段</span>
            <span v-if="segment.title" class="segment-title">{{ segment.title }}</span>
            <span class="segment-word-count">{{ segment.word_count }} 字</span>
          </div>
          <div class="segment-content">{{ segment.content }}</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="segmentDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="contentDialogVisible" :title="`原文 - ${contentDocTitle}`" width="800px">
      <div class="content-viewer">
        <pre class="content-text">{{ currentContent }}</pre>
      </div>
      <template #footer>
        <el-button @click="contentDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.knowledge-page {
  min-height: 0;
}

/* ---- 详情视图头部 ---- */

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.detail-breadcrumb {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 15px;
}

.detail-breadcrumb .el-link {
  font-size: 15px;
  display: flex;
  align-items: center;
  gap: 4px;
  color: #64748b;
}

.detail-breadcrumb .el-link:hover {
  color: #2563eb;
}

.breadcrumb-sep {
  color: #cbd5e1;
  margin: 0 4px;
}

.breadcrumb-current {
  font-weight: 600;
  color: #0f172a;
}

.detail-actions {
  display: flex;
  gap: 8px;
}

/* ---- 卡片面板 ---- */

.kb-list-panel,
.doc-detail-panel {
  flex: 1;
  min-height: 0;
}

.doc-detail-panel {
  display: flex;
  flex-direction: column;
}

/* ---- 搜索栏 ---- */

.inner-search-bar {
  padding: 12px 0;
  border-bottom: 1px solid #f1f5f9;
}

/* ---- 表格 ---- */

.table-container {
  min-height: 0;
}

.pagination {
  padding: 12px 16px;
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
}

/* ---- Tabs ---- */

.kb-tabs {
  padding: 0 16px;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.kb-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
  flex-shrink: 0;
}

.kb-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
}

.kb-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.kb-tabs :deep(.el-tab-pane) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* ---- 搜索结果 ---- */

.search-results {
  flex: 1;
  min-height: 200px;
  overflow-y: auto;
  padding: 12px 16px;
}

.search-result-item {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
}

.search-result-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
}

.search-result-doc {
  font-weight: 500;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}

.search-result-title {
  color: #909399;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}

.search-result-score {
  margin-left: auto;
  flex-shrink: 0;
}

.search-result-content {
  padding: 12px;
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}

/* ---- 分段详情 ---- */

.segment-list {
  max-height: 60vh;
  overflow-y: auto;
}

.segment-item {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
}

.segment-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
}

.segment-index {
  font-weight: 500;
  color: #409eff;
}

.segment-title {
  flex: 1;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.segment-word-count {
  font-size: 12px;
  color: #909399;
}

.segment-content {
  padding: 12px;
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}

/* ---- 原文查看 ---- */

.content-viewer {
  max-height: 60vh;
  overflow-y: auto;
  background: #f5f7fa;
  border-radius: 8px;
}

.content-text {
  margin: 0;
  padding: 16px;
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
}

@media (max-width: 768px) {
  .knowledge-page {
    min-height: auto;
  }

  .detail-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
}
</style>
