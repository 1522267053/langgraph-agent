<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import VueMarkdown from 'vue-markdown-render'
import type { KnowledgeReference } from '@/types/knowledge'

interface CitationEntry {
  marker: string
  number: number
  href: string
  reference: KnowledgeReference
}

interface InlineMarkdownState {
  inlineCodeRun: number
  bracketDepth: number
  linkDestinationDepth: number
  angleBracket: boolean
  pendingLinkTarget: boolean
}

const props = withDefaults(
  defineProps<{
    content: string
    /** 是否处于流式输出中：节流渲染 markdown，且跳过 hljs/复制按钮/mermaid 后处理 */
    streaming?: boolean
    citations?: KnowledgeReference[]
  }>(),
  { streaming: false, citations: () => [] }
)

const emit = defineEmits<{
  (e: 'citation-click', reference: KnowledgeReference): void
}>()

/** 组件是否已卸载：异步后处理（hljs/mermaid）在 await 间隙后需重新校验 */
let isUnmounted = false

const citationNamespace =
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`

const citationEntries = computed<CitationEntry[]>(() => {
  const seenMarkers = new Set<string>()
  const entries: CitationEntry[] = []

  props.citations.forEach((reference, index) => {
    const marker = reference.citation_marker.trim()
    if (!marker || seenMarkers.has(marker)) return
    seenMarkers.add(marker)
    entries.push({
      marker,
      number: index + 1,
      href: `citation://${citationNamespace}/${index + 1}`,
      reference
    })
  })

  return entries
})

const citationLinkMap = ref<Map<string, KnowledgeReference>>(new Map())

function isEscaped(text: string, index: number): boolean {
  let slashCount = 0
  for (let i = index - 1; i >= 0 && text[i] === '\\'; i--) slashCount++
  return slashCount % 2 === 1
}

function countBackticks(text: string, index: number): number {
  let end = index
  while (text[end] === '`') end++
  return end - index
}

function stripBlockContainerPrefixes(line: string): string {
  let content = line
  while (true) {
    const stripped = content.replace(/^ {0,3}(?:> ?|[-+*][ \t]|\d+[.)][ \t])/, '')
    if (stripped === content) return content
    content = stripped
  }
}

function replaceMarkersInLine(
  line: string,
  entries: CitationEntry[],
  state: InlineMarkdownState
): string {
  let result = ''
  let index = 0

  while (index < line.length) {
    const char = line[index]

    if (state.inlineCodeRun > 0) {
      if (char === '`' && !isEscaped(line, index)) {
        const runLength = countBackticks(line, index)
        if (state.inlineCodeRun === runLength) state.inlineCodeRun = 0
        result += line.slice(index, index + runLength)
        index += runLength
        continue
      }
      result += char
      index++
      continue
    }

    if (state.angleBracket) {
      result += char
      if (char === '>' && !isEscaped(line, index)) state.angleBracket = false
      index++
      continue
    }

    if (state.linkDestinationDepth > 0) {
      if (!isEscaped(line, index)) {
        if (char === '(') state.linkDestinationDepth++
        if (char === ')') state.linkDestinationDepth--
      }
      result += char
      index++
      continue
    }

    if (char === '`' && !isEscaped(line, index)) {
      const runLength = countBackticks(line, index)
      state.inlineCodeRun = runLength
      result += line.slice(index, index + runLength)
      index += runLength
      continue
    }

    if (state.pendingLinkTarget) {
      if (/\s/.test(char)) {
        result += char
        index++
        continue
      }
      if (char === '(' && !isEscaped(line, index)) {
        state.pendingLinkTarget = false
        state.linkDestinationDepth = 1
        result += char
        index++
        continue
      }
      if (char !== '[') state.pendingLinkTarget = false
    }

    if (state.bracketDepth === 0 && !state.pendingLinkTarget) {
      const entry = entries.find(item => line.startsWith(item.marker, index))
      if (entry && !isEscaped(line, index)) {
        const markerEnd = index + entry.marker.length
        const following = line.slice(markerEnd).match(/^\s*([([])/)?.[1]
        const isExistingLink =
          (index > 0 && line[index - 1] === '!' && !isEscaped(line, index - 1)) ||
          following === '(' ||
          following === '['
        if (!isExistingLink) {
          result += `[[${entry.number}]](${entry.href})`
          index = markerEnd
          continue
        }
      }
    }

    if (!isEscaped(line, index)) {
      if (char === '<' && line.indexOf('>', index + 1) !== -1) {
        state.angleBracket = true
      } else if (char === '[') {
        state.bracketDepth++
      } else if (char === ']' && state.bracketDepth > 0) {
        state.bracketDepth--
        if (state.bracketDepth === 0) state.pendingLinkTarget = true
      }
    }

    result += char
    index++
  }

  return result
}

/** 只替换普通正文中的已知 marker，代码块、行内代码和已有链接保持原样。 */
function replaceCitationMarkers(source: string, entries: CitationEntry[]): string {
  if (!source || entries.length === 0) return source

  const sortedEntries = [...entries].sort((a, b) => b.marker.length - a.marker.length)
  const parts = source.split(/(\r\n|\r|\n)/)
  const inlineState: InlineMarkdownState = {
    inlineCodeRun: 0,
    bracketDepth: 0,
    linkDestinationDepth: 0,
    angleBracket: false,
    pendingLinkTarget: false
  }
  let fenceChar = ''
  let fenceLength = 0

  return parts
    .map(part => {
      if (/^(?:\r\n|\r|\n)$/.test(part)) return part

      if (part === '') {
        inlineState.inlineCodeRun = 0
        inlineState.bracketDepth = 0
        inlineState.linkDestinationDepth = 0
        inlineState.angleBracket = false
        inlineState.pendingLinkTarget = false
        return part
      }

      const blockContent = stripBlockContainerPrefixes(part)

      if (inlineState.inlineCodeRun === 0 && /^ {0,3}\[[^\]]+\]:/.test(blockContent)) {
        return part
      }

      if (fenceChar) {
        const closingFence = blockContent.match(/^ {0,3}(`{3,}|~{3,})\s*$/)
        const closingRun = closingFence?.[1]
        if (closingRun && closingRun.charAt(0) === fenceChar && closingRun.length >= fenceLength) {
          fenceChar = ''
          fenceLength = 0
        }
        return part
      }

      if (inlineState.inlineCodeRun === 0) {
        const openingFence = blockContent.match(/^ {0,3}(`{3,}|~{3,})/)
        const openingRun = openingFence?.[1]
        if (openingRun) {
          fenceChar = openingRun.charAt(0)
          fenceLength = openingRun.length
          return part
        }
        if (/^(?: {4}|\t)/.test(blockContent)) return part
      }

      return replaceMarkersInLine(part, sortedEntries, inlineState)
    })
    .join('')
}

const md = computed(() => props.content || '')

function currentRenderedSource(): string {
  const entries = citationEntries.value
  citationLinkMap.value = new Map(entries.map(entry => [entry.href, entry.reference]))
  return replaceCitationMarkers(md.value, entries)
}

/** 实际传给 VueMarkdown 的源文本：流式期间按 STREAM_RENDER_INTERVAL 节流更新 */
const renderedSource = ref(currentRenderedSource())
const containerRef = ref<HTMLDivElement>()
let mermaidModule: (typeof import('mermaid'))['default'] | null = null
let hljsModule: (typeof import('highlight.js'))['default'] | null = null
let mermaidInitialized = false
let renderCount = 0
let mermaidTimer: ReturnType<typeof setTimeout> | null = null

/** 流式期间 markdown 重渲染的最小间隔（ms），将 O(n²) 全量重渲频率从 token 级降到 ~7次/秒 */
const STREAM_RENDER_INTERVAL = 150
let streamRenderTimer: ReturnType<typeof setTimeout> | null = null
let lastStreamRenderAt = 0
let hasPendingStreamRender = false

function decorateCitationLinks(): void {
  if (!containerRef.value) return
  const links = containerRef.value.querySelectorAll<HTMLAnchorElement>('a')
  for (const link of links) {
    const href = link.getAttribute('href') || ''
    if (!href.toLowerCase().startsWith('citation:')) continue
    const reference = citationLinkMap.value.get(href)
    link.classList.toggle('knowledge-citation-link', Boolean(reference))
    link.classList.toggle('invalid-citation-link', !reference)
    if (reference) {
      link.title = '查看引用来源'
      link.setAttribute('aria-label', `${link.textContent || '引用'}，查看引用来源`)
      link.removeAttribute('aria-disabled')
    } else {
      link.removeAttribute('href')
      link.removeAttribute('title')
      link.removeAttribute('aria-label')
      link.setAttribute('aria-disabled', 'true')
    }
  }
}

function queueCitationLinkDecoration(): void {
  nextTick(decorateCitationLinks)
}

function handleMarkdownClick(event: MouseEvent): void {
  if (!(event.target instanceof Element)) return
  const link = event.target.closest<HTMLAnchorElement>('a')
  if (!link || !containerRef.value?.contains(link)) return
  const href = link.getAttribute('href') || ''
  if (!href.toLowerCase().startsWith('citation:')) return

  // 所有模型生成的 citation:// 链接都先阻止，仅放行本组件由 metadata 生成的链接。
  event.preventDefault()
  const reference = citationLinkMap.value.get(href)
  if (reference) emit('citation-click', reference)
}

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
  if (!containerRef.value || isUnmounted) return
  await initMermaid()
  // initMermaid 动态导入期间组件可能已卸载
  if (!containerRef.value || isUnmounted) return
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
  if (!containerRef.value || isUnmounted) return
  await loadHljs()
  // loadHljs 动态导入期间组件可能已卸载
  if (!containerRef.value || isUnmounted) return
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
  decorateCitationLinks()
  onMarkdownRendered(true)
})

/** 立即应用当前源文本并执行完整后处理（hljs/复制按钮/mermaid） */
function applyRenderNow(): void {
  renderedSource.value = currentRenderedSource()
  nextTick(() => {
    onMarkdownRendered(false)
  })
}

/** 流式期间节流应用源文本，跳过所有后处理 */
function scheduleStreamRender(): void {
  hasPendingStreamRender = true
  if (streamRenderTimer) return
  const elapsed = Date.now() - lastStreamRenderAt
  const wait = Math.max(0, STREAM_RENDER_INTERVAL - elapsed)
  streamRenderTimer = setTimeout(() => {
    streamRenderTimer = null
    if (!hasPendingStreamRender) return
    hasPendingStreamRender = false
    lastStreamRenderAt = Date.now()
    renderedSource.value = currentRenderedSource()
  }, wait)
}

/** 流式结束：取消节流定时器，立即应用最终内容 + 完整后处理 */
function finishStreamRender(): void {
  if (streamRenderTimer) {
    clearTimeout(streamRenderTimer)
    streamRenderTimer = null
  }
  hasPendingStreamRender = false
  applyRenderNow()
}

watch(md, () => {
  if (props.streaming) {
    scheduleStreamRender()
  } else {
    applyRenderNow()
  }
})

watch(citationEntries, () => {
  if (props.streaming) {
    scheduleStreamRender()
  } else {
    applyRenderNow()
  }
})

watch(renderedSource, queueCitationLinkDecoration, { flush: 'post' })
watch(citationLinkMap, queueCitationLinkDecoration)

watch(
  () => props.streaming,
  streaming => {
    if (!streaming) {
      finishStreamRender()
    }
  }
)

onUnmounted(() => {
  isUnmounted = true
  if (mermaidTimer) {
    clearTimeout(mermaidTimer)
    mermaidTimer = null
  }
  if (streamRenderTimer) {
    clearTimeout(streamRenderTimer)
    streamRenderTimer = null
  }
  if (containerRef.value) {
    containerRef.value.querySelectorAll('.mermaid-rendered').forEach(el => {
      el.innerHTML = ''
    })
  }
})
</script>

<template>
  <div ref="containerRef" class="markdown-body" @click="handleMarkdownClick">
    <VueMarkdown :source="renderedSource" />
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

.markdown-body a.knowledge-citation-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 20px;
  margin: 0 2px;
  padding: 0 5px;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  background: #eff6ff;
  color: #2563eb;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  vertical-align: 1px;
  cursor: pointer;
  transition: all 0.2s;
}

.markdown-body a.knowledge-citation-link:hover {
  border-color: #60a5fa;
  background: #dbeafe;
  color: #1d4ed8;
  text-decoration: none;
}

.markdown-body a.invalid-citation-link {
  color: inherit;
  text-decoration: none;
  cursor: text;
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
