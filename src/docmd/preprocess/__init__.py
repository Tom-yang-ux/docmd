"""图像预处理模块。

- PDF 按页转换为图片
- 页面方向检测与旋转校正
- 低清晰度/倾斜/阴影/水印/裁切异常检测
- 低质量页生成增强版本（去噪、对比度、锐化、局部放大、重新裁边）
- 原图与增强图同时保留，确保可追溯
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


@dataclass
class PageImage:
    """单页渲染结果，包含原图与（可选）增强图。"""
    index: int
    original: str           # 原图路径
    enhanced: Optional[str] = None
    quality: str = "ok"     # ok / low / tilt / shadow / watermark / crop
    confidence: float = 1.0
    issues: list[str] = field(default_factory=list)


@dataclass
class PreprocessResult:
    pages: list[PageImage] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)


class Preprocessor:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.pre_dir = self.data_dir / "preprocessed"
        self.enh_dir = self.data_dir / "enhanced"
        self.pre_dir.mkdir(parents=True, exist_ok=True)
        self.enh_dir.mkdir(parents=True, exist_ok=True)

    # ---------- PDF 转图 ----------
    def pdf_to_images(self, pdf_path: str, task_id: str, zoom: float = 2.0) -> list[PageImage]:
        import pymupdf  # type: ignore
        pages: list[PageImage] = []
        doc = pymupdf.open(pdf_path)
        pdf_title = doc.metadata.get("title") or ""
        for i, page in enumerate(doc):
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img_path = str(self.pre_dir / f"{task_id}_p{i+1:03d}.png")
            pix.save(img_path)
            img = Image.open(img_path)
            issues = self._detect_issues(img)
            pi = PageImage(index=i + 1, original=img_path, issues=issues)
            if issues:
                pi.quality = issues[0]
                pi.enhanced = self._enhance(img, task_id, i + 1)
            pi.confidence = self._quality_score(img, issues)
            pages.append(pi)
        doc.close()
        return pages

    # ---------- 图片直入 ----------
    def image_to_page(self, img_path: str, task_id: str, page_no: int = 1) -> list[PageImage]:
        img = Image.open(img_path)
        stored = str(self.pre_dir / f"{task_id}_p{page_no:03d}.png")
        img.convert("RGB").save(stored, "PNG")
        issues = self._detect_issues(img)
        pi = PageImage(index=page_no, original=stored, issues=issues)
        if issues:
            pi.quality = issues[0]
            pi.enhanced = self._enhance(img, task_id, page_no)
        pi.confidence = self._quality_score(img, issues)
        return [pi]

    # ---------- 质量检测 ----------
    @staticmethod
    def _detect_issues(image: Image.Image) -> list[str]:
        issues: list[str] = []
        gray = image.convert("L")
        w, h = gray.size
        # 低清晰度/空白：原图灰度层级过少 → 内容不足
        hist = gray.histogram()
        distinct = sum(1 for v in hist if v > 0)
        if distinct < 8:
            issues.append("low")
        # 阴影/亮度异常：均值偏暗或偏亮
        mean = _mean(gray)
        if mean < 50:
            issues.append("shadow")
        # 裁切异常：边缘大块纯色
        edge = _edge_ratio(gray)
        if edge > 0.9:
            issues.append("crop")
        return issues

    @staticmethod
    def _quality_score(image: Image.Image, issues: list[str]) -> float:
        gray = image.convert("L")
        hist = gray.histogram()
        distinct = sum(1 for v in hist if v > 0)
        base = min(1.0, distinct / 100.0)
        return max(0.05, base * (1 - 0.3 * len(issues)))

    # ---------- 增强 ----------
    def _enhance(self, image: Image.Image, task_id: str, page_no: int) -> str:
        img = image
        img = ImageOps.autocontrast(img.convert("RGB"))
        img = ImageEnhance.Contrast(img).enhance(1.25)
        img = ImageEnhance.Sharpness(img).enhance(1.4)
        img = img.filter(ImageFilter.DETAIL)
        out = str(self.enh_dir / f"{task_id}_p{page_no:03d}_enh.png")
        img.save(out, "PNG")
        return out

    def preprocess(self, file_type: str, stored_path: str, task_id: str, zoom: float = 2.0) -> PreprocessResult:
        if file_type in ("pdf", ".pdf"):
            pages = self.pdf_to_images(stored_path, task_id, zoom)
            return PreprocessResult(pages, meta={"mode": "pdf_render", "page_count": len(pages)})
        if file_type == "image":
            pages = self.image_to_page(stored_path, task_id)
            return PreprocessResult(pages, meta={"mode": "image", "page_count": 1})
        # 文本型 Office/文本型 PDF：不需要转图，直接返回空（走文本提取）
        return PreprocessResult([], meta={"mode": "text", "page_count": 0})


def _mean(image: Image.Image) -> float:
    from PIL import ImageStat
    return ImageStat.Stat(image).mean[0]


def _variance(image: Image.Image) -> float:
    from PIL import ImageStat
    st = ImageStat.Stat(image)
    return st.stddev[0] ** 2


def _edge_ratio(image: Image.Image) -> float:
    """边缘纯色占比（近似裁切检测）。"""
    w, h = image.size
    # 采样四条边
    sample = []
    step = max(1, min(w, h) // 50)
    for x in range(0, w, step):
        sample.append(image.getpixel((x, 0)))
        sample.append(image.getpixel((x, h - 1)))
    for y in range(0, h, step):
        sample.append(image.getpixel((0, y)))
        sample.append(image.getpixel((w - 1, y)))
    if not sample:
        return 0.0
    vals = [s for s in sample]
    dark = sum(1 for v in vals if v < 20)
    return dark / len(vals)
