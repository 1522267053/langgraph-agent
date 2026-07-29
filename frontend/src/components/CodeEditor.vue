<script setup lang="ts">
import { ref, shallowRef } from 'vue'
import { Codemirror } from 'vue-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { basicSetup } from 'codemirror'
import { FullScreen } from '@element-plus/icons-vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    placeholder?: string
  }>(),
  {
    placeholder: ''
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'blur'): void
}>()

const fullscreenVisible = ref(false)
const fullscreenCode = ref(props.modelValue)

const extensions = [basicSetup, python(), oneDark]

const view = shallowRef()

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
  fullscreenCode.value = props.modelValue
  fullscreenVisible.value = true
}

function onFullscreenChange(value: string) {
  fullscreenCode.value = value
  emit('update:modelValue', value)
}

function onFullscreenBlur() {
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
      :model-value="modelValue"
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

  <el-dialog
    v-model="fullscreenVisible"
    title="Python 代码编辑"
    fullscreen
    :destroy-on-close="false"
    :show-close="true"
  >
    <div class="fullscreen-editor-container">
      <Codemirror
        :model-value="fullscreenCode"
        :placeholder="placeholder"
        :extensions="extensions"
        :style="{ height: '100%' }"
        :indent-with-tab="true"
        :tab-size="4"
        @update:model-value="onFullscreenChange"
        @blur="onFullscreenBlur"
      />
    </div>
  </el-dialog>
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
</style>