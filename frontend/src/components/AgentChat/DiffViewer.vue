<template>
  <!--
    文件 diff 渲染器：jsdiff 生成补丁 + diff2html 渲染（GitHub 风格）
    - 双栏/单栏切换、滚动同步、行内字符级差异高亮、语法高亮（highlight.js）
    - 默认紧凑视图（context=3），可切换显示完整文件
    - diff2html 相关 JS/CSS 均为弹窗打开时动态加载，不进主包
    入参：backup（旧文本）+ current（新文本）+ is_binary + backup_missing + change_type
  -->
  <div v-if="!isBinary" class="diff-viewer">
    <div v-if="backupMissing" class="diff-banner">
      <el-icon><Warning /></el-icon>
      <span>备份已过期或不存在（&gt;7 天），无法对比修改前内容</span>
    </div>
    <div v-else-if="changeType === 'create'" class="diff-banner create">
      <el-icon><Plus /></el-icon>
      <span>新建文件（无修改前内容）</span>
    </div>
    <div v-else-if="changeType === 'delete'" class="diff-banner delete">
      <el-icon><Delete /></el-icon>
      <span>文件已删除（无法对比当前内容）</span>
    </div>

    <div v-if="!backupMissing && showToolbar" class="diff-toolbar">
      <el-radio-group v-model="viewMode" size="small">
        <el-radio-button value="side-by-side">双栏</el-radio-button>
        <el-radio-button value="line-by-line">单栏</el-radio-button>
      </el-radio-group>
      <el-checkbox v-model="showFullFile" size="small">显示完整文件</el-checkbox>
      <span v-if="stats" class="diff-stats-inline">
        <span class="add">+{{ stats.added }}</span>
        <span class="del">-{{ stats.removed }}</span>
      </span>
    </div>

    <div v-if="renderError" class="diff-empty">{{ renderError }}</div>
    <div v-else-if="renderLoading" class="diff-empty">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>渲染 diff 中…</span>
    </div>
    <div v-show="!renderLoading" ref="containerRef" class="d2h-mount"></div>
    <div v-if="isEmptyDiff" class="diff-empty">
      <el-empty description="两个版本内容一致" :image-size="60" />
    </div>
  </div>
  <div v-else class="diff-binary">
    <el-icon><Document /></el-icon>
    <span>二进制文件，无法显示 diff</span>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Warning, Plus, Delete, Document, Loading } from '@element-plus/icons-vue'
// d2h 结构样式必须静态导入（动态 import 纯 CSS 在 dev/部分构建下不注入，
// 会导致 diff 表格裸渲染：行背景透明、左右 pane 堆叠溢出弹窗）
import 'diff2html/bundles/css/diff2html.min.css'

interface Props {
  backupContent: string
  currentContent: string
  isBinary: boolean
  backupMissing: boolean
  changeType: 'create' | 'modify' | 'delete' | string
  /** 初始视图模式（工具块内联场景用单栏） */
  defaultViewMode?: 'side-by-side' | 'line-by-line'
  /** 是否显示工具栏（视图切换 + 完整文件开关） */
  showToolbar?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  defaultViewMode: 'side-by-side',
  showToolbar: true
})

const containerRef = ref<HTMLElement | null>(null)
const viewMode = ref<'side-by-side' | 'line-by-line'>(props.defaultViewMode)
const showFullFile = ref(false)
const renderError = ref('')
const renderLoading = ref(false)
const stats = ref<{ added: number; removed: number } | null>(null)

const isEmptyDiff = computed(
  () => !renderError.value && !renderLoading.value && stats.value === null
)

/** 紧凑视图上下文行数：改动块外保留 3 行 */
const COMPACT_CONTEXT = 3
/** 完整文件视图的 context：取超大值等效于不裁剪 */
const FULL_CONTEXT = 1_000_000

let renderSeq = 0

/** 动态加载 diff 渲染 JS（diff2html-ui 静态集成 highlight.js，跟随弹窗 chunk 懒加载） */
async function loadDeps() {
  const [{ createTwoFilesPatch, diffLines }, { Diff2HtmlUI }] = await Promise.all([
    import('diff'),
    import('diff2html/lib/ui/js/diff2html-ui')
  ])
  return { createTwoFilesPatch, diffLines, Diff2HtmlUI }
}

async function render(): Promise<void> {
  const el = containerRef.value
  if (props.isBinary || props.backupMissing || !el) return
  const seq = ++renderSeq
  renderError.value = ''
  renderLoading.value = true
  try {
    const { createTwoFilesPatch, diffLines, Diff2HtmlUI } = await loadDeps()
    if (seq !== renderSeq || !containerRef.value) return

    const oldStr = props.changeType === 'create' ? '' : props.backupContent
    const newStr = props.changeType === 'delete' ? '' : props.currentContent

    // 统计新增/删除行数（jsdiff 行级分块）
    let added = 0
    let removed = 0
    for (const part of diffLines(oldStr, newStr)) {
      if (part.added) added += part.count || 0
      else if (part.removed) removed += part.count || 0
    }
    stats.value = added + removed > 0 ? { added, removed } : null

    const patch = createTwoFilesPatch(
      props.changeType === 'create' ? 'dev/null' : 'a/backup',
      props.changeType === 'delete' ? 'dev/null' : 'b/current',
      oldStr,
      newStr,
      undefined,
      undefined,
      { context: showFullFile.value ? FULL_CONTEXT : COMPACT_CONTEXT }
    )

    el.innerHTML = ''
    if (!patch || !patch.trim()) {
      stats.value = null
      return
    }

    const ui = new Diff2HtmlUI(el, patch, {
      drawFileList: false,
      outputFormat: viewMode.value,
      showFiles: false,
      matching: 'words',
      synchronisedScroll: true,
      highlight: true,
      renderNothingWhenEmpty: false,
      colorScheme: 'light'
    })
    ui.draw()
  } catch (e) {
    if (seq === renderSeq) {
      renderError.value = `diff 渲染失败：${e instanceof Error ? e.message : String(e)}`
    }
  } finally {
    if (seq === renderSeq) renderLoading.value = false
  }
}

watch(
  [
    () => props.backupContent,
    () => props.currentContent,
    () => props.isBinary,
    () => props.backupMissing,
    () => props.changeType,
    viewMode,
    showFullFile
  ],
  () => void render()
)

onMounted(() => void render())
</script>

<style scoped lang="scss">
.diff-viewer {
  // 定位祖先兜底：确保 d2h 绝对定位的行号以本组件为包含块链的一部分，
  // 与滚动容器（.dialog-diff-body，同样 relative）形成正确裁剪链
  position: relative;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
  font-family: ui-monospace, 'Cascadia Code', 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.55;
}

.diff-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: #fef3c7;
  color: #92400e;
  font-size: 12px;
  border-bottom: 1px solid #fde68a;

  &.create {
    background: #ecfdf5;
    color: #065f46;
    border-bottom-color: #a7f3d0;
  }

  &.delete {
    background: #fef2f2;
    color: #991b1b;
    border-bottom-color: #fecaca;
  }
}

.diff-binary,
.diff-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 20px;
  color: var(--el-text-color-secondary);
}

.diff-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 12px;
  background: #f8fafc;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.diff-stats-inline {
  margin-left: auto;
  display: flex;
  gap: 10px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;

  .add {
    color: #16a34a;
    font-weight: 600;
  }

  .del {
    color: #dc2626;
    font-weight: 600;
  }
}

.d2h-mount {
  // 弹窗内 diff 区域恒为白底，防止任何样式缺失时页面内容透出
  background: #fff;
  tab-size: 4;

  // 弹窗标题已展示文件名与变更类型，隐藏 d2h 自带文件头
  :deep(.d2h-file-list-wrapper),
  :deep(.d2h-file-header) {
    display: none;
  }

  :deep(.d2h-file-wrapper) {
    border: none;
    border-radius: 0;
    margin: 0;
  }

  // 行内代码：等宽字号行高与外层一致
  :deep(.d2h-code-line),
  :deep(.d2h-code-side-line) {
    font-size: 12.5px;
    line-height: 1.55;
  }

  // 语法高亮容器适配浅色 diff：vs2015 主题的暗色底与本弹窗冲突，
  // token 颜色（蓝/橙/绿）在白底上仍可读
  :deep(.hljs) {
    background: transparent !important;
    color: #24292e;
  }
}
</style>
