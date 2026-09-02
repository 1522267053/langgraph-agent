<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowUp, RefreshRight, Search, Folder } from '@element-plus/icons-vue'
import { fsApi, type DirectoryEntry } from '@/api/filesystem'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    /** 打开时定位到的目录（空则从盘符列表开始） */
    initialPath?: string
    /** 对话框标题 */
    title?: string
  }>(),
  {
    initialPath: '',
    title: '选择项目工作路径'
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  /** path 为空串表示清除选择 */
  (e: 'confirm', path: string): void
}>()

const visible = ref(false)
const loading = ref(false)
/** 当前目录（null 表示盘符列表视图） */
const currentPath = ref<string | null>(null)
const parentPath = ref<string | null>(null)
const directories = ref<DirectoryEntry[]>([])
/** 路径输入框（支持直接粘贴路径回车跳转） */
const pathInput = ref('')
/** 目录名过滤（前端过滤，对标 opencode DialogSelect） */
const filterText = ref('')

const isDriveView = computed(() => currentPath.value === null)
const filteredDirectories = computed(() => {
  const keyword = filterText.value.trim().toLowerCase()
  if (!keyword) return directories.value
  return directories.value.filter(d => d.name.toLowerCase().includes(keyword))
})

watch(
  () => props.modelValue,
  val => {
    visible.value = val
    if (val) {
      filterText.value = ''
      browse(props.initialPath)
    }
  }
)

watch(visible, val => {
  emit('update:modelValue', val)
})

async function browse(path: string): Promise<void> {
  loading.value = true
  try {
    const res = await fsApi.listDirectories(path || undefined)
    const data = res.data.data
    currentPath.value = data?.path ?? null
    parentPath.value = data?.parent ?? null
    directories.value = data?.directories || []
    pathInput.value = data?.path || ''
  } catch {
    // 错误提示由 axios 拦截器统一处理
  } finally {
    loading.value = false
  }
}

function enter(entry: DirectoryEntry): void {
  filterText.value = ''
  browse(entry.path)
}

function goUp(): void {
  if (!parentPath.value) return
  filterText.value = ''
  browse(parentPath.value)
}

function refresh(): void {
  browse(currentPath.value || '')
}

function navigateByInput(): void {
  const target = pathInput.value.trim()
  if (!target) {
    browse('')
    return
  }
  filterText.value = ''
  browse(target)
}

function handleConfirm(): void {
  if (isDriveView.value) {
    ElMessage.warning({ message: '请先进入具体目录', duration: 5000 })
    return
  }
  emit('confirm', currentPath.value || '')
  visible.value = false
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="title"
    width="620px"
    :close-on-click-modal="false"
    destroy-on-close
    @mousedown.stop
    @click.stop
  >
    <div class="dir-picker-toolbar">
      <el-tooltip content="上级目录" placement="top">
        <el-button :icon="ArrowUp" size="small" :disabled="!parentPath" @click="goUp" />
      </el-tooltip>
      <el-tooltip content="刷新" placement="top">
        <el-button :icon="RefreshRight" size="small" :disabled="isDriveView" @click="refresh" />
      </el-tooltip>
      <el-input
        v-model="pathInput"
        placeholder="输入或粘贴路径后回车跳转"
        size="small"
        clearable
        style="flex: 1"
        @keyup.enter="navigateByInput"
        @clear="navigateByInput"
      />
    </div>

    <div class="dir-picker-filter">
      <el-input
        v-model="filterText"
        placeholder="过滤当前目录"
        size="small"
        clearable
        :prefix-icon="Search"
      />
    </div>

    <div v-loading="loading" class="dir-picker-list">
      <template v-if="isDriveView">
        <div class="dir-picker-hint">选择磁盘</div>
        <div
          v-for="drive in filteredDirectories"
          :key="drive.path"
          class="dir-picker-item"
          @click="enter(drive)"
        >
          <el-icon :size="16" class="dir-picker-icon"><Folder /></el-icon>
          <span class="dir-picker-name">{{ drive.name }}</span>
        </div>
      </template>
      <template v-else>
        <div
          v-for="dir in filteredDirectories"
          :key="dir.path"
          class="dir-picker-item"
          @click="enter(dir)"
        >
          <el-icon :size="16" class="dir-picker-icon"><Folder /></el-icon>
          <span class="dir-picker-name" :title="dir.path">{{ dir.name }}</span>
        </div>
        <div v-if="filteredDirectories.length === 0" class="dir-picker-empty">
          <el-text type="info">{{ filterText ? '无匹配目录' : '空目录' }}</el-text>
        </div>
      </template>
    </div>

    <div class="dir-picker-footer">
      <span class="dir-picker-current" :title="currentPath || ''">
        当前：{{ currentPath || '未进入目录' }}
      </span>
      <div class="dir-picker-actions">
        <el-button size="small" @click="emit('confirm', ''); visible = false">清除选择</el-button>
        <el-button size="small" type="primary" @click="handleConfirm">确认选择</el-button>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.dir-picker-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.dir-picker-filter {
  margin-bottom: 10px;
}

.dir-picker-list {
  min-height: 240px;
  max-height: 380px;
  overflow-y: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.dir-picker-hint {
  padding: 8px 12px;
  color: #94a3b8;
  font-size: 12px;
}

.dir-picker-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f1f5f9;
  transition: background 0.15s;
}

.dir-picker-item:last-child {
  border-bottom: none;
}

.dir-picker-item:hover {
  background: #f8fafc;
}

.dir-picker-icon {
  color: #f59e0b;
  flex-shrink: 0;
}

.dir-picker-name {
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dir-picker-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
}

.dir-picker-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}

.dir-picker-current {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: #64748b;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dir-picker-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>
