/**
 * 条件操作符配置
 * @description 定义流程条件判断支持的操作符
 */

/** 操作符选项 */
export interface OperatorOption {
  /** 操作符值 */
  value: string
  /** 显示标签 */
  label: string
  /** 操作符类型 */
  type?: 'comparison' | 'string' | 'existence'
}

/** 条件操作符列表 */
export const CONDITION_OPERATORS: OperatorOption[] = [
  // 比较操作符
  { value: '==', label: '等于', type: 'comparison' },
  { value: '!=', label: '不等于', type: 'comparison' },
  { value: '>', label: '大于', type: 'comparison' },
  { value: '>=', label: '大于等于', type: 'comparison' },
  { value: '<', label: '小于', type: 'comparison' },
  { value: '<=', label: '小于等于', type: 'comparison' },
  // 字符串操作符
  { value: 'contains', label: '包含', type: 'string' },
  { value: 'not_contains', label: '不包含', type: 'string' },
  { value: 'starts_with', label: '开头是', type: 'string' },
  { value: 'ends_with', label: '结尾是', type: 'string' },
  // 存在性操作符
  { value: 'is_empty', label: '为空', type: 'existence' },
  { value: 'is_not_empty', label: '不为空', type: 'existence' }
]

/** 逻辑关系选项 */
export const LOGIC_OPERATORS = [
  { value: 'and', label: '且（所有条件都满足）' },
  { value: 'or', label: '或（任一条件满足）' }
] as const

/** 默认条件配置 */
export const DEFAULT_CONDITION_CONFIG = {
  /** 默认逻辑关系 */
  logic: 'and' as const
}

/**
 * 获取操作符标签
 * @param operator 操作符值
 * @returns 操作符标签
 */
export function getOperatorLabel(operator: string): string {
  const found = CONDITION_OPERATORS.find(op => op.value === operator)
  return found?.label || operator
}

/**
 * 判断操作符是否需要比较值
 * @param operator 操作符值
 * @returns 是否需要比较值
 */
export function operatorNeedsValue(operator: string): boolean {
  const noValueOperators = ['is_empty', 'is_not_empty']
  return !noValueOperators.includes(operator)
}

/**
 * 获取指定类型的操作符列表
 * @param type 操作符类型
 * @returns 操作符列表
 */
export function getOperatorsByType(type: OperatorOption['type']): OperatorOption[] {
  return CONDITION_OPERATORS.filter(op => op.type === type)
}
