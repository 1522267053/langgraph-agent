<script setup lang="ts">
/**
 * 全屏试运行弹窗（独立组件）
 *
 * 职责：
 * - 提供 fullscreen el-dialog，左侧 CodeMirror 编辑器（高度 100%），右侧默认 slot 给试运行 UI
 * - 双向绑定 modelValue（代码字符串）+ visible（弹窗开关）
 * - 单栏 / 双栏模式由 debugPanel slot 是否传值自动决定：
 *   - 未传 slot：纯单编辑器（适用于 LLM prompt 全屏编辑）
 *   - 传 slot：左右分栏（左编辑 + 右 slot 内容）
 *
 * 不变量：modelValue 内部统一以 string 处理（undefined/null 视为空串）。
 */
import { ref, watch } from 'vue'
import { Codemirror } from 'vue-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { basicSetup } from 'codemirror'

const props = withDefaults(
  defineProps<{
    /**
     * 当前代码字符串（双向绑定）。
     * 防御性接受 string | null | undefined。
     */
    modelValue?: string | null
    /** 弹窗显示状态（双向绑定） */
    visible?: boolean
    placeholder?: string
    /** CodeMirror 扩展，默认 Python + oneDark */
    extensions?: unknown[]
    /** 弹窗标题 */
    title?: string
  }>(),
  {
    modelValue: '',
    visible: false,
    placeholder: '',
    extensions: () => [basicSetup, python(), oneDark],
    title: '代码编辑'
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'update:visible', value: boolean): void
  (e: 'blur'): void
}>()

/** 入参规范化 */
const code = (): string => props.modelValue ?? ''

/** 弹窗内编辑器的本地副本，避免与父组件的 modelValue 频繁同步影响 v-model 性能 */
const localCode = ref(code())

/**
 * 弹窗可见性：组件自管状态 + 兼容 v-model:visible 双向绑定。
 *
 * 设计要点：
 * - 内部 ref `internalVisible` 作为可见性的"真相源"（el-dialog 直接监听它）
 * - props.visible 变化（父组件 v-model 绑定）时同步到内部 ref
 * - 内部 ref 变化（命令式 open()/close() 或 el-dialog 关闭按钮）时 emit 通知父组件
 *
 * 为什么不直接用 props.visible 作为 v-model：open() 是命令式调用，需要直接修改可见性，
 * 仅 emit 不修改会让"打开"动作无效（父组件未监听时尤其明显）。
 */
const internalVisible = ref(props.visible)

watch(
  () => props.visible,
  val => {
    internalVisible.value = val
  }
)

/** el-dialog v-model 变化（含关闭按钮）→ 同步内部 + 通知父组件 */
function onVisibleChange(val: boolean) {
  internalVisible.value = val
  emit('update:visible', val)
  if (val) {
    localCode.value = code()
  }
}

function onCodeChange(value: string) {
  localCode.value = value
  emit('update:modelValue', value)
}

function onCodeBlur() {
  emit('blur')
}

/** 命令式打开：直接修改内部状态 + 通知父组件同步（兼容未监听 update:visible 的父组件） */
function open() {
  localCode.value = code()
  internalVisible.value = true
  emit('update:visible', true)
}

function close() {
  internalVisible.value = false
  emit('update:visible', false)
}

defineExpose({ open, close })
</script>

<template>
  <el-dialog
    :model-value="internalVisible"
    :title="title"
    fullscreen
    :destroy-on-close="false"
    :show-close="true"
    @update:model-value="onVisibleChange"
  >
    <!-- 单编辑器模式（slot 为空时） -->
    <div v-if="!$slots.default" class="fullscreen-editor-container">
      <Codemirror
        :model-value="localCode"
        :placeholder="placeholder"
        :extensions="extensions"
        :style="{ height: '100%' }"
        :indent-with-tab="true"
        :tab-size="4"
        @update:model-value="onCodeChange"
        @blur="onCodeBlur"
      />
    </div>

    <!-- 双栏模式（左编辑 + 右 slot） -->
    <div v-else class="fullscreen-split">
      <div class="split-left">
        <Codemirror
          :model-value="localCode"
          :placeholder="placeholder"
          :extensions="extensions"
          :style="{ height: '100%' }"
          :indent-with-tab="true"
          :tab-size="4"
          @update:model-value="onCodeChange"
          @blur="onCodeBlur"
        />
      </div>
      <div class="split-right">
        <slot />
      </div>
    </div>

    <!-- 双栏模式底部说明 -->
    <template v-if="$slots.default" #footer>
      <el-text size="small" type="info">
        左侧编辑代码，右侧填写参数并试运行，结果仅供调试不影响实际流程
      </el-text>
    </template>
  </el-dialog>
</template>

<style scoped>
.fullscreen-editor-container {
  height: calc(100vh - 120px);
}

.fullscreen-editor-container :deep(.cm-editor) {
  height: 100%;
}

.fullscreen-editor-container :deep(.cm-editor .cm-scroller) {
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
  font-size: 14px;
  line-height: 1.6;
}

.fullscreen-editor-container :deep(.cm-editor .cm-content) {
  padding: 12px 0;
}

.fullscreen-editor-container :deep(.cm-editor .cm-line) {
  padding: 0 12px;
}

/* ---- 双栏布局 ---- */
.fullscreen-split {
  display: flex;
  gap: 12px;
  height: calc(100vh - 160px);
}

.split-left {
  flex: 6;
  min-width: 0;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;
}

.split-left :deep(.cm-editor) {
  height: 100%;
}

.split-left :deep(.cm-editor .cm-scroller) {
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
  font-size: 14px;
  line-height: 1.6;
}

.split-left :deep(.cm-editor .cm-content) {
  padding: 12px 0;
}

.split-left :deep(.cm-editor .cm-line) {
  padding: 0 12px;
}

.split-right {
  flex: 4;
  min-width: 320px;
  max-width: 520px;
  overflow-y: auto;
  padding: 4px;
}

/* 窄屏堆叠 */
@media (max-width: 960px) {
  .fullscreen-split {
    flex-direction: column;
    height: auto;
  }
  .split-left {
    height: 50vh;
    flex: none;
  }
  .split-right {
    max-width: none;
    flex: none;
  }
}
</style>
