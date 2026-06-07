"""
知识库相关数据模型
"""

from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.base_schema import BaseView


class KnowledgeBaseBase(BaseView):
    """知识库基础模型"""

    name: Optional[str] = Field(None, description="知识库名称")
    description: Optional[str] = Field(None, description="描述")
    status: Optional[int] = Field(None, description="状态：0=禁用，1=启用")


class KnowledgeBaseCreate(KnowledgeBaseBase):
    """创建知识库"""

    name: str = Field(..., description="知识库名称")


class KnowledgeBaseUpdate(KnowledgeBaseBase):
    """更新知识库"""

    pass


class KnowledgeDocumentBase(BaseView):
    """文档基础模型"""

    knowledge_base_id: Optional[int] = Field(None, description="所属知识库ID")
    title: Optional[str] = Field(None, description="文档标题")
    content: Optional[str] = Field(None, description="文档内容")
    file_type: Optional[str] = Field(None, description="文件类型：txt/md/docx/pdf")
    file_path: Optional[str] = Field(None, description="原始文件存储路径")
    word_count: Optional[int] = Field(None, description="字数")
    segment_count: Optional[int] = Field(None, description="分段数量")
    processing_status: Optional[int] = Field(
        None, description="处理状态：0=待处理，1=处理中，2=已完成，3=失败，4=向量化中"
    )
    error_message: Optional[str] = Field(None, description="处理失败时的错误信息")


class KnowledgeDocumentCreate(KnowledgeDocumentBase):
    """创建文档"""

    knowledge_base_id: int = Field(..., description="所属知识库ID")
    title: str = Field(..., description="文档标题")


class KnowledgeDocumentUpdate(KnowledgeDocumentBase):
    """更新文档"""

    pass


class KnowledgeDocumentSegmentBase(BaseView):
    """文档分段基础模型"""

    document_id: Optional[int] = Field(None, description="所属文档ID")
    segment_index: Optional[int] = Field(None, description="分段序号")
    title: Optional[str] = Field(None, description="分段标题")
    title_id: Optional[int] = Field(None, description="所属标题ID")
    content: Optional[str] = Field(None, description="分段内容")
    word_count: Optional[int] = Field(None, description="字数")
    vector_id: Optional[str] = Field(None, description="向量存储ID")


class KnowledgeDocumentSegmentCreate(BaseView):
    """创建文档分段"""

    document_id: int = Field(..., description="所属文档ID")
    segment_index: int = Field(..., description="分段序号")
    title: Optional[str] = Field(None, description="分段标题")
    content: str = Field(..., description="分段内容")
    word_count: int = Field(default=0, description="字数")


class KnowledgeDocumentSegmentUpdate(KnowledgeDocumentSegmentBase):
    """更新文档分段"""

    pass


class KnowledgeDocumentUploadResult(BaseView):
    """文档上传结果"""

    id: int = Field(..., description="文档ID")
    title: str = Field(..., description="文档标题")
    file_type: str = Field(..., description="文件类型")
    processing_status: int = Field(
        default=0,
        description="处理状态：0=待处理，1=处理中，2=已完成，3=失败，4=向量化中",
    )


class KnowledgeDocumentVectorizeResult(BaseView):
    """文档向量化结果"""

    document_id: int = Field(..., description="文档ID")
    document_title: str = Field(..., description="文档标题")
    total_segments: int = Field(..., description="分段总数")
    vectorized_segments: int = Field(..., description="已向量化分段数")
    failed_segments: int = Field(default=0, description="失败分段数")


class KnowledgeBaseVectorizeResult(BaseView):
    """知识库批量向量化结果"""

    knowledge_base_id: int = Field(..., description="知识库ID")
    total_documents: int = Field(..., description="文档总数")
    total_segments: int = Field(..., description="分段总数")
    vectorized_segments: int = Field(..., description="已向量化分段数")
    failed_segments: int = Field(default=0, description="失败分段数")
    details: Optional[List[KnowledgeDocumentVectorizeResult]] = Field(
        None, description="各文档详情"
    )


# ---- 三层知识库导航 Schema ----


class KnowledgeDocumentTitleBase(BaseView):
    """文档标题索引基础模型"""

    document_id: Optional[int] = Field(None, description="所属文档ID")
    title_index: Optional[int] = Field(None, description="文档内标题序号")
    level: Optional[int] = Field(None, description="标题级别 1-6")
    title: Optional[str] = Field(None, description="标题文本")
    start_segment_index: Optional[int] = Field(None, description="首段落序号")
    end_segment_index: Optional[int] = Field(None, description="末段落序号")
    vector_id: Optional[str] = Field(None, description="向量存储ID")


class KnowledgeDocumentTitleCreate(BaseView):
    """创建文档标题索引"""

    document_id: int = Field(..., description="所属文档ID")
    title_index: int = Field(..., description="文档内标题序号")
    level: int = Field(..., description="标题级别 1-6")
    title: str = Field(..., description="标题文本")
    start_segment_index: int = Field(..., description="首段落序号")
    end_segment_index: int = Field(..., description="末段落序号")


class KnowledgeDocumentTitleUpdate(KnowledgeDocumentTitleBase):
    """更新文档标题索引"""

    pass


class TitleTreeItem(BaseModel):
    """标题树节点（工具返回用）"""

    id: int = Field(..., description="标题ID")
    level: int = Field(..., description="标题级别 1-6")
    title: str = Field(..., description="标题文本")
    title_index: int = Field(..., description="文档内标题序号")
    paragraph_count: int = Field(..., description="该标题下段落数量")

    model_config = ConfigDict(from_attributes=True)


class DocumentListItem(BaseModel):
    """文档列表项（工具返回用）"""

    id: int = Field(..., description="文档ID")
    title: str = Field(..., description="文档标题")
    file_type: str = Field(..., description="文件类型")
    title_count: int = Field(default=0, description="标题数量")

    model_config = ConfigDict(from_attributes=True)


class ParagraphItem(BaseModel):
    """段落项（工具返回用）"""

    id: int = Field(..., description="段落ID")
    segment_index: int = Field(..., description="段落序号")
    content: str = Field(..., description="段落内容")
    word_count: int = Field(default=0, description="字数")

    model_config = ConfigDict(from_attributes=True)


class AdjacentParagraphsResult(BaseModel):
    """相邻段落结果（工具返回用）"""

    prev: Optional[ParagraphItem] = Field(None, description="上一个段落")
    current: Optional[ParagraphItem] = Field(None, description="当前段落")
    next: Optional[ParagraphItem] = Field(None, description="下一个段落")


class TitleLookupResult(BaseModel):
    """段落反向查找标题结果（工具返回用）"""

    current_title: Optional[TitleTreeItem] = Field(None, description="当前所属标题")
    title_tree: List[TitleTreeItem] = Field(
        default_factory=list, description="文档完整标题树"
    )


class SegmentSearchCondition(BaseModel):
    """分段向量搜索条件"""

    knowledge_base_id: int = Field(..., description="知识库ID")
    query: str = Field(..., description="搜索文本")
    top_k: int = Field(default=5, ge=1, le=50, description="返回结果数量")
