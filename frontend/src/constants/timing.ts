/** 前端统一时间常量（ms） */

/** 流式期间 markdown 重渲染最小间隔，降低全量渲染频率 */
export const STREAM_RENDER_INTERVAL = 200

/** SSE 流式 chunk 攒批合并间隔，缓冲期内合并 chunk 降低响应写频率 */
export const SSE_FLUSH_INTERVAL = 100

/** 内容变化时自动滚动的节流间隔（leading + trailing） */
export const AUTO_SCROLL_THROTTLE_MS = 100

/** 流结束后 mermaid 图表渲染防抖间隔 */
export const MERMAID_RENDER_DEBOUNCE = 300
