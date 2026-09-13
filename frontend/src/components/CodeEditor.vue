<script setup lang="ts">
/**
 * 通用代码编辑器（基于 CodeMirror）
 *
 * 极简组件：仅负责内联编辑器 + 触发全屏试运行弹窗。
 * 全屏弹窗的具体实现（布局、双栏、响应式）由 DebugRunDialog 承担。
 *
 * 防御性：modelValue 接受 string | null | undefined，内部统一以空串兜底。
 */
import { ref, shallowRef } from 'vue'
import { Codemirror } from 'vue-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { basicSetup } from 'codemirror'
import { FullScreen } from '@element-plus/icons-vue'
import DebugRunDialog from './DebugRunDialog.vue'

const props = withDefaults(
  defineProps<{
    /**
     * 当前代码字符串。
     * 防御性接受 string | null | undefined：v-model 绑定的字段可能因后端缺字段/历史数据
     * 而为 undefined，默认值空串兜底（不变量 = 输入始终是 string）。
     */
    modelValue?: string | null
    placeholder?: string
    /**
     * 是否启用调试模式。
     * true 时全屏弹窗变为左右分栏布局（左侧编辑器 + 右侧 slot="debug" 注入的试运行 UI）；
     * 默认 false（保持向后兼容，LLM 节点 prompt 等纯编辑场景不受影响）。
     */
    debugSlot?: boolean
  }>(),
  {
    modelValue: '',
    placeholder: '',
    debugSlot: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'blur'): void
}>()

/** 入参规范化：undefined / null 视为空串，保证内部不变量 */
const code = (): string => props.modelValue ?? ''

const extensions = [basicSetup, python(), oneDark]

const view = shallowRef()

/** 全屏弹窗 ref —— 默认不存在；debugSlot=true 时由 DebugRunDialog 渲染 */
const dialogRef = ref<InstanceType<typeof DebugRunDialog> | null>(null)

function onReady(payload: { view: unknown }) {
  view.value = payload.view
}

function onCodeChange(value: string) {
  emit('update:modelValue', value)
}

function onCodeBlur() {
  emit('blur')
}

function openFullscreen() {
  dialogRef.value?.open()
}

/** 弹窗内双向同步 */
function onDialogModelValueUpdate(value: string) {
  emit('update:modelValue', value)
}

function onDialogBlur() {
  emit('blur')
}
</script>

<template>
  <div class="code-editor-wrapper">
    <div class="code-editor-header">
      <span class="code-editor-lang">Python</span>
      <el-button size="small" link @click="openFullscreen">
        <el-icon><FullScreen /></el-icon>
      </el-button>
    </div>
    <Codemirror
      :model-value="code()"
      :placeholder="placeholder"
      :extensions="extensions"
      :style="{ height: '200px' }"
      :indent-with-tab="true"
      :tab-size="4"
      @ready="onReady"
      @update:model-value="onCodeChange"
      @blur="onCodeBlur"
    />
  </div>

  <DebugRunDialog
    ref="dialogRef"
    :model-value="modelValue"
    :title="debugSlot ? '代码编辑 + 试运行' : 'Python 代码编辑'"
    :placeholder="placeholder"
    :extensions="extensions"
    @update:model-value="onDialogModelValueUpdate"
    @blur="onDialogBlur"
  >
    <template v-if="debugSlot" #default>
      <slot name="debug" />
    </template>
  </DebugRunDialog>
</template>

<style scoped>
.code-editor-wrapper {
  width: 100%;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;
}

.code-editor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 2px 8px;
  background: #282c34;
  border-bottom: 1px solid #3e4451;
}

.code-editor-lang {
  font-size: 12px;
  color: #abb2bf;
}

.code-editor-header .el-button {
  color: #abb2bf;
}

.code-editor-header .el-button:hover {
  color: #fff;
}

.code-editor-wrapper :deep(.cm-editor) {
  height: 200px;
}

.code-editor-wrapper :deep(.cm-editor .cm-scroller) {
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
  font-size: 13px;
  line-height: 1.6;
}

.code-editor-wrapper :deep(.cm-editor .cm-content) {
  padding: 8px 0;
}

.code-editor-wrapper :deep(.cm-editor .cm-line) {
  padding: 0 8px;
}
</style>
