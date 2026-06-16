import { markRaw } from 'vue'
import StartNode from './StartNode.vue'
import EndNode from './EndNode.vue'
import ConditionNode from './ConditionNode.vue'
import CardNode from './CardNode.vue'
import LoopNode from './LoopNode.vue'
import LlmNode from './LlmNode.vue'
import McpNode from './McpNode.vue'
import KnowledgeNode from './KnowledgeNode.vue'
import HumanNode from './HumanNode.vue'
import ApiNode from './ApiNode.vue'
import SkillNode from './SkillNode.vue'
import PythonNode from './PythonNode.vue'
import ShellNode from './ShellNode.vue'
import MemoryNode from './MemoryNode.vue'
import TodoNode from './TodoNode.vue'
import MediaGenNode from './MediaGenNode.vue'
import IntentRouterNode from './IntentRouterNode.vue'
import SubAgentNode from './SubAgentNode.vue'
import AgendaNode from './AgendaNode.vue'

export const nodeTypes = {
  start: markRaw(StartNode),
  end: markRaw(EndNode),
  condition: markRaw(ConditionNode),
  card: markRaw(CardNode),
  loop: markRaw(LoopNode),
  llm: markRaw(LlmNode),
  mcp: markRaw(McpNode),
  knowledge: markRaw(KnowledgeNode),
  human: markRaw(HumanNode),
  api: markRaw(ApiNode),
  skill: markRaw(SkillNode),
  python: markRaw(PythonNode),
  shell: markRaw(ShellNode),
  memory: markRaw(MemoryNode),
  todo: markRaw(TodoNode),
  media_gen: markRaw(MediaGenNode),
  intent_router: markRaw(IntentRouterNode),
  sub_agent: markRaw(SubAgentNode),
  agenda: markRaw(AgendaNode)
}

export {
  StartNode,
  EndNode,
  ConditionNode,
  CardNode,
  LoopNode,
  LlmNode,
  McpNode,
  KnowledgeNode,
  HumanNode,
  ApiNode,
  SkillNode,
  PythonNode,
  ShellNode,
  MemoryNode,
  TodoNode,
  MediaGenNode,
  IntentRouterNode,
  SubAgentNode,
  AgendaNode
}
