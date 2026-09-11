/**
 * 聊天虚拟行模型
 * @description 将 chatMessages 拍平为段级虚拟行：AI 回合的每个 segment 独占一行，
 * 消息级 UI（头像/头部/尾部）拆分到 first/last 行，消除超长回合的单行巨高问题
 */

import type { StreamingMessage } from '@/composables/useStreamingMessage'
import type { Segment, ToolCall } from '@/types/segment'
import { getBlockExpandOverride } from '@/components/AgentChat/blockExpand'

export type ChatRowKind = 'human' | 'summary' | 'typing' | 'ai'

/** 行在消息内的位置：first=带头部 mid=中间段 last=带尾部 single=头尾同行 */
export type ChatRowPart = 'first' | 'mid' | 'last' | 'single'

export interface ChatRow {
  key: string
  kind: ChatRowKind
  part: ChatRowPart
  msg: StreamingMessage | null
  /** ai 行渲染的段（每段一行） */
  segment?: Segment
  /** 段在消息 segments 中的下标（用于计算消息级上下文标志） */
  segmentIndex?: number
  /** tool 行专用：折叠态是否有单行结果摘要行（false = 纯 JSON/未出结果，估值取矮行）；
   * 非工具行为 undefined */
  toolHasSummary?: boolean
  /** 是否为列表最后一条消息（流式指示器定位） */
  isLast: boolean
}

/** 段级行 key：流式段用 genSegmentId，历史段用按 DB 行生成的确定性 id 兜底 */
export function getSegmentRowKey(msg: StreamingMessage, segment: Segment, idx: number): string {
  return `row-${segment.id || `s-${msg.id}-idx${idx}`}`
}

/**
 * 行高实测缓存：key = 行 key，值 = 上次 measureElement 实测高度（含消息 chrome）
 * @description 展示开关切换会 measure() 清空 virtualizer 内部缓存，未挂载行若回落到
 * 固定粗估（content 268px，实际可达数千 px），滚动挂载时首测产生巨量 delta 并触发
 * 滚动补偿，造成滚动条大幅跳变。估算优先取上次实测值，重测 delta 即收敛到开关切换
 * 的真实增量。会话切换时调用 clearRowSizeCache() 清理
 */
const measuredSizes = new Map<string, number>()

/** 记录行实测高度（在行尺寸变化回调中调用） */
export function rememberRowSize(key: string, size: number): void {
  measuredSizes.set(key, size)
}

/** 清空实测缓存（会话切换时调用，避免跨会话残留） */
export function clearRowSizeCache(): void {
  measuredSizes.clear()
}

/**
 * 折叠态工具块是否有单行结果摘要（与 ToolResultViewer.collapsedSummary 同口径，
 * 构建行时判定一次，避免 estimateSize 滚动期反复 JSON.parse）：
 * 错误首行 / 裸字符串（shell 输出、子Agent 回复、文件写入消息）/ 文件读写与
 * 媒体富结果有摘要；子Agent 运行中的实时输出/正在调用的工具状态走裸字符串
 * 路径，同样有单行摘要；纯 JSON dict（memory、MCP、截断输出等）摘要行隐藏
 */
export function hasToolCollapsedSummary(tool: ToolCall): boolean {
  if (tool.status === 'error') return true
  if (tool.status === 'running') {
    const hasOutput = typeof tool.liveOutput === 'string' && !!tool.liveOutput.trim()
    const hasToolActivity = typeof tool.liveTool === 'string' && !!tool.liveTool.trim()
    if (hasOutput || hasToolActivity) return true
  }
  const raw = tool.result
  if (raw === undefined || raw === null) return false
  let parsed: unknown
  if (typeof raw === 'string') {
    try {
      parsed = JSON.parse(raw)
    } catch {
      return true
    }
  } else if (typeof raw === 'object') {
    parsed = raw
  } else {
    return false
  }
  if (typeof parsed !== 'object' || parsed === null) return false
  const r = parsed as Record<string, unknown>
  if (typeof r.preview_url === 'string' || typeof r.download_url === 'string') return true
  if (tool.name === 'file_read') {
    return !!r.success && typeof r.content === 'string' && typeof r.file_path === 'string'
  }
  if (tool.name === 'text_editor') {
    return !!r.success && typeof r.diff === 'string'
  }
  return false
}

/**
 * 将消息列表拍平为虚拟行
 * @param showStandaloneTyping 流式中但最后一条不是 AI 消息时，追加独立输入指示器行
 */
export function buildChatRows(
  chatMessages: StreamingMessage[],
  showStandaloneTyping: boolean
): ChatRow[] {
  const rows: ChatRow[] = []

  chatMessages.forEach((msg, msgIdx) => {
    const isLastMsg = msgIdx === chatMessages.length - 1

    if (msg.displayType === 'context-summary') {
      rows.push({ key: `m-${msg.id}`, kind: 'summary', part: 'single', msg, isLast: isLastMsg })
      return
    }
    if (msg.role === 'human') {
      rows.push({ key: `m-${msg.id}`, kind: 'human', part: 'single', msg, isLast: isLastMsg })
      return
    }

    // ai 回合：每段一行；segments 为空（流式起始）时输出单个占位行
    const segs = msg.segments
    if (segs.length === 0) {
      rows.push({ key: `m-${msg.id}`, kind: 'ai', part: 'single', msg, isLast: isLastMsg })
      return
    }
    segs.forEach((segment, i) => {
      const part: ChatRowPart =
        segs.length === 1 ? 'single' : i === 0 ? 'first' : i === segs.length - 1 ? 'last' : 'mid'
      rows.push({
        key: getSegmentRowKey(msg, segment, i),
        kind: 'ai',
        part,
        msg,
        segment,
        segmentIndex: i,
        toolHasSummary:
          segment.type === 'tool' && segment.tool
            ? hasToolCollapsedSummary(segment.tool)
            : undefined,
        isLast: isLastMsg
      })
    })
  })

  if (showStandaloneTyping) {
    rows.push({ key: 'typing', kind: 'typing', part: 'single', msg: null, isLast: true })
  }
  return rows
}

/** 展示开关状态：影响行高估算（关闭时思考段只剩头部） */
export interface RowSizePrefs {
  showThinking?: boolean
  /** 消息滚动容器宽度（px）：按实际宽度动态计算折行单位数，窄屏防低估；
   * 缺省回落桌面校准常数 */
  containerWidth?: number
}

// ---- 内容感知估值常量（与 AIMessageContent.vue 渲染 CSS 对齐，按官方建议偏保守高估）----

/** thinking：13px 等宽全角宽约 13px，聊天区行宽 ~650px，取 45 即低估行宽 → 高估行数 */
const THINKING_UNITS_PER_LINE = 45
const THINKING_LINE_HEIGHT = 13 * 1.6
/** thinking 正文上下 padding（14px × 2） */
const THINKING_BODY_PADDING = 28
/** thinking 正文封顶（.thinking-content max-height: 400px） */
const THINKING_BODY_MAX = 400
/** thinking 块外 chrome：头部 ~36 + 块外边距 12 */
const THINKING_CHROME = 48
/** content：15px 全角宽约 15px，行宽 ~600px，保守取 38 */
const CONTENT_UNITS_PER_LINE = 38
const CONTENT_LINE_HEIGHT = 15 * 1.7
/** Markdown 块级元素（标题/列表/代码块）额外垂直留白系数 */
const CONTENT_MARKDOWN_FACTOR = 1.25
/** content 行高保底：padding 40 + border 2 + margin 10（.message-content） */
const CONTENT_MIN = 220
const CONTENT_CHROME = 52
/** todo 块外 chrome：padding 40 + 头部徽标行 ~38 + 块外边距 12 */
const TODO_CHROME = 90
const TODO_ITEM_HEIGHT = 31
const TODO_ITEM_GAP = 6
/** todo 项文本：13px（.todo-content），行高对齐 thinking 的 13×1.6 保守取值 */
const TODO_LINE_HEIGHT = 13 * 1.6
/** todo 项折行单位数：块 padding 40 + 状态图标 26 + 优先级标签占位后再留余量，
 * 取保守低值（低值=高估行数，符合官方"estimate the largest possible size"） */
const TODO_UNITS_PER_LINE = 30
/** todo 列表封顶（.todo-block :deep(.todo-list) max-height: 320px） */
const TODO_BODY_MAX = 320
/** compress-summary 内容封顶（.compress-summary-content max-height: 400px） */
const SUMMARY_BODY_MAX = 400
/** compress-summary 块外 chrome：label 行 ~28（14px 文本 + 6px margin-bottom）+ 容器 padding 28 + 块外边距 8 */
const SUMMARY_CHROME = 64
/** compress-summary 内容内边距：与 .compress-summary 的 padding 14px × 2 一致 */
const SUMMARY_BODY_PADDING = 28

/** CJK/全角字符（近似全角宽度），其余按半宽 0.5 单位 */
const CJK_CHAR = /[\u2e80-\u9fff\uF900-\uFAFF\uFF00-\uFFEF\u3000-\u303F]/g

/** 消息行横向 chrome 占位（头像 + 气泡 padding 等），估值时从容器宽度扣除 */
const CONTENT_WIDTH_RESERVE = 160
/** 动态折行单位数下限，防止极窄容器下估值发散 */
const UNITS_PER_LINE_MIN = 16

/** 按容器宽度动态计算每行全角单位数；宽度未知时回落桌面校准常数 */
function unitsPerLine(
  containerWidth: number | undefined,
  charWidth: number,
  desktopFallback: number
): number {
  if (!containerWidth) return desktopFallback
  return Math.max(
    UNITS_PER_LINE_MIN,
    Math.floor((containerWidth - CONTENT_WIDTH_RESERVE) / charWidth)
  )
}

/** 文本测高：按显式换行拆行，逐行按字符宽度加权估算折行数（CJK 计 1 单位，
 * 其余计 0.5）。unitsPerLine 应取保守低值——低估行宽即高估行数，符合
 * TanStack Virtual 官方建议（estimate the largest possible size）：高估在
 * 实测后收缩扰动小，低估会导致行挂载时生长推挤布局 */
function estimateTextHeight(
  text: string | undefined,
  unitsPerLine: number,
  lineHeight: number
): number {
  if (!text) return 0
  let lines = 0
  for (const line of text.split('\n')) {
    if (line === '') {
      lines += 1
      continue
    }
    const cjk = line.match(CJK_CHAR)?.length ?? 0
    const weight = cjk + (line.length - cjk) * 0.5
    lines += Math.ceil(weight / unitsPerLine)
  }
  return lines * lineHeight
}

/** 行高初值：优先取实测缓存；无缓存时按段类型 + 内容长度估值（thinking/todo
 * 受渲染层 CSS 封顶约束、content 无界按文本测高高估），头部/尾部行附加消息
 * chrome 高度，减少测量收敛迭代 */
export function estimateRowSize(row: ChatRow | undefined, prefs?: RowSizePrefs): number {
  if (!row) return 150
  // 实测值已含 chrome，直接返回，不再走类型估值与 chrome 加成
  const measured = measuredSizes.get(row.key)
  if (measured) return measured
  switch (row.kind) {
    case 'typing':
      return 56
    case 'summary': {
      // 摘要正文 13px / line-height 1.6，与 .compress-summary-content CSS 对齐；
      // 受渲染层 400px max-height 封顶：文本超长时走 SUMMARY_BODY_MAX 而非无限生长，
      // 避免 virtualizer 首次挂载产生巨量 delta 触发滚动条跳变。
      const body = Math.min(
        SUMMARY_BODY_MAX,
        estimateTextHeight(
          row.msg?.content,
          unitsPerLine(prefs?.containerWidth, 13, CONTENT_UNITS_PER_LINE),
          13 * 1.6
        ) + SUMMARY_BODY_PADDING
      )
      return SUMMARY_CHROME + body
    }
    case 'human':
      return 90
    case 'ai': {
      let size: number
      switch (row.segment?.type) {
        case 'content':
          size = Math.max(
            CONTENT_MIN,
            estimateTextHeight(
              row.segment.content,
              unitsPerLine(prefs?.containerWidth, 15, CONTENT_UNITS_PER_LINE),
              CONTENT_LINE_HEIGHT
            ) *
              CONTENT_MARKDOWN_FACTOR +
              CONTENT_CHROME
          )
          break
        case 'thinking':
          size =
            prefs?.showThinking === false
              ? 90
              : THINKING_CHROME +
                Math.min(
                  THINKING_BODY_MAX,
                  estimateTextHeight(
                    row.segment.thinking,
                    unitsPerLine(prefs?.containerWidth, 13, THINKING_UNITS_PER_LINE),
                    THINKING_LINE_HEIGHT
                  ) + THINKING_BODY_PADDING
                )
          break
        case 'tool': {
          // 默认折叠为状态行（业界模式：工具过程是背景细节，点击展开回看）。
          // 展开真实上限 ~455px（头部 40 + args 150 + 结果 300 等封顶组合，各部件
          // 均有 max-height）；折叠态按是否有单行摘要行区分：有（结果摘要/子Agent
          // 实时输出单行）= 头部 40 + 摘要行 ~28 + 边框/边距 ~17 ≈ 85；无（纯 JSON/
          // 运行中无实时输出，摘要行 display:none）= 头部 40 + 边框/边距 ~14 ≈ 56。
          // 手动展开只发生在已挂载行（必有实测缓存），455 仅作手动展开行首帧占位
          const override = getBlockExpandOverride(row.key)
          size = override ? 455 : row.toolHasSummary === false ? 56 : 85
          break
        }
        case 'todo': {
          // n=0 时段数据尚未到达（模板有 segment.todo 守卫），沿用旧粗估
          const todos = row.segment.todo ?? []
          if (todos.length === 0) {
            size = 280
            break
          }
          // 逐项按内容测高：.todo-content 允许 break-word 折行（无单行省略），
          // 长项非固定单行，在 31px 单行基准上累加折行额外高度；
          // 整体仍封顶 320（渲染层 max-height，超长内部滚动），估值与 CSS 一致
          const itemUnits = unitsPerLine(prefs?.containerWidth, 13, TODO_UNITS_PER_LINE)
          const itemsHeight = todos.reduce(
            (sum, item) =>
              sum +
              TODO_ITEM_HEIGHT +
              Math.max(
                0,
                estimateTextHeight(item.content, itemUnits, TODO_LINE_HEIGHT) - TODO_LINE_HEIGHT
              ),
            0
          )
          const body = itemsHeight + (todos.length - 1) * TODO_ITEM_GAP
          size = TODO_CHROME + Math.min(TODO_BODY_MAX, body)
          break
        }
        default:
          size = 120
      }
      if (row.part === 'first') size += 44
      if (row.part === 'last' || row.part === 'single') size += 48
      return size
    }
  }
}
