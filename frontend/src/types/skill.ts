/**
 * Skill 技能卡片类型定义
 * @description 定义技能卡片的实体和操作相关类型
 */

import type { BaseEntity } from './common'

/** 技能实体 */
export interface Skill extends BaseEntity {
  /** 主键ID */
  id: number
  /** 技能名称 */
  name: string
  /** 技能描述 */
  description: string
  /** 技能路径 */
  skill_path?: string
  /** 分类 */
  category?: string
  /** 标签（逗号分隔） */
  tags?: string
  /** 图标 */
  icon?: string
  /** 是否启用 */
  is_enabled: number
  /** 是否系统内置 */
  is_system: number
  /** 排序顺序 */
  sort_order: number
  /** 创建者ID */
  creator_id?: number
  /** 创建者名称 */
  creator_name?: string
  /** 创建时间 */
  create_time?: string
  /** 修改者ID */
  modifier_id?: number
  /** 修改者名称 */
  modifier_name?: string
  /** 修改时间 */
  modify_time?: string
}

/** 更新技能参数 */
export interface SkillUpdate {
  /** 主键ID */
  id: number
  /** 分类 */
  category?: string
  /** 标签 */
  tags?: string
  /** 图标 */
  icon?: string
  /** 是否启用 */
  is_enabled?: number
  /** 排序顺序 */
  sort_order?: number
}

/** 技能查询条件 */
export interface SkillQuery {
  /** 名称（模糊搜索） */
  name?: string
  /** 分类 */
  category?: string
  /** 是否启用 */
  is_enabled?: number
}

/** 批量操作结果 */
export interface SkillBatchResult {
  success_count: number
  failed_count: number
  failed_items: { id: number; reason: string }[]
  skills: Skill[]
}
