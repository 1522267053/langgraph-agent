/**
 * Agent相关类型定义
 * @description 定义Agent会话、消息等相关类型
 */

import type { BaseEntity, ListResponse } from './common'
import type { FlowIOSchema } from './flow'
import type { KnowledgeReference } from './knowledge'

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
  /** 项目工作路径（空则使用 Agent 默认工作目录） */
  work_dir?: string | null
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
  /** 内部消息类型，如 context_summary */
  message_type?: string
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
  /** 工具消息携带的知识库候选来源 */
  knowledge_references?: KnowledgeReference[]
  /** AI 消息实际引用的知识库来源 */
  knowledge_citations?: KnowledgeReference[]
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
  files?: AgentFileInfo[]
  /** 用户输入参数（回退恢复用） */
  input_data?: Record<string, unknown>
  /** 结束节点输出（该轮 AI 消息携带，前端按钮查看） */
  end_output?: Record<string, unknown>
  /** 创建时间 */
  created_at?: string
}

/** Agent消息附件信息 */
export interface AgentFileInfo {
  id: number
  original_name: string
  mime_type: string
  file_path?: string
  file_type?: string
  file_size?: number
  preview_url?: string
}

/** 删除消息及之后内容的返回结果（回退恢复用） */
export interface AgentDeleteMessagesResult {
  /** 被删除用户消息的文本内容 */
  content: string
  /** 附件文件列表 */
  files?: AgentFileInfo[]
  /** 用户输入参数 */
  input_data?: Record<string, unknown>
}

/** Agent聊天请求 */
export interface AgentChatRequest {
  content: string
  params?: Record<string, unknown>
  /** 临时覆盖 LLM 模型（仅同供应商内切换，capabilities 等由后端按模型元数据联动） */
  model?: string
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
