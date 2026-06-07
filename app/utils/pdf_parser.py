"""
PDF 文档解析器

使用 pymupdf 提取文本结构和标题层级，
使用 pdfplumber 提取表格并转为 markdown 格式，
合并输出完整的 markdown 文本。
"""

import re
import logging
from typing import Optional
from collections import Counter

logger = logging.getLogger(__name__)


class PdfParser:
    """
    PDF → Markdown 解析器

    - pymupdf: 文本结构提取（按 font size 识别标题层级）
    - pdfplumber: 表格提取（转为 markdown 表格）
    """

    # 正文最小字号阈值（低于此值视为脚注/页眉页脚，忽略）
    MIN_FONT_SIZE = 5.0
    # 表格最小行数（少于此值不视为表格）
    MIN_TABLE_ROWS = 2

    def parse_to_markdown(self, file_path: str) -> str:
        """
        解析 PDF 文件为 markdown 文本

        Args:
            file_path: PDF 文件路径

        Returns:
            markdown 格式文本
        """
        import fitz
        import pdfplumber

        md_lines: list[str] = []

        # ---- 阶段1: 用 pdfplumber 提取所有页的表格 ----
        page_tables: dict[int, list[str]] = {}
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    tables = page.extract_tables()
                    table_md_list = []
                    for table in tables:
                        if not table or len(table) < self.MIN_TABLE_ROWS:
                            continue
                        md_table = self._table_to_markdown(table)
                        if md_table:
                            table_md_list.append(md_table)
                    if table_md_list:
                        page_tables[page_idx] = table_md_list
        except Exception as e:
            logger.warning(f"pdfplumber 提取表格失败，将仅使用 pymupdf 文本: {e}")

        # ---- 阶段2: 用 pymupdf 提取文本结构 ----
        try:
            doc = fitz.open(file_path)
        except Exception as e:
            raise ValueError(f"无法打开 PDF 文件: {e}") from e

        try:
            # 统计所有文本块的字号分布，确定正文基准字号
            font_size_counter: Counter[float] = Counter()
            for page in doc:
                blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)[
                    "blocks"
                ]
                for block in blocks:
                    if block["type"] != 0:
                        continue
                    for line in block["lines"]:
                        for span in line["spans"]:
                            text = span["text"].strip()
                            if text and span["size"] >= self.MIN_FONT_SIZE:
                                font_size_counter[round(span["size"], 1)] += len(text)

            # 正文基准字号 = 出现次数最多的字号
            body_font_size = self._get_body_font_size(font_size_counter)

            # ---- 阶段3: 逐页提取文本并插入表格 ----
            for page_idx, page in enumerate(doc):
                # 插入当前页的表格
                if page_idx in page_tables:
                    for table_md in page_tables[page_idx]:
                        md_lines.append("")
                        md_lines.append(table_md)
                        md_lines.append("")

                # 提取文本块
                blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)[
                    "blocks"
                ]
                for block in blocks:
                    if block["type"] != 0:
                        continue
                    for line in block["lines"]:
                        line_text = ""
                        line_max_size = 0.0
                        is_bold = False
                        for span in line["spans"]:
                            text = span["text"]
                            if not text.strip():
                                line_text += text
                                continue
                            size = span["size"]
                            if size > line_max_size:
                                line_max_size = size
                            font_flags = span.get("flags", 0)
                            if font_flags & (1 << 4):
                                is_bold = True
                            line_text += text

                        line_text = line_text.strip()
                        if not line_text:
                            continue

                        if line_max_size < self.MIN_FONT_SIZE:
                            continue

                        # 判断标题级别
                        heading_level = self._detect_heading_level(
                            line_max_size, body_font_size, is_bold
                        )

                        if heading_level:
                            md_lines.append("")
                            md_lines.append(f"{'#' * heading_level} {line_text}")
                            md_lines.append("")
                        elif self._is_list_item(line_text):
                            md_lines.append(line_text)
                        else:
                            md_lines.append(line_text)

                md_lines.append("")
        finally:
            doc.close()

        # ---- 阶段4: 清理输出 ----
        result = "\n".join(md_lines)
        result = self._cleanup_markdown(result)
        return result

    def _get_body_font_size(self, font_size_counter: Counter[float]) -> float:
        """
        根据字频统计确定正文基准字号

        出现文本量最多的字号视为正文，
        如果没有数据则回退到默认值 12.0
        """
        if not font_size_counter:
            return 12.0
        most_common_size, _ = font_size_counter.most_common(1)[0]
        return most_common_size

    def _detect_heading_level(
        self, font_size: float, body_font_size: float, is_bold: bool
    ) -> Optional[int]:
        """
        根据 font size 与正文字号的比值判断标题级别

        大于正文 1.5 倍 → h1，1.3 倍 → h2，1.15 倍 → h3
        加粗且略大 → h4
        """
        if body_font_size <= 0:
            return None

        ratio = font_size / body_font_size

        if ratio >= 1.5:
            return 1
        if ratio >= 1.3:
            return 2
        if ratio >= 1.15:
            return 3
        if is_bold and ratio >= 1.05:
            return 4
        return None

    def _is_list_item(self, text: str) -> bool:
        """判断是否为列表项"""
        list_patterns = [
            r"^[-•●▪▸►]\s+",
            r"^\d+[.)]\s+",
            r"^[一二三四五六七八九十]+[、.．]\s+",
            r"^[（(][一二三四五六七八九十\d]+[)）]\s+",
        ]
        for pattern in list_patterns:
            if re.match(pattern, text):
                return True
        return False

    def _table_to_markdown(self, table: list[list[Optional[str]]]) -> str:
        """
        将 pdfplumber 提取的表格转为 markdown 表格格式

        Args:
            table: 表格数据，每行为一个列表

        Returns:
            markdown 表格字符串
        """
        if not table:
            return ""

        cleaned_rows = []
        for row in table:
            cleaned = [cell.strip() if cell else "" for cell in row]
            if any(cleaned):
                cleaned_rows.append(cleaned)

        if len(cleaned_rows) < self.MIN_TABLE_ROWS:
            return ""

        col_count = max(len(row) for row in cleaned_rows)

        # 补齐列数
        for row in cleaned_rows:
            while len(row) < col_count:
                row.append("")

        lines = []
        # 表头
        header = "| " + " | ".join(cleaned_rows[0]) + " |"
        lines.append(header)

        # 分隔线
        separator = "| " + " | ".join(["---"] * col_count) + " |"
        lines.append(separator)

        # 数据行
        for row in cleaned_rows[1:]:
            line = "| " + " | ".join(row) + " |"
            lines.append(line)

        return "\n".join(lines)

    def _cleanup_markdown(self, text: str) -> str:
        """清理 markdown 文本"""
        # 合并连续多个空行为最多两个换行
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        # 去除行尾空白
        text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
        return text.strip()


pdf_parser = PdfParser()
