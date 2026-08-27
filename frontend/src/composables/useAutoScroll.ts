import { ref, watch, type Ref, type WatchSource } from 'vue'

interface UseAutoScrollOptions {
  threshold?: number
  /** 自动滚动节流间隔（ms），leading + trailing 模式 */
  throttleMs?: number
}

/**
 * 通用自动滚动 composable
 *
 * - 用户在底部时自动滚动到最新内容
 * - 用户上滚后停止自动滚动，回到底部后恢复
 * - 程序滚动不触发 handleScroll 误判（programmatic scroll guard）
 * - autoScroll 可作为用户偏好 v-model 绑定（如 DisplayToggle）
 */
export function useAutoScroll(
  containerRef: Ref<HTMLElement | null>,
  watchSources: WatchSource[],
  options: UseAutoScrollOptions = {}
) {
  const { threshold = 50, throttleMs = 200 } = options
  const autoScroll = ref(true)
  const isAtBottom = ref(true)
  const userScrolledUp = ref(false)
  let _programmaticScroll = false
  let _lastScrollAt = 0
  let _trailingTimer: ReturnType<typeof setTimeout> | null = null
  let _scrollFrame: number | null = null
  let _programmaticReleaseFrame: number | null = null
  let _lastUserScrollAt = 0
  let _lastScrollTop = 0
  let _lastScrollHeight = 0
  let _wasScrollable = false

  function hasScrollableOverflow(el: HTMLElement): boolean {
    return el.scrollHeight - el.clientHeight > 1
  }

  function cancelPendingScroll(): void {
    if (_trailingTimer) {
      clearTimeout(_trailingTimer)
      _trailingTimer = null
    }
    if (_scrollFrame !== null) {
      cancelAnimationFrame(_scrollFrame)
      _scrollFrame = null
    }
    if (_programmaticReleaseFrame !== null) {
      cancelAnimationFrame(_programmaticReleaseFrame)
      _programmaticReleaseFrame = null
    }
    _programmaticScroll = false
  }

  function performScrollToBottom(): void {
    if (!containerRef.value) return
    if (_programmaticReleaseFrame !== null) {
      cancelAnimationFrame(_programmaticReleaseFrame)
    }
    _programmaticScroll = true
    const el = containerRef.value
    el.scrollTop = el.scrollHeight
    _lastScrollTop = el.scrollTop
    _lastScrollHeight = el.scrollHeight
    _wasScrollable = hasScrollableOverflow(el)
    isAtBottom.value = true
    userScrolledUp.value = false
    _programmaticReleaseFrame = requestAnimationFrame(() => {
      _programmaticReleaseFrame = null
      _programmaticScroll = false
    })
  }

  function scheduleScrollToBottom(): void {
    if (_scrollFrame !== null) return
    _scrollFrame = requestAnimationFrame(() => {
      // 再等一帧，让工具结果、高亮和 Markdown 的后续 DOM 更新先完成。
      _scrollFrame = requestAnimationFrame(() => {
        _scrollFrame = null
        if (autoScroll.value && !userScrolledUp.value) performScrollToBottom()
      })
    })
  }

  function markUserScrolledUp(): void {
    userScrolledUp.value = true
    cancelPendingScroll()
  }

  function scrollToBottom(): void {
    cancelPendingScroll()
    performScrollToBottom()
  }

  /** 内容变化时条件性滚动（autoScroll && !userScrolledUp），按 throttleMs 节流（leading + trailing） */
  function maybeScrollToBottom(): void {
    const el = containerRef.value
    if (!el) return
    const scrollable = hasScrollableOverflow(el)
    const becameScrollable = !_wasScrollable && scrollable
    _wasScrollable = scrollable

    // 没有实际滚动范围时，用户不可能处于“主动上滚”状态。
    if (!scrollable) {
      userScrolledUp.value = false
      isAtBottom.value = true
      return
    }
    if (!autoScroll.value) return
    if (becameScrollable) {
      userScrolledUp.value = false
      _lastScrollAt = Date.now()
      scheduleScrollToBottom()
      return
    }
    if (userScrolledUp.value) return
    const now = Date.now()
    // leading：距上次滚动超过阈值则立即触发
    if (now - _lastScrollAt >= throttleMs) {
      _lastScrollAt = now
      scheduleScrollToBottom()
      return
    }
    // trailing：冷却期内安排一次兜底，确保最新内容最终被滚动到底部
    if (_trailingTimer) return
    _trailingTimer = setTimeout(
      () => {
        _trailingTimer = null
        _lastScrollAt = Date.now()
        scheduleScrollToBottom()
      },
      throttleMs - (Date.now() - _lastScrollAt)
    )
  }

  /** 重置全部滚动状态（切换 session 等场景调用，恢复自动滚动到底部） */
  function resetAutoScrollState(): void {
    userScrolledUp.value = false
    isAtBottom.value = true
    _lastUserScrollAt = 0
    _lastScrollAt = 0
    _lastScrollTop = containerRef.value?.scrollTop || 0
    _lastScrollHeight = containerRef.value?.scrollHeight || 0
    _wasScrollable = containerRef.value ? hasScrollableOverflow(containerRef.value) : false
    cancelPendingScroll()
  }

  /** 绑定到容器 @wheel / @touchmove / @pointerdown 事件，标记用户主动滚动意图 */
  function onUserScrollIntent(event?: Event): void {
    const el = containerRef.value
    if (!el) return
    _wasScrollable = hasScrollableOverflow(el)
    if (!_wasScrollable) {
      userScrolledUp.value = false
      isAtBottom.value = true
      _lastScrollTop = el.scrollTop
      _lastScrollHeight = el.scrollHeight
      return
    }

    _lastUserScrollAt = Date.now()
    cancelPendingScroll()

    const wheelUp = event?.type === 'wheel' && (event as WheelEvent).deltaY < 0
    const scrollbarDrag = event?.type === 'pointerdown'
    const awayFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight > threshold
    if (wheelUp || scrollbarDrag || awayFromBottom) markUserScrolledUp()
  }

  /** 绑定到容器 @scroll 事件 */
  function handleScroll(): void {
    if (!containerRef.value) return
    const { scrollTop, scrollHeight, clientHeight } = containerRef.value
    _wasScrollable = scrollHeight - clientHeight > 1
    if (!_wasScrollable) {
      userScrolledUp.value = false
      isAtBottom.value = true
      _lastScrollTop = scrollTop
      _lastScrollHeight = scrollHeight
      return
    }

    const recentUserIntent = Date.now() - _lastUserScrollAt < 500
    if (_programmaticScroll && !recentUserIntent) {
      _lastScrollTop = scrollTop
      _lastScrollHeight = scrollHeight
      return
    }

    const atBottom = scrollHeight - scrollTop - clientHeight <= threshold
    const scrollPositionChanged = Math.abs(scrollTop - _lastScrollTop) > 1
    const movedUp = scrollTop < _lastScrollTop - 1
    const contentHeightChanged = Math.abs(scrollHeight - _lastScrollHeight) > 1
    isAtBottom.value = atBottom
    if (movedUp && (recentUserIntent || !contentHeightChanged)) {
      // 即使仍在底部阈值内或 SSE 同时撑高内容，用户向上滚动也应立即停止跟随。
      markUserScrolledUp()
    } else if (!atBottom) {
      // Markdown、图片等撑高内容时 scrollTop 不变，不能误判成用户上滚。
      if (recentUserIntent || (scrollPositionChanged && !contentHeightChanged)) {
        markUserScrolledUp()
      }
    } else if (!movedUp && (recentUserIntent || (scrollPositionChanged && !contentHeightChanged))) {
      // 用户主动滚回底部（包括拖动滚动条）→ 恢复自动滚动
      userScrolledUp.value = false
    }
    _lastScrollTop = scrollTop
    _lastScrollHeight = scrollHeight
  }

  // 用户开启 autoScroll 时重置上滚状态
  watch(autoScroll, val => {
    if (val) {
      userScrolledUp.value = false
    } else {
      cancelPendingScroll()
    }
  })

  // 监听数据源变化，触发条件性滚动
  for (const source of watchSources) {
    watch(source, maybeScrollToBottom, { deep: true })
  }

  return {
    autoScroll,
    isAtBottom,
    userScrolledUp,
    scrollToBottom,
    maybeScrollToBottom,
    handleScroll,
    onUserScrollIntent,
    resetAutoScrollState
  }
}
