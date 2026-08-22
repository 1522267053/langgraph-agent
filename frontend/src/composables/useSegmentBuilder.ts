/**
 * Segment 构建器
 * @description 封装消息分段的追加和更新逻辑，供 Agent 聊天和 Flow 执行共用
 *
 * Agent 流式传入全量累积内容（需前缀匹配替换），
 * Flow 流式传入增量 chunk（需拼接到最后分段），
 * 因此 thinking/content 各提供两个语义明确的函数。
 */

import type { Segment, TodoItem } from '@/types/segment'
import type { KnowledgeReference } from '@/types/knowledge'
import type { ExecutionStep } from '@/types/execution'

let segmentIdCounter = 0

export function genSegmentId(): string {
  segmentIdCounter += 1
  return `seg-${segmentIdCounter}`
}

// ---- Flow 端使用：增量 chunk 拼接 ----

/**
 * 拼接 thinking chunk
 * 如果最后一个分段是 thinking 类型，则追加；否则创建新分段
 */
export function appendThinking(segments: Segment[], content: string): Segment[] {
  const last = segments[segments.length - 1]
  if (last?.type === 'thinking') {
    last.thinking = (last.thinking || '') + content
    return segments
  }
  return [...segments, { type: 'thinking', thinking: content, id: genSegmentId() }]
}

/**
 * 拼接 content chunk
 * 如果最后一个分段是 content 类型，则追加；否则创建新分段
 */
export function appendContent(segments: Segment[], content: string): Segment[] {
  const last = segments[segments.length - 1]
  if (last?.type === 'content') {
    last.content = (last.content || '') + content
    return segments
  }
  return [...segments, { type: 'content', content, id: genSegmentId() }]
}

// ---- Agent 端使用：全量累积内容替换 ----

/**
 * 更新 thinking 全量内容
 * 查找已有 thinking 分段（前缀匹配），替换内容；未找到则追加
 */
export function updateThinking(segments: Segment[], content: string): Segment[] {
  const idx = segments.findLastIndex(
    s => s.type === 'thinking' && (s.thinking === content || content.startsWith(s.thinking || ''))
  )
  if (idx !== -1) {
    const updated = [...segments]
    updated[idx] = { ...updated[idx], thinking: content }
    return updated
  }
  return [...segments, { type: 'thinking', thinking: content, id: genSegmentId() }]
}

/**
 * 更新 content 全量内容
 * 查找已有 content 分段（前缀匹配），替换内容；未找到则追加
 */
export function updateContent(segments: Segment[], content: string): Segment[] {
  const idx = segments.findLastIndex(
    s => s.type === 'content' && (s.content === content || content.startsWith(s.content || ''))
  )
  if (idx !== -1) {
    const updated = [...segments]
    updated[idx] = { ...updated[idx], content }
    return updated
  }
  return [...segments, { type: 'content', content, id: genSegmentId() }]
}

// ---- 通用：tool / todo ----

/**
 * 添加工具调用分段
 */
export function addTool(
  segments: Segment[],
  name: string,
  args?: Record<string, unknown>,
  status: 'running' | 'success' | 'error' = 'running',
  result?: unknown,
  toolCallId?: string
): Segment[] {
  return [
    ...segments,
    { type: 'tool', tool: { name, args, status, result, id: toolCallId }, id: genSegmentId() }
  ]
}

/**
 * 更新工具调用结果
 * 优先按 toolCallId 精确匹配（同名工具并行调用时结果不会错位）；
 * 未提供 ID 或 ID 未命中时回退按名称匹配最后一个 running 的工具分段
 */
export function updateTool(
  segments: Segment[],
  name: string,
  status: 'running' | 'success' | 'error',
  result?: unknown,
  toolCallId?: string
): Segment[] {
  let idx = -1
  if (toolCallId) {
    // 按 ID 精确匹配；命中但已结束（重复 end 事件）时直接忽略，防止误改同名工具
    const idIdx = segments.findLastIndex(s => s.type === 'tool' && s.tool?.id === toolCallId)
    if (idIdx !== -1) {
      if (segments[idIdx].tool?.status !== 'running') return segments
      idx = idIdx
    }
  }
  if (idx === -1) {
    idx = segments.findLastIndex(
      s => s.type === 'tool' && s.tool?.name === name && s.tool?.status === 'running'
    )
  }
  if (idx === -1) return segments
  const seg = segments[idx]
  const updated: Segment = {
    ...seg,
    tool: {
      ...seg.tool!,
      status,
      ...(result !== undefined ? { result } : {})
    }
  }
  return [...segments.slice(0, idx), updated, ...segments.slice(idx + 1)]
}

/**
 * 添加任务计划分段
 */
export function addTodo(segments: Segment[], todos: TodoItem[]): Segment[] {
  return [...segments, { type: 'todo', todo: [...todos], id: genSegmentId() }]
}

/** Attach validated knowledge citations to the latest content segment. */
export function attachKnowledgeCitations(
  segments: Segment[],
  citations: KnowledgeReference[]
): Segment[] {
  const idx = segments.findLastIndex(segment => segment.type === 'content')
  if (idx === -1 || citations.length === 0) return segments

  const seen = new Set<string>()
  const merged = [...(segments[idx].knowledge_citations || []), ...citations].filter(reference => {
    if (seen.has(reference.reference_id)) return false
    seen.add(reference.reference_id)
    return true
  })
  const updated = [...segments]
  updated[idx] = { ...updated[idx], knowledge_citations: merged }
  return updated
}

/** 将持久化的 Flow 执行步骤还原为消息分段。 */
export function executionStepsToSegments(steps: ExecutionStep[]): Segment[] {
  const segments: Segment[] = []
  const toolCallMap = new Map<string, number>()

  for (const step of steps) {
    if (step.role === 'human') continue

    if (step.role === 'tool') {
      const index = toolCallMap.get(step.tool_call_id || '')
      if (index !== undefined && segments[index]?.tool) {
        segments[index].tool.result = step.content || ''
      }
      continue
    }

    if (step.thinking) {
      segments.push({ type: 'thinking', thinking: step.thinking })
    }
    if (step.content || step.knowledge_citations?.length) {
      segments.push({
        type: 'content',
        content: step.content,
        knowledge_citations: step.knowledge_citations
      })
    }
    for (const tool of step.tool_calls || []) {
      const segmentIndex = segments.length
      segments.push({
        type: 'tool',
        tool: {
          name: tool.name,
          args: tool.args || {},
          status: tool.status || 'running',
          result: tool.result,
          id: tool.id
        }
      })
      if (tool.id) toolCallMap.set(tool.id, segmentIndex)
    }
  }

  return segments
}
