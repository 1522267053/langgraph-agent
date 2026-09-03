/**
 * 消息块（工具/思考等）折叠展开状态
 * @description 聊天段级虚拟行会随滚动卸载，手动展开状态须存放在组件外的模块级
 * 响应式 Map 中（key = 虚拟行 key）才能在行重挂后保持。
 * 语义：Map 中存在该 key 时用户已手动操作过，以 Map 值为准；
 * 不存在时回退调用方的行级默认值（如工具块的 isLatestTool、思考块的 showThinking）。
 * 键空间：虚拟行 key 唯一且行类型单一，tool/thinking 等块天然不冲突，共用一个 Map。
 * 手动状态不持久化（刷新/重进会话后回到默认值）；会话切换时调用
 * clearBlockExpandOverrides() 清理，避免跨会话残留
 */

import { reactive } from 'vue'

/** 用户手动展开/收起过的块覆盖状态 */
const overrides = reactive(new Map<string, boolean>())

/** 是否手动操作过该块；是则返回覆盖值，否则 undefined（走调用方行级默认） */
export function getBlockExpandOverride(key: string): boolean | undefined {
  return overrides.get(key)
}

/** 记录用户手动展开/收起 */
export function toggleBlockExpand(key: string, expanded: boolean): void {
  overrides.set(key, expanded)
}

/** 会话切换时清理，避免跨会话残留 */
export function clearBlockExpandOverrides(): void {
  overrides.clear()
}
