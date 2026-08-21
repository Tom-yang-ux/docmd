"""统一识别接口。

每个引擎输出统一结构：
- 文本型：List[ContentBlock]（直接构造）
- 视觉型：原始识别结果（Markdown/JSON/坐标/表格/公式/置信度）

文本提取器实现 build_blocks()；视觉适配器实现 recognize_pages()。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..core.document import ContentBlock
from ..core.models import BoundingBox, EngineResult


class BaseExtractor(ABC):
    engine = "base"

    @abstractmethod
    def can_handle(self, file_type: str) -> bool: ...

    @abstractmethod
    def build_blocks(self, file_path: str) -> list[ContentBlock]: ...


class BaseVisionAdapter(ABC):
    """视觉识别适配器统一基类（PaddleOCR-VL / MinerU / DeepSeek-OCR）。"""
    engine = "vision"

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def recognize_page(self, image_path: str, page: int,
                       extra: Optional[dict] = None) -> dict:
        """返回 {content, blocks?, raw, confidence}。"""
