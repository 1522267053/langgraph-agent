/**
 * 弹窗退场快照组合式函数
 *
 * 解决：弹窗内容直接绑定 store 状态、提交处理函数在关闭动画开始前同步清空
 * 该状态，导致关闭动画期间内容先消失（只剩空框渐隐）。
 *
 * 机制：源非空时持续缓存最近一次非空副本；源被清空后（关闭动画期间）改渲染
 * 快照；动画结束（el-dialog @closed）时调 reset() 释放快照。
 *
 * 约定：后续新增「内容绑 store + 提交即清空」的弹窗必须复用本函数，
 * 禁止在提交处理函数里延迟清空 store（队列推进/幂等保护是业务语义，不能动）。
 */
import { computed, shallowRef, watch } from 'vue'

export function useDialogExitSnapshot<T>(
  source: () => T,
  isEmpty: (v: T) => boolean
) {
  const last = shallowRef<T | null>(null)

  watch(source, v => {
    if (!isEmpty(v)) last.value = v
  })

  /** 弹窗渲染用：源为空（动画期间）回退到最近非空快照 */
  const display = computed<T>(() => {
    const v = source()
    return isEmpty(v) ? (last.value as T) : v
  })

  /** 动画结束（@closed）或新问题重置时调用，释放快照 */
  const reset = () => {
    last.value = null
  }

  return { display, reset }
}
