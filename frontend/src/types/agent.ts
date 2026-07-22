/**
 * Agent相关类型定义
 * @description 定义Agent会话、消息等相关类型
 */

import type { BaseEntity, ListResponse } from './common'
import type { FlowIOSchema } from './flow'

/** Agent流程实体 */
export interface AgentFlow {
  /** 主键ID */
  id: number
  /** 流程名称 */
  name: string
  /** 流程描述 */
  description?: string
  /** 流程类型 */
  flow_type: string
  /** 流程状态 */
  status: number
  /** 输入参数定义 */
  input_schema?: FlowIOSchema
  /** 建议提示词列表 */
  suggested_prompts?: string[]
  /** 创建时间 */
  created_at?: string
  /** 更新时间 */
  updated_at?: string
}

/** Agent会话实体 */
export interface AgentSession extends BaseEntity {
  /** 主键ID */
  id: number
  /** 关联的流程ID */
  flow_id: number
  /** 会话标题 */
  title: string
  /** 会话状态 */
  status: number
  /** 创建时间 */
  created_at?: string
  /** 更新时间 */
  updated_at?: string
}

/** Agent消息实体 */
export interface AgentMessage extends BaseEntity {
  /** 主键ID */
  id: number
  /** 所属会话ID */
  session_id: number
  /** 消息角色（user/assistant/system/tool） */
  role: string
  /** 消息内容 */
  content: string
  /** 原始用户消息（未渲染模板，仅 agent 模式 human 消息有值） */
  original_content?: string
  /** 思考过程 */
  thinking?: string
  /** 工具调用信息 */
  tool_calls?: Record<string, unknown>
  /** 工具调用ID */
  tool_call_id?: string
  /** 工具执行状态 */
  status?: string
  /** 消息序号 */
  sequence: number
  /** 输入token数 */
  prompt_tokens?: number
  /** 最后一次LLM调用的输入token数（非累加） */
  latest_prompt_tokens?: number
  /** 输出token数 */
  completion_tokens?: number
  /** 总token数 */
  total_tokens?: number
  /** 附件文件列表 */
  files?: Array<{ id: number; original_name: string; mime_type: string }>
  /** 创建时间 */
  created_at?: string
}

/** Agent聊天请求 */
export interface AgentChatRequest {
  content: string
  params?: Record<string, unknown>
}

/** Agent恢复请求（人工交互后继续） */
export interface AgentResumeRequest {
  /** 人工输入内容 */
  human_input: string
}

/** Agent会话列表响应 */
export type AgentSessionListResponse = ListResponse<AgentSession>

/** Agent消息列表响应 */
export type AgentMessageListResponse = ListResponse<AgentMessage>

/** Agent流程列表响应 */
export type AgentFlowListResponse = ListResponse<AgentFlow>
