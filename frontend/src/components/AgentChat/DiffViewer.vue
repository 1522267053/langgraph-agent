<template>
  <!--
    自研轻量 diff 渲染器（LCS line-level）
    入参：backup（旧文本）+ current（新文本）+ is_binary + backup_missing + change_type
    不引入 monaco / diff 库；超大文件（>5000 行）走窗口折叠
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

    <div v-if="lines.length === 0" class="diff-empty">
      <el-empty description="文件内容为空" :image-size="60" />
    </div>
    <table v-else class="diff-table">
      <colgroup>
        <col class="gutter-old" />
        <col class="gutter-new" />
        <col class="content" />
      </colgroup>
      <tbody>
        <tr v-for="row in lines" :key="row.key" :class="rowClass(row.type)">
          <td class="gutter gutter-old">{{ row.oldLine || '' }}</td>
          <td class="gutter gutter-new">{{ row.newLine || '' }}</td>
          <td class="content">
            <span class="prefix">{{ rowPrefix(row.type) }}</span>
            <span class="text">{{ row.text }}</span>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="stats" class="diff-stats">
      <span class="add">+{{ stats.added }}</span>
      <span class="del">-{{ stats.removed }}</span>
      <span class="total">{{ stats.total }} 行</span>
      <span v-if="truncated" class="truncated">（已折叠超大文件）</span>
    </div>
  </div>
  <div v-else class="diff-binary">
    <el-icon><Document /></el-icon>
    <span>二进制文件，无法显示 diff</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Warning, Plus, Delete, Document } from '@element-plus/icons-vue'

interface Props {
  backupContent: string
  currentContent: string
  isBinary: boolean
  backupMissing: boolean
  changeType: 'create' | 'modify' | 'delete' | string
  /** 单文件最大参与 diff 的行数（超过则只展示前 N 行 + 提示） */
  maxLines?: number
}

const props = withDefaults(defineProps<Props>(), {
  maxLines: 5000
})

type RowType = 'context' | 'add' | 'del' | 'meta'
interface Row {
  key: string
  oldLine: number | null
  newLine: number | null
  text: string
  type: RowType
}

// ===== 行级 LCS diff =====

/** 按行切分（保留空行） */
function splitLines(s: string): string[] {
  if (s === '') return []
  return s.split(/\r?\n/)
}

/**
 * 计算 LCS 长度表（O(m*n) 空间，省略后端 diff 库依赖）
 * 单文件最大 5000 行 → 25M 单元格（≤200MB），正常工程文件远低于此
 */
function lcsTable(a: string[], b: string[]): number[][] {
  const m = a.length
  const n = b.length
  // 单维滚动数组：内存 O(min(m,n))
  const prev = new Array<number>(n + 1).fill(0)
  const cur = new Array<number>(n + 1).fill(0)
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      cur[j] = a[i - 1] === b[j - 1]
        ? (prev[j - 1] || 0) + 1
        : Math.max(prev[j] || 0, cur[j - 1] || 0)
    }
    for (let j = 0; j <= n; j++) {
      prev[j] = cur[j]
      cur[j] = 0
    }
  }
  return [prev]
}

/**
 * 反向回溯生成行级 diff
 */
function buildDiff(a: string[], b: string[]): Row[] {
  const out: Row[] = []
  // 极端大文件：直接做对比不上 LCS，避免卡死
  if (a.length > props.maxLines || b.length > props.maxLines) {
    const truncated = [
      ...a.map((t, i) => ({ type: 'del' as RowType, oldLine: i + 1, newLine: null, text: t })),
      ...b.map((t, i) => ({ type: 'add' as RowType, oldLine: null, newLine: i + 1, text: t }))
    ]
    return truncated.slice(0, props.maxLines).map((r, i) => ({ ...r, key: `tr-${i}` }))
  }
  const table = lcsTable(a, b)
  let i = a.length
  let j = b.length
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      out.push({
        key: `c-${i}-${j}`,
        oldLine: i,
        newLine: j,
        text: a[i - 1],
        type: 'context'
      })
      i--
      j--
    } else if ((table[0][j - 1] || 0) >= (table[0][j] || 0)) {
      out.push({
        key: `d-${i}-${j}`,
        oldLine: i,
        newLine: null,
        text: a[i - 1],
        type: 'del'
      })
      i--
    } else {
      out.push({
        key: `a-${i}-${j}`,
        oldLine: null,
        newLine: j,
        text: b[j - 1],
        type: 'add'
      })
      j--
    }
  }
  while (i > 0) {
    out.push({
      key: `dt-${i}`,
      oldLine: i,
      newLine: null,
      text: a[i - 1],
      type: 'del'
    })
    i--
  }
  while (j > 0) {
    out.push({
      key: `at-${j}`,
      oldLine: null,
      newLine: j,
      text: b[j - 1],
      type: 'add'
    })
    j--
  }
  return out.reverse()
}

const lines = computed<Row[]>(() => {
  if (props.isBinary) return []
  // 文件已被删除 → 无 current；create → 无 backup；modify 双向都有
  const oldLines = props.changeType === 'create' ? [] : splitLines(props.backupContent)
  const newLines = props.changeType === 'delete' ? [] : splitLines(props.currentContent)
  return buildDiff(oldLines, newLines)
})

const stats = computed(() => {
  if (props.isBinary) return null
  let added = 0
  let removed = 0
  for (const row of lines.value) {
    if (row.type === 'add') added++
    else if (row.type === 'del') removed++
  }
  return {
    added,
    removed,
    total: lines.value.length
  }
})

const truncated = computed(() => {
  if (props.isBinary) return false
  return (
    splitLines(props.backupContent).length > props.maxLines ||
    splitLines(props.currentContent).length > props.maxLines
  )
})

function rowClass(type: RowType): string {
  if (type === 'add') return 'row-add'
  if (type === 'del') return 'row-del'
  return 'row-context'
}

function rowPrefix(type: RowType): string {
  if (type === 'add') return '+'
  if (type === 'del') return '-'
  return ' '
}
</script>

<style scoped lang="scss">
.diff-viewer {
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

.diff-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;

  .gutter {
    width: 44px;
    padding: 0 6px;
    color: var(--el-text-color-placeholder);
    text-align: right;
    user-select: none;
    background: #f8fafc;
    border-right: 1px solid var(--el-border-color-lighter);
    font-size: 11px;
    vertical-align: top;
  }

  .content {
    padding: 0 8px;
    white-space: pre-wrap;
    word-break: break-all;
    vertical-align: top;

    .prefix {
      display: inline-block;
      width: 14px;
      color: var(--el-text-color-placeholder);
      user-select: none;
    }

    .text {
      white-space: pre-wrap;
    }
  }

  tr.row-add {
    background: #dcfce7;

    .prefix {
      color: #16a34a;
      font-weight: 600;
    }
  }

  tr.row-del {
    background: #fee2e2;

    .prefix {
      color: #dc2626;
      font-weight: 600;
    }
  }

  tr.row-context {
    background: #fff;
  }
}

.diff-stats {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 6px 12px;
  background: #f8fafc;
  border-top: 1px solid var(--el-border-color-lighter);
  font-size: 12px;
  color: var(--el-text-color-regular);

  .add {
    color: #16a34a;
    font-weight: 600;
  }

  .del {
    color: #dc2626;
    font-weight: 600;
  }

  .total {
    color: var(--el-text-color-secondary);
  }

  .truncated {
    color: #d97706;
  }
}
</style>