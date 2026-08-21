"""端到端 pipeline 测试：导入 → 分级 → 待确认 → AI 提问门控 → 真实确认 → 最终导出。"""
from pathlib import Path

import pytest

from docmd.ai import AiAsker
from docmd.core.constants import FieldGrade, TaskState, BlockType
from docmd.core.document import ContentBlock
from docmd.core.models import Field
from docmd.extractors.vision import VisionUnavailable
from docmd.pipeline import Pipeline
from conftest import make_cjk_pdf


def _make_invoice_pdf(path: Path, lines=None) -> None:
    make_cjk_pdf(
        path,
        lines or ["发票 Invoice ABC-2024-001", "金额: 123,456.00", "日期: 2024-03-15 客户: 测试公司"],
    )


def _noop_ai(pipeline) -> AiAsker:
    """返回一个未配置 AI 的 asker（manual 兜底），用于把门控标记设为已提问。"""
    return AiAsker(config={}, db=pipeline.db)


def _mark_ai_and_confirm(pipeline, tid, doc):
    pipeline.ai_question(tid, _noop_ai(pipeline), mode="manual")
    for f in doc.pending_fields():
        val = f.candidates[0].text if f.candidates else "100.00"
        pipeline.confirm_field(tid, f.id, val, "candidate_a")


@pytest.fixture
def pipeline(tmp_path):
    p = Pipeline(str(tmp_path / "data"), vision_engine="mock")
    yield p
    p.close()


class TestPipeline:
    def test_full_flow_clean_text_with_ai_gate(self, tmp_path, pipeline):
        """可复制文本：字段分级 → AI 已提问 → 无待确认 → 可 finalize。"""
        src = tmp_path / "发票.pdf"
        _make_invoice_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        doc = pipeline.process_one(tid)
        assert any(f.field_type == "amount" for f in doc.all_fields())

        # 未做 AI 提问 → finalize 被门控拒绝
        with pytest.raises(RuntimeError):
            pipeline.finalize(tid)
        assert pipeline.db.ai_questioned(tid) is False

        # 单引擎结果必须人工确认后才能通过门控
        _mark_ai_and_confirm(pipeline, tid, doc)
        assert pipeline.db.ai_questioned(tid) is True
        final_path = pipeline.finalize(tid)
        content = Path(final_path).read_text(encoding="utf-8")
        assert "document_status: confirmed" in content
        assert "可直接用于后续 AI" in content
        assert pipeline.db.get_task(tid)["state"] == "done"
        assert (tmp_path / "data" / "output" / f"{tid}_evidence.json").exists()

    def test_review_markdown_embeds_ai_gate_instruction(self, tmp_path, pipeline):
        src = tmp_path / "发票.pdf"
        _make_invoice_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        pipeline.process_one(tid)
        review = Path(pipeline.review_path(tid)).read_text(encoding="utf-8")
        assert "AI 必读规则（不可跳过）" in review
        assert "禁止总结、分析、推断" in review

    def test_finalize_requires_both_gates(self, tmp_path, pipeline):
        """门控：AI 已提问但仍有未确认字段 → 拒绝。"""
        src = tmp_path / "发票.pdf"
        _make_invoice_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        doc = pipeline.process_one(tid)
        # 注入一个待确认字段
        doc.add_block(ContentBlock(id="extra", type=BlockType.PARAGRAPH, text="x", page=1))
        f = Field(id="extra_amt", key="金额", field_type="amount",
                  block_id="extra", is_key_field=True, grade=FieldGrade.D)
        doc.blocks[-1].fields.append(f)
        pipeline.db.save_doc(tid, doc)

        # 即使 AI 已提问，仍有未确认 → 拒绝
        pipeline.ai_question(tid, _noop_ai(pipeline), mode="manual")
        with pytest.raises(RuntimeError):
            pipeline.finalize(tid)

        # 确认全部待确认字段后可通过
        pipeline.confirm_field(tid, "extra_amt", "100.00", "candidate_a")
        saved = pipeline.db.load_doc(tid)
        for item in saved.pending_fields():
            pipeline.confirm_field(tid, item.id, item.candidates[0].text, "candidate_a")
        pipeline.finalize(tid)

    def test_confirm_rejects_placeholder(self, tmp_path, pipeline):
        """确认值禁止占位文本；必须真实值 + 真实来源。"""
        src = tmp_path / "发票.pdf"
        _make_invoice_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        doc = pipeline.process_one(tid)
        # 制造一个待确认字段
        doc.add_block(ContentBlock(id="b", type=BlockType.PARAGRAPH, text="金额: 999.00", page=1))
        f = Field(id="f1", key="金额", field_type="amount", block_id="b",
                  is_key_field=True, grade=FieldGrade.D)
        doc.blocks[-1].fields.append(f)
        pipeline.db.save_doc(tid, doc)

        with pytest.raises(ValueError):
            pipeline.confirm_field(tid, "f1", "手动填写值", "candidate_a")  # 占位
        with pytest.raises(ValueError):
            pipeline.confirm_field(tid, "f1", "", "candidate_a")  # 空
        with pytest.raises(ValueError):
            pipeline.confirm_field(tid, "f1", "100", "auto")  # 非法来源

    def test_ai_marks_questioned(self, tmp_path, pipeline):
        """ai_question 必须持久化门控标记。"""
        src = tmp_path / "发票.pdf"
        _make_invoice_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        pipeline.process_one(tid)
        assert pipeline.db.ai_questioned(tid) is False
        pipeline.ai_question(tid, _noop_ai(pipeline), mode="manual")
        assert pipeline.db.ai_questioned(tid) is True

    def test_scan_pdf_real_engine_unavailable_fails(self, tmp_path):
        """扫描 PDF + 真实引擎(paddle)不可用 → 必须抛错，不产出 mock/空文档。"""
        # 若本机真装了 paddle，则跳过（交给集成环境验证）
        from docmd.extractors.vision import PaddleOcrAdapter
        if PaddleOcrAdapter().available():
            pytest.skip("本机已安装 paddle，跳过不可用分支测试")

        src = tmp_path / "扫描.pdf"
        import pymupdf
        d = pymupdf.open()
        page = d.new_page()
        page.draw_rect(pymupdf.Rect(72, 72, 200, 100), color=(0, 0, 0))
        d.save(str(src)); d.close()

        p = Pipeline(str(tmp_path / "data"), vision_engine="paddle_ocr")
        try:
            tid = p.import_files([str(src)])[0]["task_id"]
            with pytest.raises(VisionUnavailable):
                p.process_one(tid)
        finally:
            p.close()

    def test_import_idempotent_same_task(self, tmp_path, pipeline):
        src = tmp_path / "发票.pdf"
        _make_invoice_pdf(src)
        a = pipeline.import_files([str(src)])
        b = pipeline.import_files([str(src)])
        assert a[0]["task_id"] == b[0]["task_id"]

    def test_cache_text_extraction(self, tmp_path, pipeline):
        """HashCache：同一文件同引擎文本提取只算一次（缓存命中）。"""
        src = tmp_path / "发票.pdf"
        _make_invoice_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        doc1 = pipeline.process_one(tid)
        before = pipeline.cache.count()
        doc2 = pipeline.process_one(tid)
        after = pipeline.cache.count()
        assert len(doc2.blocks) == len(doc1.blocks)
        # 文本提取缓存命中：二次处理不再新增该文件的缓存条目
        assert after == before
        metrics = pipeline.db.metrics(tid)
        assert metrics is not None
        assert metrics["elapsed_seconds"] >= 0

    def test_mock_engine_is_explicit_only(self, tmp_path):
        """mock 引擎用于演示：只为显式选择时可用。"""
        src = tmp_path / "扫描.pdf"
        import pymupdf
        d = pymupdf.open()
        page = d.new_page()
        page.draw_rect(pymupdf.Rect(72, 72, 200, 100), color=(0, 0, 0))
        d.save(str(src)); d.close()
        p2 = Pipeline(str(tmp_path / "data2"), vision_engine="mock")
        try:
            tid = p2.import_files([str(src)])[0]["task_id"]
            doc = p2.process_one(tid, zoom=1.0)
            assert len(doc.blocks) >= 1
            assert any("MOCK" in (b.text or "") for b in doc.blocks)
        finally:
            p2.close()
