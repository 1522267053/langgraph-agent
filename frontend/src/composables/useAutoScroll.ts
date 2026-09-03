import { onScopeDispose, ref, watch, type Ref, type WatchSource } from 'vue'
import { AUTO_SCROLL_THROTTLE_MS } from '@/constants/timing'

interface UseAutoScrollOptions {
  threshold?: number
  /** 自动滚动节流间隔（ms），leading + trailing 模式 */
  throttleMs?: number
  /** 手势有效窗口（ms）：真实输入后该时间内的 scroll 事件才被视为用户滚动 */
  gestureWindowMs?: number
}

/**
 * 通用自动滚动 composable
 *
 * - 用户在底部时自动滚动到最新内容，上滚停止跟随，回到底部恢复
 * - 手势白名单：仅 wheel/touchmove/pointerdown 真实输入后的窗口期内，scroll 事件
 *   才被视为用户滚动；程序化贴底（无手势）一律忽略，不依赖启发式推断
 * - wheel 支持嵌套滚动边界穿透：内层容器滚到边界后手势上交主容器
 * - ResizeObserver 感知容器与内容尺寸变化（流式 Markdown、高亮、图片撑高），
 *   无需逐项列举 watch sources 也能跟随
 * - 所有贴底入口统一经 rAF 单飞 + leading/trailing 节流合帧，一帧最多一次滚动
 * - autoScroll 可作为用户偏好 v-model 绑定（如 DisplayToggle）
 */
export function useAutoScroll(
  containerRef: Ref<HTMLElement | null>,
  watchSources: WatchSource[],
  options: UseAutoScrollOptions = {}
) {
  const { threshold = 50, throttleMs = AUTO_SCROLL_THROTTLE_MS, gestureWindowMs = 500 } = options
  const autoScroll = ref(true)
  const isAtBottom = ref(true)
  const userScrolledUp = ref(false)
  let _lastGestureAt = 0
  let _lastScrollAt = 0
  let _trailingTimer: ReturnType<typeof setTimeout> | null = null
  let _scrollFrame: number | null = null
  let _lastScrollTop = 0
  let _wasScrollable = false

  function hasScrollableOverflow(el: HTMLElement): boolean {
    return el.scrollHeight - el.clientHeight > 1
  }

  /**
   * 事件目标是否位于 root 内部的嵌套滚动容器（工具输出、思考块等）
   * 这类事件是用户在滚动子容器，不应视为对主容器的滚动意图
   */
  function isNestedScrollTarget(event: Event, root: HTMLElement): boolean {
    const target = event.target
    if (!(target instanceof Element) || target === root || !root.contains(target)) {
      return false
    }
    let node: Element | null = target
    while (node && node !== root) {
      if (node instanceof HTMLElement) {
        const overflowY = getComputedStyle(node).overflowY
        if ((overflowY === 'auto' || overflowY === 'scroll') && hasScrollableOverflow(node)) {
          return true
        }
      }
      node = node.parentElement
    }
    return false
  }

  /**
   * wheel 的嵌套滚动边界穿透：找到光标下最内层可滚动容器，朝滚动方向仍有余量
   * 则手势被内层消费；已到边界则穿透给主容器（参考 opencode 的 boundary gesture）
   */
  function nestedScrollConsumesGesture(event: WheelEvent, root: HTMLElement): boolean {
    const target = event.target
    if (!(target instanceof Element) || target === root || !root.contains(target)) {
      return false
    }
    let node: Element | null = target
    while (node && node !== root) {
      if (node instanceof HTMLElement) {
        const overflowY = getComputedStyle(node).overflowY
        if ((overflowY === 'auto' || overflowY === 'scroll') && hasScrollableOverflow(node)) {
          // 只关心方向符号，deltaMode 不影响边界判定
          if (event.deltaY < 0) return node.scrollTop > 0
          if (event.deltaY > 0) {
            return node.scrollTop + node.clientHeight < node.scrollHeight - 1
          }
          return true
        }
      }
      node = node.parentElement
    }
    return false
  }

  // ---- ResizeObserver：感知容器视口与内容尺寸变化 ----
  let _resizeObserver: ResizeObserver | null = null
  let _observedContainer: HTMLElement | null = null
  let _observedContent: Element | null = null

  if (typeof ResizeObserver !== 'undefined') {
    _resizeObserver = new ResizeObserver(() => {
      // 流式输出/Markdown/高亮/图片撑高与视口变化统一汇入条件贴底：
      // userScrolledUp、节流与合帧由 maybeScrollToBottom 内部裁决
      maybeScrollToBottom()
    })
    onScopeDispose(() => {
      _resizeObserver?.disconnect()
      _resizeObserver = null
    })
  }

  /** 绑定容器与其内容子元素；容器切换（如 el-scrollbar wrapRef 就绪）后重绑 */
  function _observeContentGrowth(): void {
    const el = containerRef.value
    if (!_resizeObserver || !el) return
    const content = el.firstElementChild
    if (_observedContainer === el && _observedContent === content) return
    _resizeObserver.disconnect()
    _resizeObserver.observe(el)
    if (content instanceof Element) _resizeObserver.observe(content)
    _observedContainer = el
    _observedContent = content
  }

  watch(
    containerRef,
    () => {
      _observeContentGrowth()
    },
    { flush: 'post' }
  )

  function cancelPendingScroll(): void {
    if (_trailingTimer) {
      clearTimeout(_trailingTimer)
      _trailingTimer = null
    }
    if (_scrollFrame !== null) {
      cancelAnimationFrame(_scrollFrame)
      _scrollFrame = null
    }
  }

  function performScrollToBottom(): void {
    const el = containerRef.value
    if (!el) return
    // 程序化贴底引发的 scroll 事件由手势白名单忽略（见 handleScroll），无需额外标志位
    el.scrollTop = el.scrollHeight
    _lastScrollTop = el.scrollTop
    isAtBottom.value = true
    userScrolledUp.value = false
  }

  /** rAF 单飞合帧：一帧内多次触发（如 RO 连续回调）只执行一次滚动 */
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
    _lastGestureAt = 0
    _lastScrollAt = 0
    _lastScrollTop = containerRef.value?.scrollTop || 0
    _wasScrollable = containerRef.value ? hasScrollableOverflow(containerRef.value) : false
    cancelPendingScroll()
  }

  /** 绑定到容器 @wheel / @touchmove / @pointerdown 事件，标记用户主动滚动意图 */
  function onUserScrollIntent(event?: Event): void {
    const el = containerRef.value
    if (!el) return
    if (event) {
      // wheel 按边界穿透判定：手势被嵌套容器消费时不影响主容器跟随状态
      if (event.type === 'wheel' && nestedScrollConsumesGesture(event as WheelEvent, el)) {
        return
      }
      // 其余事件无方向信息，保持整层拦截
      if (event.type !== 'wheel' && isNestedScrollTarget(event, el)) return
    }
    _wasScrollable = hasScrollableOverflow(el)
    // 手势白名单：真实输入武装窗口期，此后窗口内的 scroll 事件才被视为用户滚动
    _lastGestureAt = Date.now()
    if (!_wasScrollable) {
      userScrolledUp.value = false
      isAtBottom.value = true
      _lastScrollTop = el.scrollTop
      return
    }

    cancelPendingScroll()

    const wheelUp = event?.type === 'wheel' && (event as WheelEvent).deltaY < 0
    const scrollbarDrag = event?.type === 'pointerdown'
    const awayFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight > threshold
    if (wheelUp || scrollbarDrag || awayFromBottom) markUserScrolledUp()
  }

  /** 绑定到容器 @scroll 事件 */
  function handleScroll(): void {
    const el = containerRef.value
    if (!el) return
    const { scrollTop, scrollHeight, clientHeight } = el
    const scrollable = scrollHeight - clientHeight > 1
    _wasScrollable = scrollable
    isAtBottom.value = scrollable ? scrollHeight - scrollTop - clientHeight <= threshold : true

    if (!scrollable) {
      // 没有滚动范围时不存在“主动上滚”
      userScrolledUp.value = false
      _lastScrollTop = scrollTop
      return
    }

    // 手势白名单：距最近一次真实输入超出窗口期的 scroll 事件一律视为程序化滚动
    // （贴底跟随、内容撑高引发的位置调整等），只更新基准、不推断用户意图
    if (Date.now() - _lastGestureAt >= gestureWindowMs) {
      _lastScrollTop = scrollTop
      return
    }

    if (scrollTop < _lastScrollTop - 1) {
      // 真实输入后的上滚：立即停止跟随（即使仍在底部阈值内）
      markUserScrolledUp()
    } else if (scrollTop > _lastScrollTop + 1 && isAtBottom.value) {
      // 用户主动滚回底部（含拖动滚动条）→ 恢复自动滚动
      userScrolledUp.value = false
    }
    _lastScrollTop = scrollTop
  }

  // 用户开启 autoScroll 时重置上滚状态
  watch(autoScroll, val => {
    if (val) {
      userScrolledUp.value = false
    } else {
      cancelPendingScroll()
    }
  })

  // 监听数据源变化，触发条件性滚动（DOM 尺寸变化已由 RO 覆盖，此处兜底纯数据态变化）
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
