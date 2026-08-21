"""测试：视觉识别适配器——真实 OCR 结果转统一 ContentBlock，未配置不可用不伪装。"""
import pytest

from docmd.extractors.vision import (
    PaddleOcrAdapter, MineruAdapter, DeepSeekOcrAdapter, MockVisionAdapter,
    VisionUnavailable, make_vision_adapter,
)


class TestPaddleConversion:
    """用模拟的 PaddleOCR 原始结构验证 raw→ContentBlock 转换，无需真实安装。"""

    def _adapter(self):
        return PaddleOcrAdapter()  # 仅调用转换逻辑，不触发真实识别

    def test_parse_standard_item(self):
        adapter = self._adapter()
        # PaddleOCR 2.x 结构：[[box, (text, conf)], ...]
        raw = [[
            [[[100, 50], [200, 50], [200, 80], [100, 80]], ("金额 100.00", 0.98)],
            [[[100, 90], [200, 90], [200, 110], [100, 110]], ("日期 2024-01-01", 0.95)],
        ]]
        blocks, _ = adapter._convert_result(raw, 1)
        assert len(blocks) == 2
        assert any("金额" in b.text for b in blocks)
        assert blocks[0].bbox is not None
        assert blocks[0].bbox.page == 1
        assert blocks[0].bbox.x0 == pytest.approx(100)
        assert blocks[0].bbox.y1 == pytest.approx(80)

    def test_convert_empty(self):
        adapter = self._adapter()
        # 空识别结果：不伪装成功
        blocks, _ = adapter._convert_result([], 1)
        assert blocks == []

    def test_convert_vl_layout_result_preserves_order_and_types(self):
        adapter = self._adapter()
        raw = {"res": {"parsing_res_list": [
            {"block_label": "text", "block_content": "正文", "block_bbox": [10, 30, 200, 80], "block_order": 2, "score": 0.91},
            {"block_label": "doc_title", "block_content": "标题", "block_bbox": [10, 5, 200, 25], "block_order": 1, "score": 0.98},
            {"block_label": "table", "block_content": "| A | B |\n|---|---|\n| 1 | 2 |", "block_bbox": [10, 90, 300, 160], "block_order": 3},
        ]}}
        blocks, lines = adapter._convert_vl_result(raw, 1)
        assert [b.text for b in blocks[:2]] == ["标题", "正文"]
        assert blocks[0].type.value == "heading"
        assert blocks[2].type.value == "table"
        assert blocks[2].bbox.x1 == pytest.approx(300)
        assert "| A | B |" in lines[2]

    def test_flat_item_parse(self):
        adapter = self._adapter()
        box, text, conf = adapter._parse_item(
            [[[0, 0], [10, 0], [10, 5], [0, 5]], ("hello", 0.9)])
        assert text == "hello"
        assert conf == 0.9


class TestUnavailability:
    def test_paddle_unavailable_not_fake(self):
        """未安装 paddle：available=False，识别抛明确错误，绝不伪装。"""
        if PaddleOcrAdapter().available():
            pytest.skip("本机安装了 paddle")
        a = PaddleOcrAdapter()
        assert a.available() is False
        with pytest.raises(VisionUnavailable):
            a.recognize_page("x.png", 1)

    def test_mineru_unavailable_not_fake(self):
        if MineruAdapter().available():
            pytest.skip("本机安装了 mineru")
        a = MineruAdapter()
        assert a.available() is False
        with pytest.raises(VisionUnavailable):
            a.recognize_page("x.png", 1)

    def test_deepseek_never_fake(self):
        a = DeepSeekOcrAdapter()
        assert a.available() is False
        with pytest.raises(VisionUnavailable):
            a.recognize_page("x.png", 1)

    def test_mock_explicit_only(self):
        a = MockVisionAdapter()
        assert a.available() is True
        assert a.is_mock is True
        r = a.recognize_page("x.png", 2)
        assert "MOCK" in r["content"]

    def test_make_adapter_unknown_raises(self):
        with pytest.raises(ValueError):
            make_vision_adapter("nope")
