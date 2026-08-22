"""pytest 共享夹具与工具。"""
from __future__ import annotations

import pytest


class StubIndependentVerifier:
    """测试替身：明确模拟“第二个引擎已返回”的独立复核结果。"""
    def recognize_document(self, _stored):
        return {"markdown": (
            "发票 Invoice ABC-2024-001\n"
            "编号: ACCOUNT-2024-567\n"
            "金额: 123,456.00\n"
            "日期: 2024-03-15 客户: 测试公司\n"
            "客户: 张三丰\n"
            "合同交付期: 30天\n"
            "售价 = 100*2.5 + 30\n"
        )}


def make_cjk_pdf(path, lines, page_size=None):
    """生成含真实中文字符的可复制 PDF（使用 PyMuPDF 内建 CJK 字体 'china-s'）。

    PyMuPDF 默认字体不支持中文，会把中文渲染成占位符；须用内建 CJK 字体。
    """
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page() if page_size is None else doc.new_page(width=page_size[0], height=page_size[1])
    page.insert_font(fontname="china-s")
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s", fontsize=12)
        y += 28
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def cjk_pdf(tmp_path):
    return lambda name, lines: make_cjk_pdf(tmp_path / name, lines)
