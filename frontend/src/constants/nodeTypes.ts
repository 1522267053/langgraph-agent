/**
 * 节点类型常量 — 从 nodeRegistry 派生
 * @description 节点元数据的唯一数据源是 nodeRegistry.ts
 */

import type { AllNodeType, FieldType } from '@/types/flow'
import { getAllRegisteredTypes, getNodeEntry } from '@/components/FlowEditor/nodeRegistry'

/** 节点类型选项（向后兼容 flowTransform.ts） */
export interface NodeTypeOption {
  value: AllNodeType
  label: string
  icon?: string
  description?: string
  category: 'basic' | 'llm' | 'tool' | 'io'
  defaultConfig?: Record<string, unknown>
}

/** 所有节点类型（从注册表派生） */
export const ALL_NODE_TYPES: NodeTypeOption[] = getAllRegisteredTypes().map(type => {
  const entry = getNodeEntry(type)!
  return {
    value: type as AllNodeType,
    label: entry.label,
    description: entry.description,
    category: entry.category,
    defaultConfig: entry.defaultConfig()
  }
})

/** 字段类型选项 */
export const FIELD_TYPE_OPTIONS: { value: FieldType; label: string }[] = [
  { value: 'string', label: '字符串' },
  { value: 'number', label: '数字' },
  { value: 'boolean', label: '布尔值' },
  { value: 'object', label: '对象' },
  { value: 'array', label: '数组' }
]

/** 根据节点类型获取配置 */
export function getNodeTypeConfig(nodeType: AllNodeType): NodeTypeOption | undefined {
  return ALL_NODE_TYPES.find(n => n.value === nodeType)
}

/** 获取节点类型标签 */
export function getNodeTypeLabel(nodeType: AllNodeType): string {
  const config = getNodeTypeConfig(nodeType)
  return config?.label || nodeType
}

/** 根据分类获取节点类型列表 */
export function getNodeTypesByCategory(category: NodeTypeOption['category']): NodeTypeOption[] {
  return ALL_NODE_TYPES.filter(n => n.category === category)
}

/** 获取字段类型标签 */
export function getFieldTypeLabel(fieldType: FieldType): string {
  const found = FIELD_TYPE_OPTIONS.find(f => f.value === fieldType)
  return found?.label || fieldType
}
