<script lang="ts">
/**
 * 模块级状态：跨所有组件实例共享。
 * renderCount 必须全局唯一：虚拟滚动下多个 MarkdownRenderer 实例同时挂载，
 * 各自实例级计数会生成重复的 mermaid-{n} SVG id；mermaid 11 的 SVG 内部
 * url(#id_...) 引用按 DOM 全局解析，重复 id 会让先渲染的图引用被后渲染的
 * 劫持（defs 断裂），表现为图表整体空白且相互传染（回滚重挂载后互翻空白）。
 */
let renderCount = 0
export default {}
</script>

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import VueMarkdown from 'vue-markdown-render'
import katex from 'katex'
import texmath from 'markdown-it-texmath'
import 'katex/dist/katex.min.css'
import type { KnowledgeReference } from '@/types/knowledge'
import { STREAM_RENDER_INTERVAL, MERMAID_RENDER_DEBOUNCE } from '@/constants/timing'

/**
 * KaTeX 数学公式渲染：支持 $...$ 行内公式和 $$...$$ 块级公式
 * throwOnError=false 时解析失败的公式以红色原文显示而非抛错（流式期间常见）
 * strict='ignore' 关闭非 ASCII 字符（如中文）进入数学模式时的控制台警告
 */
const texmathPlugin = (md: unknown) =>
  texmath(md, {
    engine: katex,
    delimiters: 'dollars',
    katexOptions: { throwOnError: false, strict: 'ignore' }
  })
const mdPlugins = [texmathPlugin]

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
let mermaidTimer: ReturnType<typeof setTimeout> | null = null

let streamRenderTimer: ReturnType<typeof setTimeout> | null = null
let lastStreamRenderAt = 0
let hasPendingStreamRender = false

/* ---------- Mermaid 全屏预览（左键拖拽平移 / 滚轮与按钮缩放） ---------- */

const FULLSCREEN_MIN_SCALE = 0.05
const FULLSCREEN_MAX_SCALE = 8

/** 全屏背景色预设（transparent 显示下层深色遮罩，形成透明效果） */
const FULLSCREEN_BG_PRESETS = ['#ffffff', '#f1f5f9', '#1e293b', 'transparent']
const FULLSCREEN_BG_STORAGE_KEY = 'mermaid-fullscreen-bg'

function loadFullscreenBg(): string {
  try {
    const saved = localStorage.getItem(FULLSCREEN_BG_STORAGE_KEY)
    if (saved && FULLSCREEN_BG_PRESETS.includes(saved)) return saved
  } catch {
    // localStorage 不可用时回退默认值
  }
  return '#ffffff'
}

const fullscreenVisible = ref(false)
const fullscreenBg = ref(loadFullscreenBg())
const fullscreenSvg = ref('')
const fullscreenSize = ref({ width: 0, height: 0 })
const fullscreenScale = ref(1)
const fullscreenTranslateX = ref(0)
const fullscreenTranslateY = ref(0)
const fullscreenViewportRef = ref<HTMLDivElement>()
const fullscreenPanning = ref(false)
const fullscreenScalePercent = computed(() => `${Math.round(fullscreenScale.value * 100)}%`)

// 平移起点快照（非响应式，避免拖拽过程中多余渲染依赖）
let panStartX = 0
let panStartY = 0
let panBaseX = 0
let panBaseY = 0

/** 从 SVG 字符串解析原始尺寸：优先 viewBox，其次 width/height 属性 */
function parseSvgNaturalSize(svg: string): { width: number; height: number } {
  const viewBox = svg.match(/viewBox\s*=\s*"[^"]*?\s([\d.]+)\s([\d.]+)"/i)
  if (viewBox) {
    const width = parseFloat(viewBox[1])
    const height = parseFloat(viewBox[2])
    if (width > 0 && height > 0) return { width, height }
  }
  const width = parseFloat(svg.match(/\swidth\s*=\s*"([\d.]+)/i)?.[1] || '0')
  const height = parseFloat(svg.match(/\sheight\s*=\s*"([\d.]+)/i)?.[1] || '0')
  if (width > 0 && height > 0) return { width, height }
  return { width: 800, height: 600 }
}

function clampFullscreenScale(scale: number): number {
  return Math.min(FULLSCREEN_MAX_SCALE, Math.max(FULLSCREEN_MIN_SCALE, scale))
}

/** 重置为适应窗口并居中 */
function fitMermaidToWindow(): void {
  const viewport = fullscreenViewportRef.value
  if (!viewport) return
  const { width, height } = fullscreenSize.value
  if (width <= 0 || height <= 0) return
  const padding = 48
  fullscreenScale.value = clampFullscreenScale(
    Math.min(
      (viewport.clientWidth - padding * 2) / width,
      (viewport.clientHeight - padding * 2) / height
    )
  )
  fullscreenTranslateX.value = (viewport.clientWidth - width * fullscreenScale.value) / 2
  fullscreenTranslateY.value = (viewport.clientHeight - height * fullscreenScale.value) / 2
}

/** 以视口局部坐标为中心缩放，保证光标/中心所指内容位置不变 */
function zoomMermaidAt(localX: number, localY: number, factor: number): void {
  const scale = clampFullscreenScale(fullscreenScale.value * factor)
  const ratio = scale / fullscreenScale.value
  fullscreenTranslateX.value = localX - (localX - fullscreenTranslateX.value) * ratio
  fullscreenTranslateY.value = localY - (localY - fullscreenTranslateY.value) * ratio
  fullscreenScale.value = scale
}

function handleMermaidWheel(event: WheelEvent): void {
  const viewport = fullscreenViewportRef.value
  if (!viewport) return
  const rect = viewport.getBoundingClientRect()
  const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1
  zoomMermaidAt(event.clientX - rect.left, event.clientY - rect.top, factor)
}

/** 按钮缩放：以视口中心为缩放中心 */
function zoomMermaidCentered(factor: number): void {
  const viewport = fullscreenViewportRef.value
  if (!viewport) return
  zoomMermaidAt(viewport.clientWidth / 2, viewport.clientHeight / 2, factor)
}

function handlePanStart(event: MouseEvent): void {
  if (event.button !== 0) return
  event.preventDefault()
  fullscreenPanning.value = true
  panStartX = event.clientX
  panStartY = event.clientY
  panBaseX = fullscreenTranslateX.value
  panBaseY = fullscreenTranslateY.value
  window.addEventListener('mousemove', handlePanMove)
  window.addEventListener('mouseup', handlePanEnd)
}

function handlePanMove(event: MouseEvent): void {
  fullscreenTranslateX.value = panBaseX + (event.clientX - panStartX)
  fullscreenTranslateY.value = panBaseY + (event.clientY - panStartY)
}

function handlePanEnd(): void {
  fullscreenPanning.value = false
  window.removeEventListener('mousemove', handlePanMove)
  window.removeEventListener('mouseup', handlePanEnd)
}

/* ---------- 触摸手势：单指拖动平移 ---------- */

const isCoarsePointer = ref(false)
const fullscreenHintText = computed(() =>
  isCoarsePointer.value ? '单指拖动移动 · 点击 × 关闭' : '左键拖拽移动 · 滚轮缩放 · Esc 关闭'
)

// 触摸手势快照（非响应式）
let touchPanStartX = 0
let touchPanStartY = 0
let touchPanBaseX = 0
let touchPanBaseY = 0
let touchPanning = false

function handleTouchStart(event: TouchEvent): void {
  if (event.touches.length !== 1) return
  // 单指：平移
  const touch = event.touches[0]
  touchPanning = true
  touchPanStartX = touch.clientX
  touchPanStartY = touch.clientY
  touchPanBaseX = fullscreenTranslateX.value
  touchPanBaseY = fullscreenTranslateY.value
}

function handleTouchMove(event: TouchEvent): void {
  if (!touchPanning || event.touches.length !== 1) return
  // 阻止浏览器默认的页面滚动/缩放
  event.preventDefault()
  const touch = event.touches[0]
  fullscreenTranslateX.value = touchPanBaseX + (touch.clientX - touchPanStartX)
  fullscreenTranslateY.value = touchPanBaseY + (touch.clientY - touchPanStartY)
}

function handleTouchEnd(event: TouchEvent): void {
  if (event.touches.length === 0) {
    touchPanning = false
  }
}

function openMermaidFullscreen(svg: string): void {
  fullscreenSvg.value = svg
  fullscreenSize.value = parseSvgNaturalSize(svg)
  // 手机/平板切换触摸提示文案
  isCoarsePointer.value = window.matchMedia?.('(pointer: coarse)').matches ?? false
  fullscreenVisible.value = true
  nextTick(fitMermaidToWindow)
}

function closeMermaidFullscreen(): void {
  fullscreenVisible.value = false
  fullscreenSvg.value = ''
}

function setFullscreenBg(color: string): void {
  fullscreenBg.value = color
  try {
    localStorage.setItem(FULLSCREEN_BG_STORAGE_KEY, color)
  } catch {
    // ignore
  }
}

function handleFullscreenKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeMermaidFullscreen()
}

watch(fullscreenVisible, visible => {
  if (visible) {
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', handleFullscreenKeydown)
    window.addEventListener('resize', fitMermaidToWindow)
  } else {
    document.body.style.overflow = ''
    window.removeEventListener('keydown', handleFullscreenKeydown)
    window.removeEventListener('resize', fitMermaidToWindow)
    handlePanEnd()
  }
})

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

/** Mermaid flowchart 节点标签里 @、&、| 等字符需要用双引号包住才能解析；这里对未加引号且命中危险字符的单层 [...] / (...) / {...} 标签自动加引号。
 *  标签内若已含 <br/> 等 inline HTML 也允许加引号（Mermaid 文档明确引号内仍支持 inline HTML）；
 *  标签文本排除形如 ([..]) / {(..)} 等嵌套结构字符，避免误吃 ((..)) 圆形节点与边的 (>..) 形态。 */
function quoteMermaidLabels(src: string): string {
  const wrapIfNeeded = (id: string, label: string, open: string, close: string): string => {
    if (/[(){}[\]|\\&@!~:;#]/.test(label)) {
      const escaped = label.replace(/"/g, '#quot;')
      return `${id}${open}"${escaped}"${close}`
    }
    return `${id}${open}${label}${close}`
  }
  return src
    // [label]：标签内不能含 [ { ( —— 排除嵌套与边
    .replace(/(\b\w[\w-]*)\[([^\]"{(\n]*?)\]/g, (_, id, label) => wrapIfNeeded(id, label, '[', ']'))
    // (label)：排除嵌套 ((..))（前面不能是 (），标签内不能含 ( [ { —— 排除 (-x) 边与形如 ({..})
    .replace(/(?<!\()(\b\w[\w-]*)\(([^)"{[\n]*?)\)(?!>)/g, (_, id, label) => wrapIfNeeded(id, label, '(', ')'))
    // {label}：标签内不能含 { [ (
    .replace(/(\b\w[\w-]*)\{([^}"[(\n]*?)\}/g, (_, id, label) => wrapIfNeeded(id, label, '{', '}'))
}

async function initMermaid(): Promise<void> {
  if (mermaidInitialized) return
  const m = await loadMermaid()
  m.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'strict',
    fontFamily: 'inherit',
    // 强制使用 dagre-wrapper：mermaid 11 在某些带 subgraph 与中文标签的图上
    // 会触发 elk 布局器 bug（"Could not find a suitable point for the given distance"）
    flowchart: { defaultRenderer: 'dagre-wrapper' }
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
    // 复制内容：渲染失败后若自动修复成功，此处同步为修复版源码
    let copyableCode = trimmedCode
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(copyableCode)
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

    // 不再创建 hidden renderContainer —— mermaid 11 在 hidden (display:none) 容器里
    // 调 getBBox() 会返回异常 bbox，触发 layout 阶段 'Could not find a suitable point' bug。
    // 直接传 2 参数让 mermaid 自动管理临时 DOM（自动注入 body 并清理）。

    const fullscreenBtn = document.createElement('button')
    fullscreenBtn.className = 'mermaid-toggle-btn'
    fullscreenBtn.textContent = '全屏'
    fullscreenBtn.title = '全屏预览'

    const showRenderError = (err: unknown): void => {
      const errMsg = err instanceof Error ? err.message : 'Mermaid 渲染失败'
      previewDiv.innerHTML = `<span class="mermaid-error-label">Mermaid 渲染失败</span><pre class="mermaid-error-msg">${errMsg.replace(/</g, '&lt;')}</pre>`
      previewDiv.className = 'mermaid-preview mermaid-error'
      sourceBtn.click()
    }

    let renderedSvg = ''
    // 离屏测量宿主：mermaid render 的两参数模式会把 d{id} 临时测量容器裸挂到 body
    // （static 定位、无隐藏样式），瞬态参与文档流 → 撑出贯穿整窗的全局滚动条。
    // 三参数模式让临时容器落在本组件的 absolute+hidden 宿主内，不再影响 body 布局。
    let measureHost: HTMLDivElement | null = document.createElement('div')
    measureHost.style.position = 'absolute'
    measureHost.style.visibility = 'hidden'
    measureHost.style.left = '-9999px'
    measureHost.style.top = '0'
    // 宽度对齐消息正文实际宽度，保证文本折行测量与真实展示一致
    measureHost.style.width = `${containerRef.value.clientWidth}px`
    document.body.appendChild(measureHost)
    try {
      const { svg } = await m.render(id, code.trim(), measureHost)
      renderedSvg = svg
    } catch (firstErr) {
      // 第一次解析失败：尝试自动给含保留字符的标签加引号后再渲染
      const fixedCode = quoteMermaidLabels(code.trim())
      if (fixedCode !== code.trim()) {
        try {
          const { svg } = await m.render(id, fixedCode, measureHost)
          renderedSvg = svg
          // 修复成功：同步源码视图与复制按钮内容，并加"已修复"角标
          sourceDiv.textContent = fixedCode
          copyableCode = fixedCode
          const badge = document.createElement('span')
          badge.className = 'mermaid-fixed-badge'
          badge.textContent = '已修复'
          badge.title = '原源码因节点标签含保留字符无法解析，已自动加引号处理'
          toolbar.appendChild(badge)
        } catch {
          // 修复版仍失败（多为 mermaid elk/dagre 布局阶段问题，非源码语法问题）：
          // 不动源码视图，告知用户可尝试手动给含特殊字符的标签加引号
          showRenderError(firstErr)
        }
      } else {
        showRenderError(firstErr)
      }
    } finally {
      // 测量宿主用完即弃（三参数模式下 mermaid 会清空 container 内容，但宿主 div 本身需自清理）
      measureHost?.remove()
      measureHost = null
    }

    if (renderedSvg) {
      previewDiv.innerHTML = renderedSvg
      // 仅渲染成功时提供全屏入口（失败态展示错误信息，无图可看）
      const captured = renderedSvg
      fullscreenBtn.addEventListener('click', () => openMermaidFullscreen(captured))
      toolbar.appendChild(fullscreenBtn)
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
    if (pre.parentElement?.classList.contains('code-block-wrapper')) continue
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
        // 仅打标（供 renderMermaidBlocks 定位、copy-btn 遍历排除），
        // 不提前 display:none：隐藏会让块高瞬间塌到 0，而图表要等
        // MERMAID_RENDER_DEBOUNCE + render 完成才挂载，期间出现
        // 「高度骤降→SVG 挂载回升」的跳动；保持代码块可见直至
        // renderMermaidBlocks 渲染完成后一次性 replaceWith，高度只切换一次
        pre.className = 'mermaid-block'
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
    }, MERMAID_RENDER_DEBOUNCE)
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
  if (fullscreenVisible.value) {
    document.body.style.overflow = ''
  }
  window.removeEventListener('keydown', handleFullscreenKeydown)
  window.removeEventListener('resize', fitMermaidToWindow)
  handlePanEnd()
  if (containerRef.value) {
    containerRef.value.querySelectorAll('.mermaid-rendered').forEach(el => {
      el.innerHTML = ''
    })
  }
})
</script>

<template>
  <div ref="containerRef" class="markdown-body" @click="handleMarkdownClick">
    <VueMarkdown :source="renderedSource" :plugins="mdPlugins" :options="{ breaks: true }" />
  </div>

  <Teleport to="body">
    <div v-if="fullscreenVisible" class="mermaid-fullscreen-mask">
      <div
        ref="fullscreenViewportRef"
        class="mermaid-fullscreen-viewport"
        :class="{ panning: fullscreenPanning }"
        :style="{ background: fullscreenBg }"
        @mousedown="handlePanStart"
        @wheel.prevent="handleMermaidWheel"
        @touchstart="handleTouchStart"
        @touchmove.prevent="handleTouchMove"
        @touchend="handleTouchEnd"
        @touchcancel="handleTouchEnd"
      >
        <div
          class="mermaid-fullscreen-canvas"
          :style="{
            width: `${fullscreenSize.width}px`,
            height: `${fullscreenSize.height}px`,
            transform: `translate(${fullscreenTranslateX}px, ${fullscreenTranslateY}px) scale(${fullscreenScale})`
          }"
          v-html="fullscreenSvg"
        />
      </div>

      <div class="mermaid-fullscreen-toolbar" @mousedown.stop>
        <button
          class="mermaid-fullscreen-tool-btn"
          title="缩小"
          @click="zoomMermaidCentered(1 / 1.25)"
        >
          −
        </button>
        <span class="mermaid-fullscreen-scale">{{ fullscreenScalePercent }}</span>
        <button class="mermaid-fullscreen-tool-btn" title="放大" @click="zoomMermaidCentered(1.25)">
          +
        </button>
        <button class="mermaid-fullscreen-tool-btn" title="适应窗口" @click="fitMermaidToWindow">
          适应
        </button>
        <div class="mermaid-fullscreen-bg-group" title="背景颜色">
          <button
            v-for="color in FULLSCREEN_BG_PRESETS"
            :key="color"
            class="mermaid-fullscreen-bg-dot"
            :class="{ active: fullscreenBg === color, transparent: color === 'transparent' }"
            :style="color === 'transparent' ? undefined : { background: color }"
            @click="setFullscreenBg(color)"
          />
        </div>
        <button
          class="mermaid-fullscreen-tool-btn close"
          title="关闭 (Esc)"
          @click="closeMermaidFullscreen"
        >
          ✕
        </button>
      </div>

      <div class="mermaid-fullscreen-hint">{{ fullscreenHintText }}</div>
    </div>
  </Teleport>
</template>

<style>
.markdown-body {
  line-height: 1.6;
  word-break: break-word;
}

/* KaTeX 0.18 用自定义标签包裹公式，默认 display:inline 会导致块级公式外边距塌陷 */
.markdown-body eqn {
  display: block;
}

.markdown-body eq {
  display: inline;
}

.markdown-body pre {
  background: var(--ink-island);
  padding: 12px;
  border-radius: 6px;
  /* 巨型代码块封顶（与 chatRow.ts CODE_BLOCK_BODY_MAX 估值口径对齐）：
   * 无界行高（实测可达 3 万 px+）在 hljs 高亮前后高度漂移，虚拟滚动重挂载时
   * 巨量塌缩 delta 导致下方内容整体上移 + scrollTop 越界被钳底（上滚跳末尾） */
  max-height: 480px;
  overflow: auto;
  /* 8px 间距挂在 pre 本身而非 .code-block-wrapper：流式中 post-processing 跳过
   * attachCodeCopyBtns，pre 为裸元素；SSE 结束才包 wrapper（margin 8px 0），
   * 若间距只在 wrapper 上，结束瞬间代码块 0 → 8px 净增（margin 折叠后恰 +8px）
   * → 视口「往下沉」。间距上移到 pre 后两个状态盒模型一致，高度恒等不跳变 */
  margin: 8px 0;
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
  font-family: var(--font-mono);
  font-size: 13px;
}

.markdown-body pre code {
  color: #e8e3d8;
  background: transparent;
  padding: 0;
}

/* 抵消 github.css 的 pre code.hljs { padding: 1em; display: block; background: #fff }：
   流式中 post-processing 跳过 hljs，SSE 结束 finishStreamRender 高亮时 code 净增
   ~2em 高度 + 闪白（SSE 结束视口下沉的根因）。padding/background 归零后高亮
   只改变字色不改盒模型，pre 自身的 padding: 12px 与深底主题保持一致 */
.markdown-body pre code.hljs {
  padding: 0;
  background: transparent;
}

.markdown-body :not(pre) > code {
  background: var(--paper-warm);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--vermilion);
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
  border-left: 4px solid var(--vermilion-line);
  padding-left: 12px;
  margin: 8px 0;
  color: var(--paper-ink-3);
}

.markdown-body table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}

.markdown-body th,
.markdown-body td {
  border: 1px solid var(--paper-line);
  padding: 8px;
  text-align: left;
}

.markdown-body th {
  background: var(--paper-warm);
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
  color: var(--vermilion);
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
  border: 1px solid var(--vermilion-line);
  border-radius: 6px;
  background: var(--vermilion-soft);
  color: var(--vermilion);
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  vertical-align: 1px;
  cursor: pointer;
  transition: all 0.2s;
}

.markdown-body a.knowledge-citation-link:hover {
  border-color: var(--vermilion);
  background: #fde8d8;
  color: var(--vermilion-hover);
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

/* ---- 数学公式（KaTeX）横向溢出滚动 ----
 * katex.min.css 的 .katex-display 无 overflow 处理，长公式（内部 nowrap）
 * 在窄容器（手机端）会撑爆消息气泡宽度；钳制在容器宽内滚动查看 */
.markdown-body .katex-display {
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  /* 公式与滚动条不贴边 */
  padding: 4px 2px;
}

.markdown-body .mermaid-container {
  margin: 8px 0;
  border: 1px solid var(--paper-line);
  border-radius: 6px;
  overflow: hidden;
}

.markdown-body .mermaid-toolbar {
  display: flex;
  gap: 0;
  background: var(--paper-warm);
  border-bottom: 1px solid var(--paper-line);
  padding: 0;
}

.markdown-body .mermaid-toggle-btn {
  padding: 4px 14px;
  font-size: 12px;
  font-weight: 500;
  color: var(--paper-ink-4);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 0.2s;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}

.markdown-body .mermaid-toggle-btn:hover {
  color: var(--paper-ink);
}

.markdown-body .mermaid-toggle-btn.active {
  color: var(--paper-ink);
  border-bottom-color: var(--vermilion);
  background: var(--paper-card);
}

.markdown-body .mermaid-preview {
  padding: 12px;
  background: var(--paper);
  /* 大图封顶内部滚动（全屏预览不受影响），防止超大 SVG 无限撑高虚拟行 */
  max-height: 480px;
  overflow: auto;
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

.markdown-body .mermaid-fixed-badge {
  display: inline-block;
  padding: 1px 8px;
  margin-left: auto;
  order: 99;
  font-size: 11px;
  font-weight: 500;
  color: #15803d;
  background: #dcfce7;
  border-radius: 10px;
  user-select: none;
}

/* ---------- Mermaid 全屏预览覆盖层 ---------- */

.mermaid-fullscreen-mask {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: rgba(15, 23, 42, 0.92);
}

.mermaid-fullscreen-viewport {
  position: absolute;
  inset: 0;
  overflow: hidden;
  cursor: grab;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
  touch-action: none;
}

.mermaid-fullscreen-viewport.panning {
  cursor: grabbing;
}

.mermaid-fullscreen-canvas {
  position: absolute;
  top: 0;
  left: 0;
  transform-origin: 0 0;
}

/* 覆盖 mermaid 内联的 max-width/width，使 SVG 精确铺满 canvas（尺寸随缩放由 transform 控制） */
.mermaid-fullscreen-canvas svg {
  width: 100% !important;
  height: 100% !important;
  max-width: none !important;
  display: block;
}

.mermaid-fullscreen-toolbar {
  position: absolute;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 6px 10px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
}

.mermaid-fullscreen-tool-btn {
  min-width: 28px;
  height: 28px;
  padding: 0 10px;
  font-size: 13px;
  color: #475569;
  background: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.mermaid-fullscreen-tool-btn:hover {
  color: #1e293b;
  background: #f1f5f9;
}

.mermaid-fullscreen-tool-btn.close {
  margin-left: 6px;
  border-left: 1px solid #e2e8f0;
  border-radius: 0 4px 4px 0;
}

.mermaid-fullscreen-scale {
  min-width: 46px;
  text-align: center;
  font-size: 12px;
  font-weight: 500;
  color: #334155;
  user-select: none;
}

.mermaid-fullscreen-bg-group {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 4px;
  padding: 0 10px;
  border-right: 1px solid #e2e8f0;
}

.mermaid-fullscreen-bg-dot {
  width: 16px;
  height: 16px;
  padding: 0;
  border: 1px solid rgba(15, 23, 42, 0.2);
  border-radius: 50%;
  cursor: pointer;
  transition: all 0.2s;
}

.mermaid-fullscreen-bg-dot:hover {
  transform: scale(1.15);
}

.mermaid-fullscreen-bg-dot.active {
  box-shadow: 0 0 0 2px #409eff;
}

.mermaid-fullscreen-bg-dot.transparent {
  background-image: conic-gradient(#cbd5e1 25%, transparent 0 50%, #cbd5e1 0 75%, transparent 0);
  background-size: 8px 8px;
  background-color: #fff;
}

.mermaid-fullscreen-hint {
  position: absolute;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  padding: 4px 12px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.78);
  background: rgba(15, 23, 42, 0.55);
  border-radius: 12px;
  user-select: none;
  pointer-events: none;
}
</style>
