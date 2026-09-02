/**
 * 工具块展开/收起状态
 * @description 聊天段级虚拟行会随滚动卸载，展开状态须存放在组件外的模块级
 * 响应式 Map 中（key = 虚拟行 key）才能在行重挂后保持。
 * 语义：Map 中存在该 key 时用户已手动操作过，以 Map 值为准；
 * 不存在时回退行级默认值（流式中最新工具自动展开，其余折叠，见 chatRow.isLatestTool）
 */

import { reactive } from 'vue'

/** 用户手动展开/收起过的工具行覆盖状态 */
const overrides = reactive(new Map<string, boolean>())

/** 是否手动操作过该工具行；是则返回覆盖值，否则 undefined（走行级默认） */
export function getToolExpandOverride(key: string): boolean | undefined {
  return overrides.get(key)
}

/** 记录用户手动展开/收起 */
export function toggleToolExpand(key: string, expanded: boolean): void {
  overrides.set(key, expanded)
}

/** 会话切换时清理，避免跨会话残留 */
export function clearToolExpandOverrides(): void {
  overrides.clear()
}
