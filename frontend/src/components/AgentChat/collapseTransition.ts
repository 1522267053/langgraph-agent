/**
 * 工具块高度过渡 hooks
 * @description 高度过渡动画仅应在用户点击后启用——流式 handoff / 流结束的程序性
 * 翻转若也走 300ms 行高渐变，贴底跟随（useAutoScroll 的 RO）会逐帧追新底部，
 * 产生滚动抖动。用法：`<Transition v-bind="启用 ? collapseHooks : {}">`，
 * Transition 实例常驻、v-if 照常控制显隐；hooks 为空对象时翻转瞬时完成，
 * 置为 collapseHooks 后的翻转才带高度过渡。
 */

type CollapseElement = HTMLElement

function withHeightTransition(el: CollapseElement): void {
  el.style.transition = '0.25s height ease, 0.25s padding-top ease, 0.25s padding-bottom ease'
}

export const collapseHooks = {
  onBeforeEnter(el: Element) {
    const e = el as CollapseElement
    e.style.height = '0'
    e.style.overflow = 'hidden'
    withHeightTransition(e)
  },
  onEnter(el: Element) {
    const e = el as CollapseElement
    // 读取 scrollHeight 强制 reflow：让 height:0 先被计算，再设目标高度才能过渡
    e.style.height = `${e.scrollHeight}px`
  },
  onAfterEnter(el: Element) {
    const e = el as CollapseElement
    e.style.height = ''
    e.style.overflow = ''
    e.style.transition = ''
  },
  onBeforeLeave(el: Element) {
    // 起始高度用 offsetHeight（max-height 钳制后的实际渲染高度）而非 scrollHeight
    // （未钳制内容高度）：基准与视觉一致，过渡全程线性收缩；否则过渡大半时间被
    // max-height 钳制、渲染高度不动，末帧瞬间塌缩（"内容突然变没"）
    const e = el as CollapseElement
    e.style.height = `${e.offsetHeight}px`
    e.style.overflow = 'hidden'
    withHeightTransition(e)
  },
  onLeave(el: Element) {
    const e = el as CollapseElement
    // 强制 reflow：让起始高度先被计算，height→0 才能产生过渡
    void e.offsetHeight
    e.style.height = '0'
  },
  onAfterLeave(el: Element) {
    const e = el as CollapseElement
    e.style.height = ''
    e.style.overflow = ''
    e.style.transition = ''
  }
}
