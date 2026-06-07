"""
Skill ZIP 上传工具

处理 Skill ZIP 文件上传、解析和保存
"""

import asyncio
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.skill import Skill
from app.agent_flow.exceptions import FlowValidationError
from app.config.settings import settings


def _write_bytes(path, content: bytes) -> None:
    """同步写入字节到文件（供 asyncio.to_thread 调用）"""
    with open(path, "wb") as f:
        f.write(content)


class SkillUploader:
    """
    Skill 上传工具类

    处理 Skill ZIP 文件上传：
    1. 解压 ZIP 文件
    2. 解析 SKILL.md 获取 name 和 description
    3. 校验名称唯一性
    4. 保存文件到 uploads/skills/ 目录
    5. 创建数据库记录
    """

    SKILL_FILE_NAME = "SKILL.md"

    def __init__(self):
        self._front_matter_pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
        self._block_scalar_indicators = ("|", ">")

    def _parse_skill_markdown(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """
        解析 SKILL.md 文件

        提取 YAML front matter 中的 name 和 description，支持多行块标量（|）
        """
        try:
            content = file_path.read_text(encoding="utf-8")

            match = self._front_matter_pattern.match(content)
            if not match:
                return None

            front_matter = match.group(1)

            skill_info: Dict[str, Any] = {}
            lines = front_matter.strip().split("\n")
            current_key = None
            current_value_lines: list[str] = []
            in_block_scalar = False
            block_style = "|"

            for line in lines:
                if in_block_scalar and (
                    line.startswith("  ") or line.startswith("\t") or line.strip() == ""
                ):
                    current_value_lines.append(line.rstrip())
                    continue

                if in_block_scalar:
                    if block_style == ">":
                        skill_info[current_key] = " ".join(
                            "\n".join(current_value_lines).split()
                        )
                    else:
                        skill_info[current_key] = "\n".join(current_value_lines).strip()
                    in_block_scalar = False
                    current_key = None
                    current_value_lines = []

                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip().lower().replace("-", "_")
                    value = value.strip()

                    if value in self._block_scalar_indicators:
                        in_block_scalar = True
                        block_style = value
                        current_key = key
                        current_value_lines = []
                    else:
                        value = value.strip("\"'")
                        skill_info[key] = value

            if in_block_scalar and current_key:
                if block_style == ">":
                    skill_info[current_key] = " ".join(
                        "\n".join(current_value_lines).split()
                    )
                else:
                    skill_info[current_key] = "\n".join(current_value_lines).strip()

            if "name" not in skill_info or "description" not in skill_info:
                return None

            return {
                "name": skill_info.get("name", ""),
                "description": skill_info.get("description", ""),
                "category": skill_info.get("category"),
                "tags": skill_info.get("tags"),
                "icon": skill_info.get("icon"),
            }

        except Exception as e:
            print(f"[SkillUploader] 解析文件失败 {file_path}: {e}")
            return None

    async def handle_upload_zip(
        self, zip_file: UploadFile, db: AsyncSession
    ) -> Dict[str, Any]:
        """
        处理上传的 ZIP 文件

        Args:
            zip_file: 上传的 ZIP 文件
            db: 数据库会话

        Returns:
            包含 skill 信息的字典

        Raises:
            FlowValidationError: 校验失败时抛出
        """
        if not zip_file.filename or not zip_file.filename.endswith(".zip"):
            raise FlowValidationError("只支持 ZIP 格式文件")

        skill_name = None
        temp_dir = None

        try:
            temp_dir = tempfile.mkdtemp(prefix="skill_upload_")
            zip_path = os.path.join(temp_dir, zip_file.filename)

            content = await zip_file.read()
            await asyncio.to_thread(_write_bytes, zip_path, content)

            extract_dir = os.path.join(temp_dir, "extracted")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            skill_md_path = None
            for root, dirs, files in os.walk(extract_dir):
                if "SKILL.md" in files:
                    skill_md_path = os.path.join(root, "SKILL.md")
                    break

            if not skill_md_path:
                raise FlowValidationError("ZIP 文件中未找到 SKILL.md 文件")

            skill_info = self._parse_skill_markdown(Path(skill_md_path))
            if not skill_info:
                raise FlowValidationError(
                    "SKILL.md 文件格式错误，必须包含 YAML front matter 中的 name 和 description"
                )

            skill_name = skill_info["name"]

            if not re.match(r"^[a-zA-Z0-9_-]+$", skill_name):
                raise FlowValidationError(
                    f"Skill 名称 '{skill_name}' 格式无效，只能包含字母、数字、中划线和下划线"
                )

            existing = await db.execute(
                select(Skill).where(Skill.name == skill_name, Skill.is_delete == 0)
            )
            if existing.scalar_one_or_none():
                raise FlowValidationError(
                    f"Skill 名称 '{skill_name}' 已存在，请使用不同的名称"
                )

            upload_dir = settings.get_absolute_path(settings.upload_dir)
            target_dir = upload_dir / "skills" / skill_name
            if target_dir.exists():
                existing = await db.execute(
                    select(Skill).where(Skill.name == skill_name, Skill.is_delete == 0)
                )
                if existing.scalar_one_or_none():
                    raise FlowValidationError(
                        f"Skill 名称 '{skill_name}' 已存在，请使用不同的名称"
                    )
                shutil.rmtree(target_dir, ignore_errors=True)

            skill_md_dir = Path(skill_md_path).parent
            target_dir.mkdir(parents=True, exist_ok=True)

            for item in skill_md_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, target_dir / item.name)
                elif item.is_dir():
                    shutil.copytree(item, target_dir / item.name)

            skill_path = (
                f"{settings.upload_dir}/skills/{skill_name}/{self.SKILL_FILE_NAME}"
            )

            new_skill = Skill(
                name=skill_name,
                description=skill_info["description"],
                skill_path=skill_path,
                category=skill_info.get("category"),
                tags=skill_info.get("tags"),
                icon=skill_info.get("icon"),
                is_enabled=1,
                is_system=0,
                sort_order=0,
            )
            db.add(new_skill)
            await db.commit()
            await db.refresh(new_skill)

            return {
                "id": new_skill.id,
                "name": new_skill.name,
                "description": new_skill.description,
                "skill_path": new_skill.skill_path,
                "category": new_skill.category,
                "tags": new_skill.tags,
                "icon": new_skill.icon,
                "is_enabled": new_skill.is_enabled,
                "create_time": new_skill.create_time,
            }

        except FlowValidationError:
            raise
        except zipfile.BadZipFile:
            raise FlowValidationError("无效的 ZIP 文件")
        except Exception as e:
            if skill_name:
                upload_dir = settings.get_absolute_path(settings.upload_dir)
                target_dir = upload_dir / "skills" / skill_name
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
            raise FlowValidationError(f"上传处理失败: {str(e)}")
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def handle_batch_upload(
        self, zip_files: List[UploadFile], db: AsyncSession
    ) -> Dict[str, Any]:
        """
        批量处理上传的 ZIP 文件

        逐个处理，单个失败不影响其他文件，返回成功/失败统计

        Args:
            zip_files: 上传的 ZIP 文件列表
            db: 数据库会话

        Returns:
            包含 success_count、failed_items、skills 的字典
        """
        success_count = 0
        failed_items: List[Dict[str, str]] = []
        skills: List[Dict[str, Any]] = []

        for zip_file in zip_files:
            filename = zip_file.filename or "unknown"
            try:
                result = await self.handle_upload_zip(zip_file, db)
                success_count += 1
                skills.append(result)
            except FlowValidationError as e:
                failed_items.append({"filename": filename, "reason": str(e)})

        return {
            "success_count": success_count,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
            "skills": skills,
        }


skill_uploader = SkillUploader()
