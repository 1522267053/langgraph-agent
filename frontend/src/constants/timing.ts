/** 前端统一时间常量（ms） */

/** 流式期间 markdown 重渲染最小间隔，降低全量渲染频率 */
export const STREAM_RENDER_INTERVAL = 200

/** SSE 流式 chunk 攒批合并间隔，缓冲期内合并 chunk 降低响应写频率 */
export const SSE_FLUSH_INTERVAL = 100

/** 内容变化时自动滚动的节流间隔（leading + trailing） */
export const AUTO_SCROLL_THROTTLE_MS = 50

/** 距底判定阈值（px）：距底部小于该值视为贴底（外层列表跟随与 thinking 块内部跟随共用） */
export const AUTO_SCROLL_BOTTOM_THRESHOLD = 60

/** 贴底跟随的收尾窗口（ms）：程序化贴底后该窗口内的 scroll 事件视为跟随
 *  延续——高速流式下事件派发时内容往往又已增长，几何暂时偏离底部属常态，
 *  不得据此翻转贴底判定（否则按钮闪现且跟随中断） */
export const AUTO_SCROLL_FOLLOW_SETTLE_MS = 200

/** 虚拟列表测量补偿静默窗（ms）：virtualizer 按行高 delta 写 scrollTop 的程序化
 *  补偿会瞬间拉离底部，随后 scroll 事件按几何刷新会把贴底判定翻成 false（跟随
 *  永久中断、scroll-to-bottom 按钮误现，AI 并行多工具行同帧挂载时必现竞态）。
 *  该窗口内的 scroll 事件仅同步基准，不做贴底判定与意图推断 */
export const AUTO_SCROLL_ADJUST_QUIET_MS = 50

/** 流结束后 mermaid 图表渲染防抖间隔 */
export const MERMAID_RENDER_DEBOUNCE = 300

/** 等待用户响应的审批倒计时秒数（非 ms）：与后端 USER_RESPONSE_TIMEOUT_SECONDS
 *  （3600s）对齐，减 2s 缓冲防止前端先于后端归零 */
export const USER_RESPONSE_COUNTDOWN_SECONDS = 3598
