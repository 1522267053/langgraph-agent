/**
 * 通用基础类型定义
 * @description 定义项目中通用的基础类型，所有API响应和实体都基于这些类型
 */

/** API 通用响应结构 */
export interface ApiResponse<T = unknown> {
  /** 响应码：1=成功，0=失败 */
  code: number
  /** 响应消息 */
  msg: string
  /** 响应数据 */
  data: T
}

/** 分页响应结构 */
export interface PaginatedResponse<T> {
  /** 总记录数 */
  total: number
  /** 当前页码 */
  page: number
  /** 每页记录数 */
  page_size: number
  /** 总页数 */
  total_pages: number
  /** 数据列表 */
  items: T[]
}

/** 分页查询参数 */
export interface PaginationParams<T = unknown> {
  /** 当前页码 */
  page: number
  /** 每页记录数 */
  page_size: number
  /** 排序字段 */
  order_by?: string
  /** 是否升序 */
  is_asc?: boolean
  /** 查询条件 */
  condition?: T
}

/** 基础实体接口
 * @description 所有数据库实体的基础接口，包含通用字段
 */
export interface BaseEntity {
  /** 主键ID */
  id?: number
  /** 创建者ID */
  creator_id?: number
  /** 创建者类型 */
  creator_type?: number
  /** 创建者名称 */
  creator_name?: string
  /** 创建时间 */
  create_time?: string
  /** 修改者ID */
  modifier_id?: number
  /** 修改者类型 */
  modifier_type?: number
  /** 修改者名称 */
  modifier_name?: string
  /** 修改时间 */
  modify_time?: string
}

/** 通用列表响应结构（兼容旧API） */
export interface ListResponse<T> {
  /** 总记录数 */
  total: number
  /** 数据列表 */
  list: T[]
}

/** 通用状态枚举 */
export enum CommonStatus {
  /** 禁用 */
  Disabled = 0,
  /** 启用 */
  Enabled = 1
}

/** 通用布尔值枚举（数据库存储用） */
export enum BooleanValue {
  False = 0,
  True = 1
}
