/**
 * 状态枚举配置
 * @description 定义各类状态枚举和状态文本映射
 */

import { ExecutionStatus, NodeExecutionStatus } from '@/types/execution'

/** 流程状态 */
export enum FlowStatusValue {
  /** 草稿 */
  Draft = 0,
  /** 已发布 */
  Published = 1
}

/** 流程状态选项 */
export const FLOW_STATUS_OPTIONS = [
  { value: FlowStatusValue.Draft, label: '草稿' },
  { value: FlowStatusValue.Published, label: '已发布' }
] as const

/** 流程状态文本映射 */
export const FLOW_STATUS_TEXT: Record<FlowStatusValue, string> = {
  [FlowStatusValue.Draft]: '草稿',
  [FlowStatusValue.Published]: '已发布'
}

/** 执行状态文本映射（从execution.ts导入） */
export { EXECUTION_STATUS_TEXT, NODE_EXECUTION_STATUS_TEXT } from '@/types/execution'

/** 执行状态选项 */
export const EXECUTION_STATUS_OPTIONS = [
  { value: ExecutionStatus.Pending, label: '待执行', type: 'info' },
  { value: ExecutionStatus.Running, label: '执行中', type: 'warning' },
  { value: ExecutionStatus.Success, label: '成功', type: 'success' },
  { value: ExecutionStatus.Failed, label: '失败', type: 'danger' },
  { value: ExecutionStatus.Cancelled, label: '已取消', type: 'info' },
  { value: ExecutionStatus.WaitingInput, label: '等待输入', type: 'warning' }
] as const

/** 节点执行状态选项 */
export const NODE_EXECUTION_STATUS_OPTIONS = [
  { value: NodeExecutionStatus.Pending, label: '待执行', type: 'info' },
  { value: NodeExecutionStatus.Running, label: '执行中', type: 'warning' },
  { value: NodeExecutionStatus.Success, label: '成功', type: 'success' },
  { value: NodeExecutionStatus.Failed, label: '失败', type: 'danger' },
  { value: NodeExecutionStatus.Skipped, label: '跳过', type: 'info' }
] as const

/** 通用启用状态 */
export const ENABLED_STATUS_OPTIONS = [
  { value: 0, label: '禁用', type: 'danger' },
  { value: 1, label: '启用', type: 'success' }
] as const

/**
 * 获取流程状态标签
 * @param status 状态值
 * @returns 状态标签
 */
export function getFlowStatusLabel(status: FlowStatusValue): string {
  return FLOW_STATUS_TEXT[status] || '未知'
}

/**
 * 获取执行状态标签
 * @param status 状态值
 * @returns 状态标签
 */
export function getExecutionStatusLabel(status: ExecutionStatus): string {
  const found = EXECUTION_STATUS_OPTIONS.find(o => o.value === status)
  return found?.label || '未知'
}

/**
 * 获取执行状态类型（用于标签颜色）
 * @param status 状态值
 * @returns 状态类型
 */
export function getExecutionStatusType(
  status: ExecutionStatus
): 'info' | 'warning' | 'success' | 'danger' {
  const found = EXECUTION_STATUS_OPTIONS.find(o => o.value === status)
  return found?.type || 'info'
}

/**
 * 获取节点执行状态标签
 * @param status 状态值
 * @returns 状态标签
 */
export function getNodeExecutionStatusLabel(status: NodeExecutionStatus): string {
  const found = NODE_EXECUTION_STATUS_OPTIONS.find(o => o.value === status)
  return found?.label || '未知'
}

/**
 * 获取节点执行状态类型
 * @param status 状态值
 * @returns 状态类型
 */
export function getNodeExecutionStatusType(
  status: NodeExecutionStatus
): 'info' | 'warning' | 'success' | 'danger' {
  const found = NODE_EXECUTION_STATUS_OPTIONS.find(o => o.value === status)
  return found?.type || 'info'
}

/**
 * 判断执行是否已完成（无论成功失败）
 * @param status 状态值
 * @returns 是否已完成
 */
export function isExecutionFinished(status: ExecutionStatus): boolean {
  return [ExecutionStatus.Success, ExecutionStatus.Failed, ExecutionStatus.Cancelled].includes(
    status
  )
}

/**
 * 判断执行是否可取消
 * @param status 状态值
 * @returns 是否可取消
 */
export function isExecutionCancellable(status: ExecutionStatus): boolean {
  return [ExecutionStatus.Pending, ExecutionStatus.Running].includes(status)
}
