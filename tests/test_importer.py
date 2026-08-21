"""测试：导入模块（类型识别、分流、副本落盘）"""
from pathlib import Path

from docmd.importer import Importer, ImportedFile


def _make_pdf(path: Path) -> None:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello World Invoice 100.00")
    page.insert_text((72, 100), "Date: 2024-01-01 Company ABC")
    doc.save(str(path))
    doc.close()


class TestImporter:
    def test_detect_type(self):
        assert Importer.detect_type("a.pdf") == "pdf"
        assert Importer.detect_type("a.docx") == "docx"
        assert Importer.detect_type("a.xlsx") == "xlsx"
        assert Importer.detect_type("a.pptx") == "pptx"
        assert Importer.detect_type("a.png") == "image"
        assert Importer.detect_type("a.xyz") == "unknown"

    def test_needs_vision(self):
        assert Importer.needs_vision("image") is True
        # 文本型 Office/PDF 默认走文本提取
        assert Importer.needs_vision("pdf") is False
        assert Importer.needs_vision("docx") is False

    def test_import_pdf(self, tmp_path):
        src = tmp_path / "产品报价.pdf"
        _make_pdf(src)
        imp = Importer(str(tmp_path / "data"))
        f = imp.import_file(str(src))
        assert f.task_id.startswith("task_")
        assert f.file_type == "pdf"
        assert f.title == "产品报价"
        # 副本已落盘
        assert (tmp_path / "data" / "raw" / f.stored_name).exists()
        # 再次导入同一文件不重复拷贝
        f2 = imp.import_file(str(src))
        assert f2.copied is False

    def test_detect_unknown(self, tmp_path):
        src = tmp_path / "readme.md"
        src.write_text("# hi", encoding="utf-8")
        imp = Importer(str(tmp_path / "data"))
        f = imp.import_file(str(src))
        assert f.file_type == "unknown"
