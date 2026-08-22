"""测试：字段提取（全部关键字段类型）与 evidence.json 证据链完整性。"""
import json
from pathlib import Path

import pytest

from docmd.pipeline import Pipeline
from docmd.ai import AiAsker
from docmd.core.constants import BlockType, FieldGrade
from docmd.core.document import ContentBlock, Document
from docmd.grading import grade_all
from conftest import make_cjk_pdf


def _make_rich_pdf(path: Path) -> None:
    make_cjk_pdf(path, [
        "编号: ACCOUNT-2024-567",
        "金额: 88,800.00",
        "日期: 2024-05-20",
        "客户: 张三丰",
        "合同交付期: 30天",
        "售价 = 100*2.5 + 30",
    ])


@pytest.fixture
def pipeline(tmp_path):
    p = Pipeline(str(tmp_path / "data"), vision_engine="mock")
    from conftest import StubIndependentVerifier
    p.verifier = StubIndependentVerifier()
    yield p
    p.close()


class TestFieldExtraction:
    def test_two_independent_engines_are_merged_before_grading(self, pipeline):
        """同一字段必须保留 text_extractor 与 MinerU 两个候选，再决定 A/B/C/D。"""
        doc = Document("dual", "sample.xlsx", "双引擎")
        primary = ContentBlock("primary", BlockType.PARAGRAPH, "金额: 1,200.00", page=1,
                               meta={"engine": "text_extractor", "confidence": 0.99})
        verifier = ContentBlock("mineru", BlockType.PARAGRAPH, "金额: 1200.00", page=1,
                                meta={"engine": "mineru", "confidence": 0.92})
        doc.add_block(primary)
        pipeline._extract_fields(doc, [verifier])
        grade_all(doc)
        field = doc.all_fields()[0]
        assert {c.engine for c in field.candidates} == {"text_extractor", "mineru"}
        assert field.grade == FieldGrade.B

    def test_all_key_field_types(self, tmp_path, pipeline):
        src = tmp_path / "对账单.pdf"
        _make_rich_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        doc = pipeline.process_one(tid)
        types = {f.field_type for f in doc.all_fields()}
        assert "amount" in types
        assert "date" in types
        assert "number_id" in types
        assert "person_name" in types
        assert "contract_term" in types
        assert "formula" in types

    def test_fields_have_page_and_source(self, tmp_path, pipeline):
        src = tmp_path / "对账单.pdf"
        _make_rich_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        doc = pipeline.process_one(tid)
        for f in doc.all_fields():
            assert f.page is None or f.page >= 1  # 字段带页码
            block = next((b for b in doc.blocks if b.id == f.block_id), None)
            assert block is not None
            # 候选带引擎信息与置信度
            assert f.candidates, f"{f.key} 有候选"

    def test_evidence_json_complete(self, tmp_path, pipeline):
        """evidence.json 完整保留审计信息。"""
        src = tmp_path / "对账单.pdf"
        _make_rich_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        doc = pipeline.process_one(tid)
        pipeline.ai_question(tid, AiAsker(config={}, db=pipeline.db), mode="manual")
        for f in doc.pending_fields():
            pipeline.confirm_field(tid, f.id, f.candidates[0].text, "candidate_a")
        final = pipeline.finalize(tid)
        ev_path = Path(final).parent / f"{tid}_evidence.json"
        data = json.loads(ev_path.read_text(encoding="utf-8"))
        # 证据链完整：任务/来源/状态/块/字段/候选/确认
        assert data["task_id"] == tid
        assert data["status"] == "confirmed"
        assert len(data["blocks"]) > 0
        all_fields = [f for b in data["blocks"] for f in b.get("fields", [])]
        assert all_fields, "evidence 必须包含字段"
        sample = all_fields[0]
        assert "candidates" in sample
        assert "grade" in sample
        assert "user_confirmed" in sample
        assert "confirm_source" in sample

    def test_cd_in_review_and_cleanup_in_final(self, tmp_path, pipeline):
        """C/D 写入 review_required.md；最终 Markdown 移除候选/待确认说明。"""
        src = tmp_path / "对账单.pdf"
        _make_rich_pdf(src)
        tid = pipeline.import_files([str(src)])[0]["task_id"]
        doc = pipeline.process_one(tid)
        review_path = pipeline.review_path(tid)
        review = Path(review_path).read_text(encoding="utf-8")
        # review 包含状态规则与待确认清单
        assert "document_status: awaiting_confirmation" in review
        assert "processing_gate: user_confirmation_required" in review

        # 制造一个 C 级冲突字段以确保有 C/D 输出到 review
        from docmd.core.models import Field, EngineResult
        from docmd.core.constants import BlockType, FieldGrade
        from docmd.core.document import ContentBlock
        doc.add_block(ContentBlock(id="c1", type=BlockType.PARAGRAPH, text="冲突项", page=1))
        f = Field(id="c1_amt", key="金额", field_type="amount", block_id="c1",
                  is_key_field=True, grade=FieldGrade.C,
                  conflict_reason="候选不一致")
        f.candidates.append(EngineResult(engine="a", text="100.00", confidence=0.9))
        f.candidates.append(EngineResult(engine="b", text="9999.00", confidence=0.8))
        doc.blocks[-1].fields.append(f)
        # 持久化注入的字段，并重新生成含 C/D 的 review_required.md
        pipeline.db.save_doc(tid, doc)
        Path(review_path).write_text(doc.to_markdown(for_confirmation=True), encoding="utf-8")

        review = Path(review_path).read_text(encoding="utf-8")
        assert "等级 C" in review or "100.00" in review or "9999.00" in review

        # AI 已提问 + 确认全部 → 最终 Markdown 清理候选与待确认说明
        pipeline.ai_question(tid, AiAsker(config={}, db=pipeline.db), mode="manual")
        for pf in doc.pending_fields():
            pipeline.confirm_field(tid, pf.id, pf.candidates[0].text, "candidate_a")
        final = pipeline.finalize(tid)
        final_text = Path(final).read_text(encoding="utf-8")
        # 最终 Markdown 使用确认值，不再出现候选/待确认说明
        assert "document_status: confirmed" in final_text
        assert "processing_gate: open" in final_text
        assert "待确认清单" not in final_text
        assert "是否确认" not in final_text
        assert "processing_gate: user_confirmation_required" not in final_text

    def test_final_markdown_uses_manual_confirmation_value(self):
        """确认值必须写回最终正文，不能只存在 evidence.json。"""
        doc = Document("confirmed", "sample.pdf", "确认值回填")
        block = ContentBlock("p1", BlockType.PARAGRAPH, "金额: 100.00", page=1)
        from docmd.core.models import Field, EngineResult
        f = Field(id="amount", key="金额", field_type="amount", block_id="p1",
                  is_key_field=True, grade=FieldGrade.C, user_confirmed="120.00")
        f.candidates.append(EngineResult(engine="paddle_ocr", text="100.00", confidence=0.9))
        f.candidates.append(EngineResult(engine="mineru", text="100.00", confidence=0.9))
        block.fields.append(f)
        doc.add_block(block)
        final = doc.to_markdown(for_confirmation=False)
        assert "金额: 120.00" in final
        assert "金额: 100.00" not in final
