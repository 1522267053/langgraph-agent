/**
 * 聊天虚拟行模型
 * @description 将 chatMessages 拍平为段级虚拟行：AI 回合的每个 segment 独占一行，
 * 消息级 UI（头像/头部/尾部）拆分到 first/last 行，消除超长回合的单行巨高问题
 */

import type { StreamingMessage } from '@/composables/useStreamingMessage'
import type { Segment } from '@/types/segment'
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
  /** 是否为列表最后一条消息（流式指示器定位） */
  isLast: boolean
  /** 流式进行中时，属于最后一轮工具调用的 tool 段行自动展开（默认展开态的行级判定） */
  isLatestTool?: boolean
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
 * 将消息列表拍平为虚拟行
 * @param showStandaloneTyping 流式中但最后一条不是 AI 消息时，追加独立输入指示器行
 * @param isStreaming 流式进行中时标记最后一轮工具调用的 tool 段行为默认展开
 */
export function buildChatRows(
  chatMessages: StreamingMessage[],
  showStandaloneTyping: boolean,
  isStreaming: boolean
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
        isLast: isLastMsg
      })
    })
  })

  if (showStandaloneTyping) {
    rows.push({ key: 'typing', kind: 'typing', part: 'single', msg: null, isLast: true })
  }
  if (isStreaming) {
    // 最后一轮工具调用默认展开：一轮 LLM 响应可并行发起多个工具，
    // 对应最后一个非 tool 的 AI 段之后的连续 tool 段
    const lastNonToolIdx = rows.findLastIndex(
      row => row.kind === 'ai' && row.segment?.type !== 'tool'
    )
    rows.forEach((row, i) => {
      if (i > lastNonToolIdx && row.kind === 'ai' && row.segment?.type === 'tool') {
        row.isLatestTool = true
      }
    })
  }
  return rows
}

/** 展示开关状态：影响行高估算（关闭时思考段只剩头部） */
export interface RowSizePrefs {
  showThinking?: boolean
}

/** 行高初值：优先取实测缓存；无缓存时按段类型估值，头部/尾部行附加消息 chrome
 * 高度，减少测量收敛迭代 */
export function estimateRowSize(row: ChatRow | undefined, prefs?: RowSizePrefs): number {
  if (!row) return 150
  // 实测值已含 chrome，直接返回，不再走类型估值与 chrome 加成
  const measured = measuredSizes.get(row.key)
  if (measured) return measured
  switch (row.kind) {
    case 'typing':
      return 56
    case 'summary':
      return 140
    case 'human':
      return 90
    case 'ai': {
      let size: number
      switch (row.segment?.type) {
        case 'content':
          size = 220
          break
        case 'thinking':
          size = prefs?.showThinking === false ? 90 : 160
          break
        case 'tool': {
          // 展开态判定与渲染层一致：手动操作覆盖 > 流式最后一轮工具默认展开；
          // 估值按实测校准（折叠头部约 50px，展开含入参约 150px）
          const override = getBlockExpandOverride(row.key)
          size = (override ?? row.isLatestTool === true) ? 150 : 50
          break
        }
        case 'todo':
          size = 280
          break
        default:
          size = 120
      }
      if (row.part === 'first') size += 44
      if (row.part === 'last' || row.part === 'single') size += 48
      return size
    }
  }
}
