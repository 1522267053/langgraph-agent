---
name: knowledge-manager
description: |
  创建、管理知识库及其文档和 AI 知识沉淀。适用场景：
  (1) 用户要求创建新的知识库
  (2) 用户想上传文档到知识库（支持 txt/md/docx/pdf/xlsx）
  (3) 用户想查看、修改或删除已有的知识库或文档
  (4) 用户想向量化知识库文档、重新处理失败的文档、查看处理进度
  (5) 用户想搜索知识库分段内容
  (6) 用户想查看或管理 AI 生成的知识沉淀
  (7) 用户想把知识库接入 Agent/Flow，或判断知识库为什么检索不到内容

  触发词：「创建知识库」「添加知识库」「上传文档」「向量化」「知识库检索」「知识沉淀」「文档分段」「搜索知识库」「重新处理文档」「接入 Agent」
---

# Knowledge Base Manager

服务器：`http://127.0.0.1:8000`

## 核心规则（必须遵守）

1. **上传是异步处理**：`POST /upload` 立即返回 `processing_status=0`，后台定时任务负责解析、分段、生成标题索引、向量化。需轮询状态直到 `=2`（已完成）才能检索
2. **处理状态码**：`0=待处理` `1=处理中` `2=已完成` `3=失败` `4=向量化中`
3. **支持格式**：`txt` / `md` / `docx` / `pdf` / `xlsx`
4. **向量化独立步骤**：上传处理完成（status=2）后，还需调用向量化接口才会进入 ChromaDB。未向量化的分段无法被语义检索命中
5. **三层结构**：知识库 → 文档（document）→ 分段（segment）。分段会自动建立「标题索引」（按文档标题层级），供 Agent 工具三层导航
6. **`/api/knowledge/*` 路径需登录态**：本机回环（127.0.0.1）调用免认证，外部调用需 session cookie
7. **先处理、再向量化、最后接入**：不要在文档仍为待处理或失败状态时连接到 Agent；检索不到时先检查文档状态和向量化状态

## 知识库数据模型

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 知识库 ID |
| `name` | string | 名称（创建时必填，全局可重名） |
| `description` | string | 描述（可选） |
| `status` | int | `0=禁用` `1=启用` |
| `create_time` / `update_time` | datetime | 创建/更新时间 |

## API 速查

### 知识库（`/api/knowledge/base`）

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/knowledge/base/page` | 分页列表 |
| GET | `/api/knowledge/base/get/{id}` | 详情 |
| POST | `/api/knowledge/base/create` | 创建 |
| POST | `/api/knowledge/base/update` | 更新（`exclude_unset`，无法置空字段） |
| GET | `/api/knowledge/base/delete/{id}` | 删除（软删除，不级联删文档） |
| POST | `/api/knowledge/base/delete-batch` | 批量删除 |

### 文档（`/api/knowledge/document`）

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/knowledge/document/upload` | 上传文档（multipart） |
| POST | `/api/knowledge/document/page` | 文档分页列表 |
| GET | `/api/knowledge/document/get/{id}` | 文档详情 |
| GET | `/api/knowledge/document/content/{id}` | 获取原文内容 |
| GET | `/api/knowledge/document/segments/{id}` | 获取分段列表 |
| GET | `/api/knowledge/document/download/{id}` | 下载源文件 |
| POST | `/api/knowledge/document/vectorize/{kb_id}` | 批量向量化整个知识库 |
| POST | `/api/knowledge/document/vectorize/document/{id}` | 向量化单个文档 |
| POST | `/api/knowledge/document/reprocess/{id}` | 重新处理（失败重试） |
| GET | `/api/knowledge/document/delete/{id}` | 删除文档（级联删分段+文件） |
| POST | `/api/knowledge/document/search-segments` | 向量搜索分段 |

### 知识沉淀（`/api/knowledge/insight`）

AI 综合原始段落得出的结论性知识，与原始文档分层存储，检索时优先匹配。

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/knowledge/insight/page` | 分页列表 |
| GET | `/api/knowledge/insight/get/{id}` | 详情 |
| POST | `/api/knowledge/insight/create` | 新建沉淀 |
| POST | `/api/knowledge/insight/update` | 更新 |
| GET | `/api/knowledge/insight/delete/{id}` | 删除 |

## 创建知识库流程

```
1. POST /api/knowledge/base/create     # 创建空知识库
2. POST /api/knowledge/document/upload  # 上传文档（可批量）
3. 轮询 GET /api/knowledge/document/get/{id}  # 等 processing_status=2
4. POST /api/knowledge/document/vectorize/{kb_id}  # 向量化
```

### 创建知识库

```json
POST /api/knowledge/base/create
{
  "name": "产品手册",
  "description": "公司产品使用手册与规格说明",
  "status": 1
}
```

返回的 `data.id` 即 `knowledge_base_id`，后续上传文档需要用到。

## 上传文档

### 接口

```
POST /api/knowledge/document/upload
Content-Type: multipart/form-data
```

| 参数 | 位置 | 必填 | 说明 |
|------|:----:|:----:|------|
| `file` | form | ✅ | 文件（txt/md/docx/pdf/xlsx） |
| `knowledge_base_id` | form | ✅ | 目标知识库 ID |

### 调用示例（curl）

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge/document/upload \
  -F "file=@/path/to/产品手册.pdf" \
  -F "knowledge_base_id=1"
```

### 响应

```json
{
  "code": 1,
  "data": {
    "id": 12,
    "title": "产品手册.pdf",
    "file_type": "pdf",
    "processing_status": 0
  },
  "msg": "文档已上传，后台处理中"
}
```

### 轮询处理进度

```json
GET /api/knowledge/document/get/12
```

关注 `processing_status` 字段：

| 值 | 含义 | 下一步 |
|:--:|------|--------|
| `0` | 待处理 | 继续等待 |
| `1` | 解析/分段中 | 继续等待 |
| `4` | 向量化中 | 继续等待 |
| `2` | 已完成 | 可检索/可向量化 |
| `3` | 失败 | 看 `error_message`，调 `reprocess` 重试 |

> 处理失败时 `error_message` 含错误原因。重试：`POST /api/knowledge/document/reprocess/{id}`（重置为待处理，由后台重新解析）。

## 向量化

向量化将分段转为向量存入 ChromaDB，是语义检索的前提。

### 批量向量化整个知识库

```json
POST /api/knowledge/document/vectorize/1?force=false
```

| 参数 | 说明 |
|------|------|
| `force=false`（默认） | 增量向量化，只处理未向量化的分段 |
| `force=true` | 强制重新向量化所有分段 |

### 向量化单个文档

```json
POST /api/knowledge/document/vectorize/document/12?force=false
```

> 单文档向量化异步执行，接口立即返回，需轮询 `processing_status`（`4=向量化中` → `2=完成`）。

## 搜索知识库

```json
POST /api/knowledge/document/search-segments
{
  "knowledge_base_id": 1,
  "query": "产品保修期多久",
  "top_k": 5
}
```

`top_k` 范围 1~50，返回匹配的分段内容、所属文档和标题信息。

> 注意：此接口只检索**已向量化**的原始文档分段。AI 沉淀层的检索由 Agent 的知识库工具（`knowledge_search`）自动完成，优先匹配沉淀层、兜底原始文档。

## 知识沉淀

知识沉淀是 AI 综合分析后保存的结论性知识（非原始文档内容）。Agent 运行时通过 `knowledge_save_insight` 工具自动写入，也可通过本接口手动管理。

### 新建沉淀

```json
POST /api/knowledge/insight/create
{
  "knowledge_base_id": 1,
  "question": "产品保修期是多久",
  "answer": "标准保修 2 年，延保最多 5 年，详见售后政策第 3 节。",
  "keywords": "保修,质保,售后",
  "source_segment_ids": [101, 102]
}
```

| 字段 | 说明 |
|------|------|
| `question` | 触发问题（用于语义检索匹配） |
| `answer` | 沉淀结论内容 |
| `keywords` | 关键词（逗号分隔，辅助检索） |
| `source_segment_ids` | 关联的原始段落 ID 列表（用于溯源） |

## 连接到 Agent / Flow

知识库需通过 **knowledge 节点** 接入流程，详见 [agent-manager](../agent-manager/SKILL.md)。

要点：
1. Agent 中加 `knowledge` 类型节点，配置 `knowledge_base_id` + `top_k`
2. 用工具边（`source_handle=default` → `target_handle=tools`）连到 LLM 节点
3. 连接后 LLM 自动获得三层导航工具（search / title_search / get_paragraphs / adjacent / title_lookup）+ 沉淀工具（save_insight / delete_insight）

## 通过 Agent/Flow 使用知识库

按以下顺序配置：

1. 确认知识库已启用，至少有一个文档处理完成（`processing_status=2`）。
2. 确认文档已向量化；没有向量配置或向量化失败时，语义检索可能不可用。
3. 在 Flow/Agent 编辑器添加 `knowledge` 节点。
4. 设置 `knowledge_base_id` 和 `top_k`（建议 3-5，范围 1-50）。`knowledge_base_name` 仅用于显示。
5. 将知识库节点的 `tools` 输出连接到 LLM 节点的 `tools` 输入，不要把它当作普通执行边。
6. 让 LLM 先用全局搜索定位相关内容；需要精确引用时，再按“文档列表 → 标题树 → 段落 → 相邻段落”逐层导航。
7. 只有在综合多个段落形成稳定结论时才调用 `knowledge_save_insight`；不要把单段原文、临时回答或不确定内容写入沉淀。

工具模式下的工具名由节点 key 加前缀，具体名称以运行时工具列表为准。知识库节点本身不作为普通执行节点产生流程分支。

## 添加知识库的最短流程

```text
创建知识库 → 上传文档 → 等待 processing_status=2 → 向量化 → 测试搜索 → 连接 knowledge 节点到 LLM
```

每一步都要确认接口返回 `code=1`。批量上传时逐个记录文档 ID，并逐个检查失败文档的 `error_message`；不要因为同一批次部分成功就跳过失败项。

## 检索不到内容时

1. 用 `GET /api/knowledge/document/get/{id}` 检查是否为 `processing_status=2`。
2. 如果为 `0` 或 `1`，继续等待后台处理；如果为 `3`，读取 `error_message` 后调用 `POST /api/knowledge/document/reprocess/{id}`。
3. 如果文档已处理完成但没有向量，调用对应文档或整个知识库的向量化接口，并等待状态从 `4` 回到 `2`。
4. 用 `POST /api/knowledge/document/search-segments` 做直接搜索，区分是数据问题还是 Agent 节点连接问题。
5. 直接搜索成功但 Agent 无结果时，检查节点的 `knowledge_base_id`、工具边、LLM 节点和工具调用日志。
6. 检查 embedding 配置：`EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL` 可来自 `.env` 或全局配置。缺失时提示配置向量模型，不要伪造检索结果。

## 安全与删除

- 上传文档前确认其中没有不应进入模型上下文的密码、Token、个人信息或内部机密。
- 删除知识库不会级联删除文档，建议先删除文档再删除知识库，避免产生孤儿文档。
- 删除 AI 知识沉淀前确认其来源和影响；原始文档分段与沉淀是分开存储的。

## 分页查询示例

```json
POST /api/knowledge/base/page
{
  "page": 1,
  "page_size": 20,
  "condition": {
    "name": "产品"
  }
}
```

`condition.name` 为名称/描述模糊匹配。返回 `data.items`（数组）+ `data.total`。

## 详细接口字段

完整请求/响应字段见 [references/api.md](references/api.md)。
