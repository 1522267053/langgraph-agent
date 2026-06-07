export const VariablePrefix = {
  INPUT: 'input',
  VARIABLES: 'variables',
  OUTPUT: 'output',
  NODES: 'nodes'
} as const

export type VariablePrefixType = (typeof VariablePrefix)[keyof typeof VariablePrefix]

/**
 * 变量路径规范（前后端统一）
 *
 * === 主视图（设计时） ===
 *   input.<field>              流程输入参数
 *   nodes.<nodeId>.<var>       上游节点输出
 *   nodes.<nodeId>.<var>.<sub> 节点输出的子路径
 *
 * === 子视图 / 循环体内（设计时） ===
 *   input.<field>                     循环输入映射字段（前端自动转换为 nodes.<loopKey>.input_<field>）
 *   nodes.<loopKey>.input_<field>     循环输入映射字段（直接引用）
 *   nodes.<subNodeId>.<var>           子图内上游节点输出
 *   variables.loop_index              当前迭代索引
 *   variables.loop_count              总迭代次数
 *   variables.loop_item               当前遍历元素（for_each 模式）
 *
 * === 模板引用（提示词中使用 {{ }}） ===
 *   {{变量名}}           无前缀，按 context → input → variables 优先级查找
 *   {{input.xxx}}        流程输入
 *   {{variables.xxx}}    流程变量
 *   {{output.xxx}}       流程输出
 *
 * === 运行时重写（后端自动处理，前端无需关心） ===
 *   卡片子流程中 input.xxx  → nodes.<cardKey>.input_xxx
 *   循环迭代中 nodes.xxx   → nodes.<loopKey>__xxx_iter_<i>
 */
