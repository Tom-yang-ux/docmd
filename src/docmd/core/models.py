"""统一文档结构层的数据模型。

多引擎结果被转换为同一种内部结构：
- Document：一个任务对应一份文档
- ContentBlock：按页/标题/段落/表格/图片说明/公式/关键字段拆分
- Field：字段级记录，绑定多引擎结果、页码坐标、可信等级、用户确认值
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from .constants import BlockType, FieldGrade


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class BoundingBox:
    """坐标区域（页内，可选）。"""
    page: Optional[int] = None
    x0: Optional[float] = None
    y0: Optional[float] = None
    x1: Optional[float] = None
    y1: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
        }


@dataclass
class EngineResult:
    """单个引擎对一个字段/内容块的原始识别结果。"""
    engine: str
    text: str
    confidence: Optional[float] = None
    bbox: Optional[BoundingBox] = None
    raw: Any = None

    def to_dict(self) -> dict:
        d: dict = {
            "engine": self.engine,
            "text": self.text,
            "confidence": self.confidence,
        }
        if self.bbox is not None:
            d["bbox"] = self.bbox.to_dict()
        return d


@dataclass
class Field:
    """字段级记录（含完整证据链：页码、坐标、来源图片、多引擎候选、冲突原因）。"""
    id: str
    key: str                # 字段名
    field_type: str         # 金额/日期/姓名/编号/...
    block_id: Optional[str] = None
    page: Optional[int] = None          # 字段所在页码
    source_image: Optional[str] = None  # 来源原图局部区域
    candidates: list[EngineResult] = field(default_factory=list)
    grade: FieldGrade = FieldGrade.D
    conflict_reason: str = ""
    is_key_field: bool = False
    user_confirmed: Optional[str] = None
    confirm_source: str = ""        # candidate A / candidate B / manual
    confirmed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def bbox(self):
        """字段的主要坐标：取首个候选的 bbox（如存在）。"""
        for c in self.candidates:
            if c.bbox is not None:
                return c.bbox
        return None
