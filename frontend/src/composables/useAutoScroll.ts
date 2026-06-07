import { ref, watch, nextTick, type Ref } from 'vue'

interface UseAutoScrollOptions {
  threshold?: number
}

export function useAutoScroll(
  containerRef: Ref<HTMLElement | null>,
  watchSources: Ref[],
  options: UseAutoScrollOptions = {}
) {
  const { threshold = 50 } = options
  const autoScroll = ref(true)
  const isAtBottom = ref(true)

  function scrollToBottom(): void {
    nextTick(() => {
      if (containerRef.value) {
        containerRef.value.scrollTop = containerRef.value.scrollHeight
        autoScroll.value = true
        isAtBottom.value = true
      }
    })
  }

  function handleScroll(): void {
    if (!containerRef.value) return
    const { scrollTop, scrollHeight, clientHeight } = containerRef.value
    const atBottom = scrollHeight - scrollTop - clientHeight <= threshold
    isAtBottom.value = atBottom
    autoScroll.value = atBottom
  }

  for (const source of watchSources) {
    watch(
      source,
      () => {
        if (autoScroll.value) scrollToBottom()
      },
      { deep: true }
    )
  }

  return {
    autoScroll,
    isAtBottom,
    scrollToBottom,
    handleScroll
  }
}
