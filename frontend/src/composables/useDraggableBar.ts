/**
 * 最小化悬浮条拖动 composable
 *
 * 统一三处最小化等待悬浮条（QuestionDialog / HumanInputDialog /
 * AgentChat minimized-waits 组）的拖动逻辑：
 * - pointerdown 挂 window 级 pointermove/pointerup（bar 拖动中会自行移动，
 *   用 window 监听保证指针移出 bar 后仍跟手）
 * - 移动中 clamp 在视口内；拖动期间禁用文本选中
 * - 移动距离 < DRAG_THRESHOLD 视为点击（不记位置，@click 恢复行为不受影响）；
 *   真实拖动松手后浏览器补发的 click 被吞掉（防「拖开即弹回」）
 * - 位置持久化 localStorage（按 storageKey 区分），下次显示恢复；
 *   无记录时返回空 style（走 CSS 默认 right/bottom 定位，零回归）
 * - 读取记录/窗口 resize 均做视口 clamp，防 bar 停在视口外
 *
 * 用法：
 *   const { barStyle, onPointerDown, isDragging } = useDraggableBar('key')
 *   <button :style="barStyle" @pointerdown="onPointerDown" @click="restore">
 */

import { computed, onUnmounted, ref, type CSSProperties } from 'vue'

/** 位移小于该值视为点击（像素），与浏览器原生拖拽阈值近似 */
const DRAG_THRESHOLD = 4

/** 拖动松手后吞 click 的时间窗口（毫秒）：浏览器补发的 click 紧随 pointerup */
const SWALLOW_CLICK_WINDOW_MS = 300

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

/**
 * 读取持久化位置并 clamp 到当前视口；无记录/解析失败返回 null（走默认定位）。
 * 上次在大屏放到右侧后换小屏打开时，clamp 保证 bar 仍可见（不直接丢弃记录）
 */
function loadPosition(storageKey: string): { left: number; top: number } | null {
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { left?: unknown; top?: unknown }
    const left = Number(parsed.left)
    const top = Number(parsed.top)
    if (!Number.isFinite(left) || !Number.isFinite(top)) return null
    return clampToViewport(left, top)
  } catch {
    return null
  }
}

function clampToViewport(left: number, top: number): { left: number; top: number } {
  return {
    left: clamp(left, 0, Math.max(window.innerWidth - DRAG_THRESHOLD, 0)),
    top: clamp(top, 0, Math.max(window.innerHeight - DRAG_THRESHOLD, 0))
  }
}

export function useDraggableBar(storageKey: string) {
  const saved = loadPosition(storageKey)
  const position = ref<{ left: number; top: number } | null>(saved)
  const isDragging = ref(false)

  // 无记录时空 style → CSS 默认 right/bottom；有记录时内联 left/top。
  // 必须同时置 right/bottom: auto——CSS 里 bar 默认用 right/bottom 定位，
  // 只注入 left/top 会形成四边偏移过约束，fixed 元素被拉伸成视口大小
  const barStyle = computed<CSSProperties>(() =>
    position.value
      ? {
          left: `${position.value.left}px`,
          top: `${position.value.top}px`,
          right: 'auto',
          bottom: 'auto'
        }
      : {}
  )

  let startX = 0
  let startY = 0
  let originLeft = 0
  let originTop = 0
  let moved = false
  let dragging = false
  /** 拖动结束后吞 click 的时间窗口截止点（performance.now 基准） */
  let swallowClickUntil = 0

  /**
   * 捕获阶段吞掉「拖动结束后」浏览器补发的那次 click。
   *
   * 拖动松手后浏览器会在 bar 上派发 click（pointerdown/up 同元素），
   * 不吞掉会立刻触发 @click 的「恢复弹窗」——用户刚把 bar 拖开就被弹窗重新遮挡。
   * 用时间窗口（而非一次性监听）判定：既不依赖 event.target（拖出元素后
   * pointerup 的 target 会变），也无需定时器清理（避免 setTimeout(0) 早于
   * click 派发导致判定失效）。
   */
  function swallowClick(event: MouseEvent): void {
    if (performance.now() > swallowClickUntil) return
    swallowClickUntil = 0
    event.stopImmediatePropagation()
    event.preventDefault()
  }

  function onPointerMove(event: PointerEvent): void {
    const dx = event.clientX - startX
    const dy = event.clientY - startY
    if (!moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return
    moved = true
    if (!dragging) {
      dragging = true
      isDragging.value = true
      // 拖动期间禁用文本选中（防拖动时选中页面文字，松手后残留高亮）
      document.body.style.userSelect = 'none'
    }
    const width = window.innerWidth
    const height = window.innerHeight
    position.value = {
      left: clamp(originLeft + dx, 0, Math.max(width - DRAG_THRESHOLD, 0)),
      top: clamp(originTop + dy, 0, Math.max(height - DRAG_THRESHOLD, 0))
    }
  }

  function onPointerUp(): void {
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', onPointerUp)
    document.body.style.userSelect = ''
    if (dragging) {
      isDragging.value = false
      dragging = false
      // 后续时间窗口内到达的 click 判定为拖动收尾，吞掉（见 swallowClick 注释）
      swallowClickUntil = performance.now() + SWALLOW_CLICK_WINDOW_MS
      try {
        localStorage.setItem(storageKey, JSON.stringify(position.value))
      } catch {
        // 存储失败不影响拖动功能（下次退回默认位置）
      }
    }
    moved = false
  }

  function onPointerDown(event: PointerEvent): void {
    // 仅主键（左键）；右键/中键不触发拖动
    if (event.button !== 0) return
    swallowClickUntil = 0 // 新交互开始：清除上次拖动残留的吞 click 窗口
    startX = event.clientX
    startY = event.clientY
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
    // 记录拖动起点：有内联位置用之，否则用 getBoundingClientRect 实测
    // （兼容 CSS right/bottom 定位的默认位置，首次拖动即从视觉位置出发）
    originLeft = position.value?.left ?? rect.left
    originTop = position.value?.top ?? rect.top
    moved = false
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
  }

  function onWindowResize(): void {
    // 原地 clamp 到新视口内（保留用户选定位置，不重置回默认）
    const current = position.value
    if (!current) return
    position.value = clampToViewport(current.left, current.top)
  }

  window.addEventListener('resize', onWindowResize)
  // 拖动收尾吞 click：常驻 window 捕获监听（内部按时间窗口判定，零成本）
  window.addEventListener('click', swallowClick, true)
  onUnmounted(() => {
    window.removeEventListener('resize', onWindowResize)
    window.removeEventListener('click', swallowClick, true)
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', onPointerUp)
    document.body.style.userSelect = ''
  })

  return { barStyle, onPointerDown, isDragging }
}
