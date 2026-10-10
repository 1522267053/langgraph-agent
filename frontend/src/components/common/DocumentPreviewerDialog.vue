<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Download } from '@element-plus/icons-vue'
import request from '@/api/index'
import { isDocx, isPdf, isXlsx } from '@/utils/format'

export interface DocumentPreviewFile {
  id: number
  original_name: string
  mime_type: string
  preview_url?: string
}

const props = defineProps<{
  visible: boolean
  file: DocumentPreviewFile | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

const loading = ref(false)
const errorMsg = ref('')
const arrayBuffer = ref<ArrayBuffer | null>(null)
// xlsx：SheetNames → HTML 表格（table-layout:auto 内容自适应列宽），大工作簿按需生成
const sheetNames = ref<string[]>([])
const activeSheet = ref('')
const sheetHtml = ref<Record<string, string>>({})
// docx：docx-preview 渲染目标容器
const docxContainer = ref<HTMLElement | null>(null)

const docType = computed(() => {
  const f = props.file
  if (!f) return ''
  if (isXlsx(f.mime_type, f.original_name)) return 'xlsx'
  if (isDocx(f.mime_type, f.original_name)) return 'docx'
  if (isPdf(f.mime_type, f.original_name)) return 'pdf'
  return ''
})

// PDF 走浏览器原生渲染，必须 ?inline=1 去掉 attachment 头（file_api.py 支持）
const pdfUrl = computed(() =>
  props.file ? `/api/file/download/${props.file.id}?inline=1` : ''
)

// responseType 必须用 blob：axios 拦截器仅对 blob 跳过统一 code 校验，
// arraybuffer 会被当成 {code} 响应误判为失败
async function loadFile() {
  if (!props.file) return
  // PDF 由 iframe 自行加载，不取内容
  if (docType.value === 'pdf') return
  loading.value = true
  errorMsg.value = ''
  arrayBuffer.value = null
  sheetNames.value = []
  sheetHtml.value = {}
  try {
    const res = await request.get(`/file/download/${props.file.id}`, { responseType: 'blob' })
    arrayBuffer.value = await (res.data as Blob).arrayBuffer()
    if (docType.value === 'xlsx') {
      await parseXlsx(arrayBuffer.value)
    } else if (docType.value === 'docx') {
      await renderDocx(arrayBuffer.value)
    }
    loading.value = false
  } catch {
    errorMsg.value = '文件加载失败，请尝试下载后查看'
    loading.value = false
  }
}

async function parseXlsx(buf: ArrayBuffer) {
  try {
    const XLSX = await import('xlsx')
    workbook = XLSX.read(buf)
    sheetNames.value = workbook.SheetNames
    if (!sheetNames.value.length) throw new Error('empty')
    await switchSheet(workbook.SheetNames[0])
  } catch {
    errorMsg.value = '表格解析失败，请尝试下载后查看'
  }
}

// 当前工作簿（非响应式，sheet 切换复用，避免重复解析）
let workbook: Awaited<ReturnType<typeof import('xlsx')['read']>> | null = null

// 懒生成：首次切到的 sheet 才转 HTML，避免大工作簿全量渲染卡顿
async function switchSheet(name: string) {
  activeSheet.value = name
  if (sheetHtml.value[name] || !workbook) return
  const XLSX = await import('xlsx')
  // header/footer 置空只输出 <table> 片段
  sheetHtml.value[name] = XLSX.utils.sheet_to_html(workbook.Sheets[name], {
    header: '',
    footer: ''
  })
}

async function renderDocx(buf: ArrayBuffer) {
  try {
    const { renderAsync } = await import('docx-preview')
    // 容器由 v-else-if 渲染，nextTick 等挂载后再注入
    await nextTick()
    if (docxContainer.value) {
      await renderAsync(buf, docxContainer.value)
    }
  } catch {
    errorMsg.value = '文档解析失败，请尝试下载后查看'
  }
}

function handleDownload() {
  if (props.file) window.open(`/api/file/download/${props.file.id}`, '_blank')
}

watch(
  () => [props.visible, props.file?.id] as const,
  ([visible]) => {
    if (visible && props.file && docType.value) {
      loadFile()
    }
    if (!visible) {
      // 关闭即释放文件内容与渲染产物
      arrayBuffer.value = null
      workbook = null
      sheetNames.value = []
      sheetHtml.value = {}
      activeSheet.value = ''
      errorMsg.value = ''
      loading.value = false
    }
  }
)
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="file?.original_name || '文档预览'"
    width="90%"
    top="4vh"
    append-to-body
    destroy-on-close
    class="doc-preview-dialog"
    @update:model-value="v => emit('update:visible', v)"
  >
    <div v-loading="loading" class="doc-preview-body">
      <div v-if="errorMsg" class="doc-preview-error">
        <el-empty :description="errorMsg">
          <el-button type="primary" :icon="Download" @click="handleDownload">下载文件</el-button>
        </el-empty>
      </div>
      <!-- PDF：浏览器原生渲染（同源 iframe + 后端 inline 接口） -->
      <iframe
        v-else-if="docType === 'pdf'"
        :src="pdfUrl"
        class="doc-preview-iframe"
        title="PDF 预览"
      />
      <!-- XLSX：SheetJS 转 HTML 表格，多 sheet 标签切换 -->
      <template v-else-if="docType === 'xlsx' && sheetNames.length">
        <el-tabs v-model="activeSheet" class="sheet-tabs" @tab-change="switchSheet">
          <el-tab-pane v-for="name in sheetNames" :key="name" :label="name" :name="name" />
        </el-tabs>
        <div class="sheet-scroll">
          <!-- sheet_to_html 输出受控表格结构（单元格文本已转义），非用户可控 HTML -->
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div class="sheet-table" v-html="sheetHtml[activeSheet]" />
        </div>
      </template>
      <!-- DOCX：docx-preview 渲染 -->
      <div v-else-if="docType === 'docx'" ref="docxContainer" class="docx-body" />
    </div>
  </el-dialog>
</template>

<style scoped>
.doc-preview-body {
  height: calc(88vh - 120px);
  overflow: auto;
}

.doc-preview-error {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.doc-preview-iframe {
  width: 100%;
  height: 100%;
  border: 0;
}

.sheet-tabs {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--el-bg-color);
}

.sheet-scroll {
  overflow: auto;
}

/* sheet_to_html 输出无样式表格，这里补齐 Excel 观感；auto 布局按内容自适应列宽 */
.sheet-table :deep(table) {
  table-layout: auto;
  border-collapse: collapse;
}

.sheet-table :deep(td),
.sheet-table :deep(th) {
  border: 1px solid #dcdfe6;
  padding: 4px 10px;
  font-size: 13px;
  white-space: nowrap;
}

.sheet-table :deep(tr:first-child td),
.sheet-table :deep(tr:first-child th) {
  background: #f5f7fa;
  font-weight: 600;
}

.docx-body {
  min-height: 200px;
}
</style>

<style>
/* 非 scoped：el-dialog 挂载在 body 下，需要全局作用域控制头部宽度 */
.doc-preview-dialog {
  max-width: 1280px;
}

/* docx-preview 默认灰底分页样式，弹窗内贴合主题 */
.docx-body .docx-wrapper {
  background: transparent;
  padding: 8px 0;
}
</style>
