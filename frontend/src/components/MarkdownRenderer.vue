<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import VueMarkdown from 'vue-markdown-render'

const props = defineProps<{
  content: string
}>()

const md = computed(() => props.content || '')
const containerRef = ref<HTMLDivElement>()
let mermaidModule: (typeof import('mermaid'))['default'] | null = null
let hljsModule: (typeof import('highlight.js'))['default'] | null = null
let mermaidInitialized = false
let renderCount = 0
let mermaidTimer: ReturnType<typeof setTimeout> | null = null

async function loadMermaid() {
  if (!mermaidModule) {
    mermaidModule = (await import('mermaid')).default
  }
  return mermaidModule
}

async function loadHljs() {
  if (!hljsModule) {
    hljsModule = (await import('highlight.js')).default
  }
  return hljsModule
}

async function initMermaid(): Promise<void> {
  if (mermaidInitialized) return
  const m = await loadMermaid()
  m.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'strict',
    fontFamily: 'inherit'
  })
  mermaidInitialized = true
}

async function renderMermaidBlocks(): Promise<void> {
  if (!containerRef.value) return
  await initMermaid()
  const m = mermaidModule!
  const placeholders = containerRef.value.querySelectorAll<HTMLPreElement>('.mermaid-block')
  for (const el of placeholders) {
    const code = el.textContent || ''
    const id = `mermaid-${++renderCount}`
    const outer = document.createElement('div')
    outer.className = 'mermaid-container'

    const toolbar = document.createElement('div')
    toolbar.className = 'mermaid-toolbar'
    const toggleBtn = document.createElement('button')
    toggleBtn.className = 'mermaid-toggle-btn active'
    toggleBtn.textContent = '预览'
    const sourceBtn = document.createElement('button')
    sourceBtn.className = 'mermaid-toggle-btn'
    sourceBtn.textContent = '源码'
    toolbar.appendChild(toggleBtn)
    toolbar.appendChild(sourceBtn)

    const previewDiv = document.createElement('div')
    previewDiv.className = 'mermaid-preview'
    const sourceWrapper = document.createElement('div')
    sourceWrapper.className = 'code-block-wrapper'
    sourceWrapper.style.display = 'none'
    const sourceDiv = document.createElement('pre')
    sourceDiv.className = 'mermaid-source'
    sourceDiv.textContent = code.trim()
    sourceWrapper.appendChild(sourceDiv)

    const copyBtn = document.createElement('button')
    copyBtn.className = 'code-copy-btn'
    copyBtn.innerHTML =
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg><span>复制</span>'
    sourceWrapper.appendChild(copyBtn)

    const trimmedCode = code.trim()
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(trimmedCode)
        copyBtn.classList.add('copied')
        const spanEl = copyBtn.querySelector('span')
        if (spanEl) spanEl.textContent = '已复制'
        setTimeout(() => {
          copyBtn.classList.remove('copied')
          if (spanEl) spanEl.textContent = '复制'
        }, 1500)
      } catch {
        // ignore
      }
    })

    const renderContainer = document.createElement('div')
    renderContainer.style.display = 'none'
    document.body.appendChild(renderContainer)

    try {
      const { svg } = await m.render(id, code.trim(), renderContainer)
      previewDiv.innerHTML = svg
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : 'Mermaid 渲染失败'
      previewDiv.innerHTML = `<span class="mermaid-error-label">Mermaid 渲染失败</span><pre class="mermaid-error-msg">${errMsg.replace(/</g, '&lt;')}</pre>`
      previewDiv.className = 'mermaid-preview mermaid-error'
      sourceBtn.click()
    } finally {
      renderContainer.remove()
    }

    toggleBtn.addEventListener('click', () => {
      toggleBtn.classList.add('active')
      sourceBtn.classList.remove('active')
      previewDiv.style.display = ''
      sourceWrapper.style.display = 'none'
    })
    sourceBtn.addEventListener('click', () => {
      sourceBtn.classList.add('active')
      toggleBtn.classList.remove('active')
      sourceWrapper.style.display = ''
      previewDiv.style.display = 'none'
    })

    outer.appendChild(toolbar)
    outer.appendChild(previewDiv)
    outer.appendChild(sourceWrapper)
    el.replaceWith(outer)
  }
}

function attachCodeCopyBtns(): void {
  if (!containerRef.value) return
  const preBlocks = containerRef.value.querySelectorAll<HTMLPreElement>(
    'pre:not(.mermaid-block):not(.mermaid-container)'
  )
  for (const pre of preBlocks) {
    if (pre.querySelector('.code-copy-btn')) continue
    const wrapper = document.createElement('div')
    wrapper.className = 'code-block-wrapper'
    pre.parentNode?.insertBefore(wrapper, pre)
    wrapper.appendChild(pre)

    const copyBtn = document.createElement('button')
    copyBtn.className = 'code-copy-btn'
    copyBtn.innerHTML =
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg><span>复制</span>'
    wrapper.appendChild(copyBtn)

    copyBtn.addEventListener('click', async () => {
      const code = pre.textContent || ''
      try {
        await navigator.clipboard.writeText(code)
        copyBtn.classList.add('copied')
        const spanEl = copyBtn.querySelector('span')
        if (spanEl) spanEl.textContent = '已复制'
        setTimeout(() => {
          copyBtn.classList.remove('copied')
          if (spanEl) spanEl.textContent = '复制'
        }, 1500)
      } catch {
        // ignore
      }
    })
  }
}

async function onMarkdownRendered(immediate = false): Promise<void> {
  if (!containerRef.value) return
  await loadHljs()
  const codeBlocks = containerRef.value.querySelectorAll<HTMLElement>('pre code')
  for (const block of codeBlocks) {
    if (hljsModule && !block.dataset.highlighted) {
      const classes = block.className || ''
      const langMatch = classes.match(/language-(\S+)/)
      if (langMatch && langMatch[1] !== 'mermaid') {
        try {
          hljsModule.highlightElement(block)
        } catch {
          // ignore
        }
      }
    }
    const classes = block.className || ''
    const langMatch = classes.match(/language-(\S+)/)
    if (langMatch && langMatch[1] === 'mermaid') {
      const pre = block.parentElement
      if (pre) {
        pre.className = 'mermaid-block'
        pre.style.display = 'none'
      }
    }
  }
  if (mermaidTimer) clearTimeout(mermaidTimer)
  if (immediate) {
    await nextTick()
    attachCodeCopyBtns()
    renderMermaidBlocks()
  } else {
    await nextTick()
    attachCodeCopyBtns()
    mermaidTimer = setTimeout(() => {
      mermaidTimer = null
      renderMermaidBlocks()
    }, 800)
  }
}

onMounted(async () => {
  await nextTick()
  await nextTick()
  onMarkdownRendered(true)
})

watch(md, () => {
  nextTick(() => {
    onMarkdownRendered(false)
  })
})

onUnmounted(() => {
  if (mermaidTimer) {
    clearTimeout(mermaidTimer)
    mermaidTimer = null
  }
  if (containerRef.value) {
    containerRef.value.querySelectorAll('.mermaid-rendered').forEach(el => {
      el.innerHTML = ''
    })
  }
})
</script>

<template>
  <div ref="containerRef" class="markdown-body">
    <VueMarkdown :source="md" />
  </div>
</template>

<style>
.markdown-body {
  line-height: 1.6;
  word-break: break-word;
}

.markdown-body pre {
  background: #1e1e1e;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0;
}

.markdown-body .code-block-wrapper {
  position: relative;
  margin: 8px 0;
  border-radius: 6px;
  overflow: hidden;
}

.markdown-body .code-block-wrapper pre {
  margin: 0;
  border-radius: 0;
}

.markdown-body .code-copy-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
  opacity: 0;
}

.markdown-body .code-block-wrapper:hover .code-copy-btn {
  opacity: 1;
}

.markdown-body .code-copy-btn:hover {
  color: rgba(255, 255, 255, 0.85);
  background: rgba(255, 255, 255, 0.15);
}

.markdown-body .code-copy-btn.copied {
  color: #22c55e;
  border-color: rgba(34, 197, 94, 0.4);
  background: rgba(34, 197, 94, 0.1);
  opacity: 1;
}

.markdown-body code {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 13px;
}

.markdown-body pre code {
  color: #d4d4d4;
  background: transparent;
  padding: 0;
}

.markdown-body :not(pre) > code {
  background: #f5f5f5;
  padding: 2px 6px;
  border-radius: 4px;
  color: #e83e8c;
}

.markdown-body p {
  margin: 0 0 12px 0;
}

.markdown-body p:last-child {
  margin-bottom: 0;
}

.markdown-body ul,
.markdown-body ol {
  padding-left: 20px;
  margin: 8px 0;
}

.markdown-body li {
  margin: 4px 0;
}

.markdown-body blockquote {
  border-left: 4px solid #ddd;
  padding-left: 12px;
  margin: 8px 0;
  color: #666;
}

.markdown-body table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}

.markdown-body th,
.markdown-body td {
  border: 1px solid #ddd;
  padding: 8px;
  text-align: left;
}

.markdown-body th {
  background: #f5f5f5;
  font-weight: 600;
}

.markdown-body h1,
.markdown-body h2,
.markdown-body h3,
.markdown-body h4,
.markdown-body h5,
.markdown-body h6 {
  margin: 16px 0 8px 0;
  font-weight: 600;
}

.markdown-body h1 {
  font-size: 1.5em;
}

.markdown-body h2 {
  font-size: 1.3em;
}

.markdown-body h3 {
  font-size: 1.1em;
}

.markdown-body a {
  color: #409eff;
  text-decoration: none;
}

.markdown-body a:hover {
  text-decoration: underline;
}

.markdown-body hr {
  border: none;
  border-top: 1px solid #eee;
  margin: 16px 0;
}

.markdown-body img {
  max-width: 100%;
  border-radius: 4px;
}

.markdown-body .mermaid-container {
  margin: 8px 0;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
}

.markdown-body .mermaid-toolbar {
  display: flex;
  gap: 0;
  background: #f1f5f9;
  border-bottom: 1px solid #e2e8f0;
  padding: 0;
}

.markdown-body .mermaid-toggle-btn {
  padding: 4px 14px;
  font-size: 12px;
  font-weight: 500;
  color: #64748b;
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 0.2s;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}

.markdown-body .mermaid-toggle-btn:hover {
  color: #334155;
}

.markdown-body .mermaid-toggle-btn.active {
  color: #334155;
  border-bottom-color: #409eff;
  background: #fff;
}

.markdown-body .mermaid-preview {
  padding: 12px;
  background: #f9fafb;
  overflow-x: auto;
  text-align: center;
}

.markdown-body .mermaid-preview svg {
  max-width: 100%;
  height: auto;
}

.markdown-body .mermaid-source {
  padding: 12px 16px;
  background: #1e1e1e;
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 13px;
  color: #d4d4d4;
  white-space: pre-wrap;
  word-break: break-all;
  overflow-x: auto;
  margin: 0;
  max-height: 400px;
  overflow-y: auto;
  border-radius: 0;
}

.markdown-body .mermaid-error {
  margin: 8px 0;
  padding: 12px;
  background: #f9fafb;
  border-radius: 6px;
  text-align: left;
}

.markdown-body .mermaid-error-label {
  display: inline-block;
  padding: 2px 8px;
  background: #dc2626;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  border-radius: 4px;
  margin-bottom: 8px;
}

.markdown-body .mermaid-error-msg {
  margin: 4px 0;
  padding: 8px;
  background: #fef2f2;
  border-radius: 4px;
  font-size: 12px;
  color: #dc2626;
  white-space: pre-wrap;
  word-break: break-all;
}

.markdown-body .mermaid-error-code {
  margin: 4px 0;
  padding: 8px;
  background: #f5f5f5;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
