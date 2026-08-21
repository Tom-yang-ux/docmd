"""文档导入模块。

- 类型识别（PDF/图片/Word/Excel/PPT）
- 文本提取 vs 视觉识别分流（可复制文档走原生文本提取；扫描音/图片走视觉）
"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ..core.constants import TaskState

TEXT_TYPES = {".pdf", ".docx", ".xlsx", ".pptx"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
OFFICE_TEXT = {".docx", ".xlsx", ".pptx"}


@dataclass
class ImportedFile:
    task_id: str
    source_path: str
    title: str
    file_type: str          # pdf / image / docx / xlsx / pptx / unknown
    origin_path: str        # 原始路径
    stored_name: str        # 副本文件名（数据目录内）
    copied: bool = False


class Importer:
    """把外部文件导入数据目录并登记任务。"""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 类型识别 ----------
    @staticmethod
    def detect_type(path: str) -> str:
        ext = Path(path).suffix.lower()
        if ext in TEXT_TYPES:
            return ext[1:]          # pdf / docx / xlsx / pptx
        if ext in IMAGE_TYPES:
            return "image"
        return "unknown"

    @staticmethod
    def needs_vision(file_type: str) -> bool:
        """判断是否需要进入视觉识别流程。

        图片必须视觉；Office 文件优先文本提取（视觉仅用于复核/复杂版式）；
        PDF 需进一步判断是否可复制文本（此处返回“默认尝试文本”，由提取时探测）。
        """
        if file_type == "image":
            return True
        if file_type in {".pdf", "pdf"}:
            # 由文本提取器探测，扫描件自动落到视觉
            return False
        return False

    # ---------- 导入 ----------
    def import_file(self, source_path: str, origin_path: Optional[str] = None) -> ImportedFile:
        src = Path(source_path)
        if not src.exists():
            raise FileNotFoundError(f"文件不存在: {source_path}")
        ftype = self.detect_type(str(src))
        task_id = self._task_id(str(src))
        title = Path(str(src)).stem
        stored_name = f"{task_id}{src.suffix.lower()}"
        stored = self.raw_dir / stored_name
        # 同一文件已导入则跳过拷贝
        copied = False
        if not stored.exists():
            shutil.copy2(src, stored)
            copied = True
        return ImportedFile(
            task_id=task_id,
            source_path=str(stored),
            title=title,
            file_type=ftype,
            origin_path=origin_path or str(src),
            stored_name=stored_name,
            copied=copied,
        )

    def import_many(self, paths: Iterable[str]) -> list[ImportedFile]:
        return [self.import_file(p) for p in paths]

    @staticmethod
    def _task_id(source_path: str) -> str:
        """按源文件路径生成确定性任务 ID：同一路径 → 同一任务（避免重复识别）。"""
        h = hashlib.sha1(os.path.abspath(source_path).encode("utf-8")).hexdigest()[:12]
        return f"task_{h}"
