/**
 * 节点类型配置
 * @description 定义流程编辑器支持的节点类型及其属性
 */

import type { AllNodeType, FieldType } from '@/types/flow'

/** 节点类型选项 */
export interface NodeTypeOption {
  /** 节点类型值 */
  value: AllNodeType
  /** 显示标签 */
  label: string
  /** 节点图标 */
  icon?: string
  /** 节点描述 */
  description?: string
  /** 节点分类 */
  category: 'basic' | 'llm' | 'tool' | 'io'
  /** 默认配置 */
  defaultConfig?: Record<string, unknown>
}

/** 基础节点类型配置 */
export const BASIC_NODE_TYPES: NodeTypeOption[] = [
  {
    value: 'start',
    label: '开始节点',
    icon: 'VideoPlay',
    description: '流程的起始点，定义输入参数',
    category: 'basic'
  },
  {
    value: 'end',
    label: '结束节点',
    icon: 'VideoPause',
    description: '流程的结束点，定义输出结果',
    category: 'basic'
  },
  {
    value: 'condition',
    label: '条件分支',
    icon: 'Share',
    description: '根据条件判断执行不同的分支',
    category: 'basic'
  },
  {
    value: 'loop',
    label: '循环',
    icon: 'RefreshRight',
    description: '循环执行子流程，支持固定次数、条件和数组遍历',
    category: 'basic'
  },
  {
    value: 'intent_router',
    label: '意图路由',
    icon: 'Aim',
    description: '使用规则（关键字/正则）或 LLM 把输入分类到不同分支',
    category: 'basic',
    defaultConfig: {
      enable_rule_layer: true,
      enable_llm_layer: true,
      case_sensitive: false,
      temperature: 0.1,
      max_tokens: 200,
      confidence_threshold: 0.6,
      input_variable: 'input.question',
      intents: []
    }
  }
]

/** LLM节点类型配置 */
export const LLM_NODE_TYPES: NodeTypeOption[] = [
  {
    value: 'llm',
    label: 'LLM节点',
    icon: 'ChatDotRound',
    description: '调用大语言模型进行推理',
    category: 'llm',
    defaultConfig: {
      provider: 'deepseek',
      model: 'deepseek-chat',
      maxToolIterations: 5,
      historyMode: 'node',
      maxHistoryTurns: 10
    }
  }
]

/** 工具节点类型配置 */
export const TOOL_NODE_TYPES: NodeTypeOption[] = [
  {
    value: 'mcp',
    label: 'MCP工具',
    icon: 'Connection',
    description: '调用MCP服务器提供的工具',
    category: 'tool'
  },
  {
    value: 'api',
    label: 'API调用',
    icon: 'Link',
    description: '调用外部API接口',
    category: 'tool',
    defaultConfig: {
      method: 'GET'
    }
  },
  {
    value: 'skill',
    label: '技能卡片',
    icon: 'MagicStick',
    description: '调用预定义的技能',
    category: 'tool'
  },
  {
    value: 'knowledge',
    label: '知识库',
    icon: 'Collection',
    description: '从知识库检索相关信息',
    category: 'tool',
    defaultConfig: {
      topK: 5
    }
  },
  {
    value: 'python',
    label: 'Python代码',
    icon: 'Cpu',
    description: '在沙箱环境中执行Python代码',
    category: 'tool',
    defaultConfig: {
      timeout: 30
    }
  },
  {
    value: 'shell',
    label: 'Shell命令',
    icon: 'Monitor',
    description: '在受限环境中执行Shell命令',
    category: 'tool',
    defaultConfig: {
      timeout: 300
    }
  },
  {
    value: 'memory',
    label: '记忆',
    icon: 'Notebook',
    description: '为Agent提供记忆保存与检索能力',
    category: 'tool',
    defaultConfig: {
      max_results: 5,
      default_importance: 3,
      default_category: 'event',
      max_index_lines: 200,
      max_index_bytes: 25000,
      auto_promote_threshold: 5,
      consolidate_threshold: 50,
      hot_decay_days: 30,
      warm_decay_days: 60,
      consolidate_interval_days: 7
    }
  },
  {
    value: 'todo',
    label: '任务计划',
    icon: 'List',
    description: '为Agent提供任务规划与进度跟踪能力',
    category: 'tool'
  },
  {
    value: 'media_gen',
    label: '媒体生成',
    icon: 'PictureFilled',
    description: '生成图片、音频、视频',
    category: 'tool',
    defaultConfig: {
      media_type: 'image',
      image: {
        enabled: true,
        provider: 'openai_compatible',
        model: 'dall-e-3',
        api_key: '',
        base_url: '',
        params: {}
      },
      audio: {
        enabled: false,
        provider: 'openai_compatible',
        model: 'tts-1',
        api_key: '',
        base_url: '',
        params: {}
      },
      video: {
        enabled: false,
        provider: 'minimax',
        model: 'video-01',
        api_key: '',
        base_url: '',
        params: {}
      }
    }
  },
  {
    value: 'sub_agent',
    label: '子Agent',
    icon: 'Avatar',
    description: '调用已发布的Agent作为子任务执行器',
    category: 'tool',
    defaultConfig: { agent_id: null }
  },
  {
    value: 'agenda',
    label: '日程',
    icon: 'Calendar',
    description: '为Agent提供日程管理能力（创建、查询、更新、删除日程）',
    category: 'tool'
  }
]

/** 交互节点类型配置 */
export const IO_NODE_TYPES: NodeTypeOption[] = [
  {
    value: 'human',
    label: '人工交互',
    icon: 'User',
    description: '等待人工输入或审核',
    category: 'io',
    defaultConfig: {
      timeout: 600
    }
  },
  {
    value: 'card',
    label: '流程卡片',
    icon: 'Postcard',
    description: '引用其他流程作为子流程',
    category: 'io'
  }
]

/** 所有节点类型（合并） */
export const ALL_NODE_TYPES: NodeTypeOption[] = [
  ...BASIC_NODE_TYPES,
  ...LLM_NODE_TYPES,
  ...TOOL_NODE_TYPES,
  ...IO_NODE_TYPES
]

/** 字段类型选项 */
export const FIELD_TYPE_OPTIONS: { value: FieldType; label: string }[] = [
  { value: 'string', label: '字符串' },
  { value: 'number', label: '数字' },
  { value: 'boolean', label: '布尔值' },
  { value: 'object', label: '对象' },
  { value: 'array', label: '数组' }
]

/**
 * 根据节点类型获取配置
 * @param nodeType 节点类型
 * @returns 节点类型配置
 */
export function getNodeTypeConfig(nodeType: AllNodeType): NodeTypeOption | undefined {
  return ALL_NODE_TYPES.find(n => n.value === nodeType)
}

/**
 * 获取节点类型标签
 * @param nodeType 节点类型
 * @returns 节点类型标签
 */
export function getNodeTypeLabel(nodeType: AllNodeType): string {
  const config = getNodeTypeConfig(nodeType)
  return config?.label || nodeType
}

/**
 * 根据分类获取节点类型列表
 * @param category 节点分类
 * @returns 节点类型列表
 */
export function getNodeTypesByCategory(category: NodeTypeOption['category']): NodeTypeOption[] {
  switch (category) {
    case 'basic':
      return BASIC_NODE_TYPES
    case 'llm':
      return LLM_NODE_TYPES
    case 'tool':
      return TOOL_NODE_TYPES
    case 'io':
      return IO_NODE_TYPES
    default:
      return []
  }
}

/**
 * 获取字段类型标签
 * @param fieldType 字段类型
 * @returns 字段类型标签
 */
export function getFieldTypeLabel(fieldType: FieldType): string {
  const found = FIELD_TYPE_OPTIONS.find(f => f.value === fieldType)
  return found?.label || fieldType
}
