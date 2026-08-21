"""测试：预处理模块（PDF 转图、图片直入、质量检测、增强、文本分流）"""
from pathlib import Path

from PIL import Image

from docmd.preprocess import Preprocessor


def _make_pdf(path: Path) -> None:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Invoice 100.00 Date 2024-01-01")
    doc.save(str(path))
    doc.close()


def _solid_image(path: Path, size=(200, 200), color=200) -> Path:
    img = Image.new("L", size, color)
    img.save(path)
    return path


class TestPreprocess:
    def test_pdf_to_images(self, tmp_path):
        p = tmp_path / "f.pdf"
        _make_pdf(p)
        pre = Preprocessor(str(tmp_path / "data"))
        res = pre.preprocess("pdf", str(p), "task1")
        assert res.page_count == 1
        assert res.pages[0].original != ""
        assert Path(res.pages[0].original).exists()

    def test_image_direct(self, tmp_path):
        img = _solid_image(tmp_path / "scan.png")
        pre = Preprocessor(str(tmp_path / "data"))
        res = pre.preprocess("image", str(tmp_path / "scan.png"), "task1")
        assert res.page_count == 1
        assert res.pages[0].original != ""

    def test_quality_detection_low(self, tmp_path):
        img = _solid_image(tmp_path / "blank.png", color=250)  # 纯色 → low 清晰度
        pre = Preprocessor(str(tmp_path / "data"))
        pages = pre.image_to_page(str(img), "task1")
        assert pages[0].quality in ("low", "crop")
        # 低质量页会生成增强图
        assert pages[0].enhanced is not None

    def test_text_mode_no_pages(self, tmp_path):
        pre = Preprocessor(str(tmp_path / "data"))
        res = pre.preprocess("docx", str(tmp_path / "f.docx"), "task1")
        assert res.page_count == 0
        assert res.meta["mode"] == "text"
