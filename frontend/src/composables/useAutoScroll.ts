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
  let _lastUserScrollAt = 0

  function scrollToBottom(): void {
    if (!containerRef.value) return
    _programmaticScroll = true
    const el = containerRef.value
    el.scrollTop = el.scrollHeight
    isAtBottom.value = true
    userScrolledUp.value = false
    requestAnimationFrame(() => {
      _programmaticScroll = false
    })
  }

  /** 内容变化时条件性滚动（autoScroll && !userScrolledUp），按 throttleMs 节流（leading + trailing） */
  function maybeScrollToBottom(): void {
    if (!autoScroll.value || userScrolledUp.value) return
    const now = Date.now()
    // leading：距上次滚动超过阈值则立即触发
    if (now - _lastScrollAt >= throttleMs) {
      _lastScrollAt = now
      requestAnimationFrame(() => {
        if (!userScrolledUp.value) scrollToBottom()
      })
      return
    }
    // trailing：冷却期内安排一次兜底，确保最新内容最终被滚动到底部
    if (_trailingTimer) return
    _trailingTimer = setTimeout(
      () => {
        _trailingTimer = null
        _lastScrollAt = Date.now()
        requestAnimationFrame(() => {
          if (autoScroll.value && !userScrolledUp.value) scrollToBottom()
        })
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
    if (_trailingTimer) {
      clearTimeout(_trailingTimer)
      _trailingTimer = null
    }
  }

  /** 绑定到容器 @wheel / @touchmove 事件，标记用户主动滚动意图 */
  function onUserScrollIntent(): void {
    _lastUserScrollAt = Date.now()
  }

  /** 绑定到容器 @scroll 事件 */
  function handleScroll(): void {
    if (!containerRef.value || _programmaticScroll) return
    const { scrollTop, scrollHeight, clientHeight } = containerRef.value
    const atBottom = scrollHeight - scrollTop - clientHeight <= threshold
    isAtBottom.value = atBottom
    if (!atBottom) {
      // 不在底部 → 用户已上滑（或 DOM 重排推离底部）
      userScrolledUp.value = true
    } else if (Date.now() - _lastUserScrollAt < 500) {
      // 用户主动滚回底部 → 恢复自动滚动
      userScrolledUp.value = false
    }
    // else: DOM 重排导致位置在底部附近，非用户意图 → 不恢复自动滚动
  }

  // 用户开启 autoScroll 时重置上滚状态
  watch(autoScroll, val => {
    if (val) userScrolledUp.value = false
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
