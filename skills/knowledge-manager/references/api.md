# Knowledge Base API 参考

Base URL: `http://127.0.0.1:8000` | 响应: `{code:1, msg, data}` 成功 / `{code:0, msg}` 失败

## 目录

- [处理状态码](#处理状态码)
- [知识库接口](#知识库接口)
- [文档接口](#文档接口)
- [分段接口](#分段接口)
- [知识沉淀接口](#知识沉淀接口)
- [完整数据模型](#完整数据模型)

---

## 处理状态码

文档 `processing_status` 字段枚举（`ProcessingStatus`）：

| 值 | 常量名 | 含义 | 说明 |
|:--:|--------|------|------|
| `0` | PENDING | 待处理 | 上传后初始状态，等待后台定时任务拉取 |
| `1` | PROCESSING | 处理中 | 正在解析原文、分段、生成标题索引 |
| `2` | COMPLETED | 已完成 | 解析分段完成，可被检索、可向量化 |
| `3` | FAILED | 失败 | 处理出错，`error_message` 字段含原因 |
| `4` | VECTORIZING | 向量化中 | 正在生成分段向量写入 ChromaDB |

**状态流转**：`0(待处理) → 1(处理中) → 2(已完成)` ；向量化时 `2 → 4 → 2`；失败时 `→ 3`。

---

## 知识库接口

前缀：`/api/knowledge/base`

### POST /page — 分页列表

```json
{
  "page": 1,
  "page_size": 20,
  "condition": {
    "name": "关键词"
  }
}
```

`condition.name` 匹配名称或描述（模糊，可选）。

**响应**：`data.items[]` + `data.total`

```json
{
  "code": 1,
  "data": {
    "items": [
      {
        "id": 1,
        "name": "产品手册",
        "description": "公司产品使用手册",
        "status": 1,
        "create_time": "2026-07-29 10:00:00",
        "update_time": "2026-07-29 10:00:00"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 20
  }
}
```

### GET /get/{id} — 详情

返回单个知识库对象（字段同上）。

### POST /create — 创建

```json
{
  "name": "产品手册",
  "description": "可选描述",
  "status": 1
}
```

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `name` | ✅ | 名称 |
| `description` | | 描述 |
| `status` | | `0=禁用` `1=启用`，默认启用 |

### POST /update — 更新

使用 `exclude_unset`，未传字段保持不变，**无法将字段置为 `null`**。

```json
{
  "id": 1,
  "name": "新名称",
  "description": "新描述"
}
```

### GET /delete/{id} — 删除

软删除（`is_delete=1`）。**不级联删除**下属文档，文档成为孤儿（建议先删文档）。

### POST /delete-batch — 批量删除

```json
[1, 2, 3]
```

请求体为 ID 数组。

---

## 文档接口

前缀：`/api/knowledge/document`

> 注意：文档不支持直接 `create`（`enable_create=false`），只能通过 `upload` 上传。

### POST /upload — 上传文档

`Content-Type: multipart/form-data`

| 参数 | 位置 | 必填 | 说明 |
|------|:----:|:----:|------|
| `file` | form | ✅ | 文件，支持 txt/md/docx/pdf/xlsx |
| `knowledge_base_id` | form | ✅ | 目标知识库 ID |

**响应**（`KnowledgeDocumentUploadResult`）：

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

### POST /page — 文档分页列表

```json
{
  "page": 1,
  "page_size": 20,
  "condition": {
    "title": "手册",
    "knowledge_base_id": 1
  }
}
```

`condition.title` 模糊匹配文档标题。

### GET /get/{id} — 文档详情

返回 `KnowledgeDocument` 完整字段（见[数据模型](#完整数据模型)）。

### GET /content/{id} — 原文内容

```json
{
  "code": 1,
  "data": {
    "id": 12,
    "title": "产品手册.pdf",
    "content": "文档解析后的纯文本内容...",
    "word_count": 15200,
    "file_type": "pdf"
  }
}
```

### GET /segments/{id} — 分段列表

返回该文档的所有分段（`KnowledgeDocumentSegmentBase[]`）：

```json
{
  "code": 1,
  "data": [
    {
      "id": 101,
      "document_id": 12,
      "segment_index": 0,
      "title": "第一章 概述",
      "content": "分段文本内容...",
      "word_count": 320,
      "create_time": "...",
      "update_time": "..."
    }
  ]
}
```

### GET /download/{id} — 下载源文件

返回 `FileResponse`（`application/octet-stream`），含 `Content-Disposition` 文件名。

### POST /vectorize/{kb_id} — 批量向量化知识库

```
POST /api/knowledge/document/vectorize/{knowledge_base_id}?force=false
```

| 参数 | 说明 |
|------|------|
| `force=false` | 增量向量化（仅未向量化的分段） |
| `force=true` | 强制全量重新向量化 |

**响应**（`KnowledgeBaseVectorizeResult`）：

```json
{
  "code": 1,
  "data": {
    "knowledge_base_id": 1,
    "total_documents": 5,
    "total_segments": 120,
    "vectorized_segments": 120,
    "failed_segments": 0,
    "details": [
      {
        "document_id": 12,
        "document_title": "产品手册.pdf",
        "total_segments": 40,
        "vectorized_segments": 40,
        "failed_segments": 0
      }
    ]
  }
}
```

### POST /vectorize/document/{id} — 向量化单个文档

```
POST /api/knowledge/document/vectorize/document/{document_id}?force=false
```

**异步执行**：接口立即返回，文档状态置为 `4=向量化中`，完成后回到 `2=已完成`。需轮询 `GET /get/{id}`。

> 前置条件：文档 `processing_status` 必须为 `2=已完成`，否则报错"文档未完成处理"。

### POST /reprocess/{id} — 重新处理

```
POST /api/knowledge/document/reprocess/{document_id}
```

重置文档为 `0=待处理`，由后台定时任务重新解析、分段、向量化。适用于：
- 处理失败（status=3）的重试
- 已处理文档需要重新分段

### POST /search-segments — 向量搜索

```json
{
  "knowledge_base_id": 1,
  "query": "产品保修期",
  "top_k": 5
}
```

| 字段 | 必填 | 约束 | 说明 |
|------|:----:|------|------|
| `knowledge_base_id` | ✅ | | 知识库 ID |
| `query` | ✅ | | 自然语言查询 |
| `top_k` | | 1~50，默认 5 | 返回结果数 |

**响应**：

```json
{
  "code": 1,
  "data": [
    {
      "segment_id": 101,
      "content": "分段内容...",
      "score": 0.85,
      "document_id": 12,
      "document_title": "产品手册.pdf",
      "title_id": 5,
      "title_text": "第一章 售后政策"
    }
  ],
  "msg": "找到 1 条结果"
}
```

### GET /delete/{id} — 删除文档

**级联删除**：分段、标题索引、向量存储、源文件一并删除（`delete_document_with_segments`）。

---

## 分段接口

分段（segment）是文档解析后的最小检索单元，目前没有独立的 CRUD 接口，只能通过文档接口查看：

- `GET /api/knowledge/document/segments/{document_id}` — 查看某文档的全部分段

分段会在 Agent 工具层被三层导航工具使用（`get_paragraphs` / `adjacent` 等），无需直接操作。

---

## 知识沉淀接口

前缀：`/api/knowledge/insight`

AI 综合原始段落得出的结论性知识，与原始文档分层存储。Agent 的 `knowledge_search` 工具检索时**优先匹配沉淀层**，未命中再查原始文档。

### POST /page — 分页列表

```json
{
  "page": 1,
  "page_size": 20,
  "condition": {
    "knowledge_base_id": 1
  }
}
```

### GET /get/{id} — 详情

返回沉淀完整字段（见[数据模型](#完整数据模型)）。

### POST /create — 新建

```json
{
  "knowledge_base_id": 1,
  "question": "产品保修期是多久",
  "answer": "标准保修 2 年，延保最多 5 年",
  "keywords": "保修,质保",
  "source_segment_ids": [101, 102]
}
```

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `knowledge_base_id` | ✅ | 所属知识库 |
| `question` | ✅ | 触发问题（语义检索锚点） |
| `answer` | ✅ | 沉淀结论 |
| `keywords` | | 逗号分隔关键词（辅助检索） |
| `source_segment_ids` | | 关联原始段落 ID 列表（溯源用） |

### POST /update — 更新

`exclude_unset`，未传字段不变。需传 `id`。

### GET /delete/{id} — 删除

软删除。

---

## 完整数据模型

### KnowledgeBase（知识库）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键 |
| `name` | string | 名称 |
| `description` | string | 描述 |
| `status` | int | `0=禁用` `1=启用` |
| `create_time` | datetime | 创建时间 |
| `update_time` | datetime | 更新时间 |

### KnowledgeDocument（文档）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键 |
| `knowledge_base_id` | int | 所属知识库 ID |
| `title` | string | 文档标题（默认文件名） |
| `content` | string | 解析后纯文本 |
| `file_type` | string | `txt`/`md`/`docx`/`pdf`/`xlsx` |
| `file_path` | string | 源文件存储路径 |
| `word_count` | int | 字数 |
| `segment_count` | int | 分段数量 |
| `processing_status` | int | 处理状态（见上） |
| `error_message` | string | 失败原因 |

### KnowledgeDocumentSegment（分段）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键（段落 ID） |
| `document_id` | int | 所属文档 ID |
| `segment_index` | int | 段落序号（0 起始） |
| `title` | string | 所属标题文本 |
| `title_id` | int | 所属标题索引 ID |
| `content` | string | 分段内容 |
| `word_count` | int | 字数 |
| `vector_id` | string | ChromaDB 向量 ID |

### KnowledgeInsight（知识沉淀）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键（沉淀 ID） |
| `knowledge_base_id` | int | 所属知识库 ID |
| `question` | string | 触发问题 |
| `answer` | string | 沉淀结论 |
| `keywords` | string | 关键词（逗号分隔） |
| `source_segment_ids` | string | 关联段落 ID 列表（JSON 数组字符串） |
| `create_time` | datetime | 创建时间 |
