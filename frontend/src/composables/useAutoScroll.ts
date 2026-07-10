import { ref, watch, nextTick, type Ref, type WatchSource } from 'vue'

interface UseAutoScrollOptions {
  threshold?: number
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
  const { threshold = 50 } = options
  const autoScroll = ref(true)
  const isAtBottom = ref(true)
  const userScrolledUp = ref(false)
  let _programmaticScroll = false

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

  /** 内容变化时条件性滚动（autoScroll && !userScrolledUp） */
  function maybeScrollToBottom(): void {
    if (autoScroll.value && !userScrolledUp.value) {
      nextTick(() => {
        if (userScrolledUp.value) return
        requestAnimationFrame(() => scrollToBottom())
      })
    }
  }

  /** 绑定到容器 @scroll 事件 */
  function handleScroll(): void {
    if (!containerRef.value || _programmaticScroll) return
    const { scrollTop, scrollHeight, clientHeight } = containerRef.value
    const atBottom = scrollHeight - scrollTop - clientHeight <= threshold
    isAtBottom.value = atBottom
    userScrolledUp.value = !atBottom
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
    handleScroll
  }
}
