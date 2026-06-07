---
name: skill-creator
description: 创建、编辑、优化或审计 AgentSkills。当需要创建新技能、上传技能到系统、修改现有技能、上传ZIP包、重载技能元数据、刷新技能内容、或审计现有技能时使用。触发词：「创建技能」「上传技能」「重载技能」「修改技能」「更新技能」「刷新技能」「审计技能」「整理技能」「清理技能」。
---

# Skill Creator

## Skill 结构

```
skill-name/
├── SKILL.md          # 必需，YAML frontmatter + Markdown 正文
├── scripts/          # 可执行代码（按需，不加载到上下文即可执行）
├── references/       # 参考文档（按需加载到上下文）
└── assets/           # 输出用资源（模板、图片等，不加载到上下文）
```

**禁止创建** README.md、CHANGELOG.md 等辅助文件。

## 核心原则

1. **简洁**：上下文窗口是公共资源，只添加 AI 不具备的上下文，用示例代替解释
2. **三级加载**：元数据(~100字,常驻) → SKILL.md 正文(<500行,触发时加载) → 打包资源(按需)
3. **正文精简**：接近 500 行时拆分到 references/，SKILL.md 只保留核心工作流 + 选择指导 + 引用链接
4. **自由度匹配**：易出错操作→具体脚本(低自由度)；多种方法有效→文本指令(高自由度)

## SKILL.md 编写规范

### Frontmatter（YAML）

只含 `name` 和 `description` 两个字段。**description 是主要触发机制**，必须包含所有"何时使用"信息（正文只在触发后加载，正文中的触发描述对 AI 无效）。

```yaml
---
name: skill-name
description: |
  技能描述。说明做什么 + 使用场景 + 触发词。
  ❌ 不要把触发词写在正文里，AI 看不到。
---
```

### 正文编写指南

- 始终使用祈使/不定式形式
- information 只放一处（SKILL.md 或 references/，不要两边都放）
- 详细参考资料、模式、配置放 references/，SKILL.md 只保留核心流程
- references 从 SKILL.md 一级深度链接，避免嵌套

### References 拆分策略

| 模式 | 适用场景 | 示例 |
|------|---------|------|
| 按功能拆分 | 高级功能按需加载 | SKILL.md 提及「详见 references/advanced.md」 |
| 按领域拆分 | 多领域技能 | 按领域各一个文件，用户问哪个读哪个 |
| 按变体拆分 | 多框架/多选项 | aws.md / gcp.md / azure.md |

超过 100 行的 reference 文件在顶部放目录。

## 技能创建流程

```
1. 理解需求 → 2. 规划资源 → 3. 初始化 → 4. 编辑 → 5. 上传/重载 → 6. 迭代
```

### 1. 理解需求

通过示例确认技能用途。问用户：支持什么功能？使用场景？触发词是什么？

### 2. 规划资源

分析示例，识别哪些内容值得打包为 scripts/references/assets。

### 3. 初始化

```bash
scripts/init_skill.py <skill-name> --path <output-dir> [--resources scripts,references,assets] [--examples]
```

命名规则：小写字母+数字+连字符，≤64 字符，优先动词短语。

### 4. 编辑

编辑 SKILL.md 和资源文件。脚本必须实际运行测试。

## API 接口

服务器：`http://127.0.0.1:8000`

| 操作 | 接口 | 说明 |
|------|------|------|
| 上传新技能 | `POST /api/skill/upload` | multipart/form-data, file=<skill.zip> |
| 重载元数据 | `POST /api/skill/{id}/reload` | **修改已有技能后必须调用** |
| 获取内容 | `GET /api/skill/{id}/content` | 获取 SKILL.md 文件内容 |
| 列表 | `GET /api/skill/list` | 所有启用技能 |

### 上传规则

- **新建**：ZIP 包上传（内含 SKILL.md + 可选资源）
- **修改已有**：直接改 `uploads/skills/{name}/` 下文件，然后调 reload

ZIP 内 SKILL.md 必须有 YAML frontmatter（name + description）。

## 本项目技能编写模板

### 操作类（脚本驱动）

```markdown
> ⚠️ **执行规范（强制）**
> 1. 严格按文档执行，直接运行脚本，不自定义
> 2. 报错时告知原因和脚本路径，停止执行
> 3. 文档未覆盖的场景，先更新文档再执行

## 概述 → 前置要求 → 执行步骤 → 参数说明 → 输出说明 → 错误处理 → 踩坑记录
```

### 角色扮演类

章节：角色规则 → 身份卡 → 心智模型 → 决策启发式 → 表达DNA → 诚实边界 → 调研来源

### 本项目共同规范

- 直接运行脚本，不自定义
- 精确路径，禁止通配符 `*`
- 错误即停，告知原因和路径
- 关键步骤后询问用户
- 主会话直接执行，禁止 subagent
