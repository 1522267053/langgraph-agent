/**
 * 聊天虚拟行模型
 * @description 将 chatMessages 拍平为段级虚拟行：AI 回合的每个 segment 独占一行，
 * 消息级 UI（头像/头部/尾部）拆分到 first/last 行，消除超长回合的单行巨高问题
 */

import type { StreamingMessage } from '@/composables/useStreamingMessage'
import type { Segment } from '@/types/segment'

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
}

/** 段级行 key：流式段用 genSegmentId，历史段用按 DB 行生成的确定性 id 兜底 */
export function getSegmentRowKey(msg: StreamingMessage, segment: Segment, idx: number): string {
  return `row-${segment.id || `s-${msg.id}-idx${idx}`}`
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
        isLast: isLastMsg
      })
    })
  })

  if (showStandaloneTyping) {
    rows.push({ key: 'typing', kind: 'typing', part: 'single', msg: null, isLast: true })
  }
  return rows
}

/** 行高初值：按段类型估值，头部/尾部行附加消息 chrome 高度，减少测量收敛迭代 */
export function estimateRowSize(row: ChatRow | undefined): number {
  if (!row) return 150
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
          size = 160
          break
        case 'tool':
          size = 110
          break
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
