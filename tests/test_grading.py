"""测试：字段级可信度分级 A/B/C/D。"""
from docmd import grading
from docmd.core.constants import FieldGrade
from docmd.core.models import EngineResult, Field


def _f(cand_texts, field_type="amount", is_key=True):
    f = Field(id="f1", key="金额", field_type=field_type, is_key_field=is_key)
    for i, t in enumerate(cand_texts):
        f.candidates.append(EngineResult(engine=f"e{i}", text=t, confidence=0.9))
    return f


class TestGrading:
    def test_identical_tokens_a(self):
        f = _f(["100.00", "100.00"])
        grading.grade_field(f)
        assert f.grade == FieldGrade.A

    def test_format_diff_b(self):
        # 千分位差异 → 仅格式差异 → B
        f = _f(["123,456.00", "123456.00"])
        grading.grade_field(f)
        assert f.grade == FieldGrade.B

    def test_conflict_c(self):
        f = _f(["100.00", "9,999.00"])
        grading.grade_field(f)
        assert f.grade == FieldGrade.C
        assert f.conflict_reason != ""

    def test_no_candidate_d(self):
        f = Field(id="f1", key="金额", field_type="amount", is_key_field=True)
        grading.grade_field(f)
        assert f.grade == FieldGrade.D

    def test_key_field_conflict_never_a(self):
        # 关键字段冲突不得自动降级 A/B
        f = _f(["100.00", "101.00"], field_type="amount", is_key=True)
        grading.grade_field(f)
        assert f.grade == FieldGrade.C

    def test_single_engine_is_unverified_d(self):
        f = _f(["100.00"])
        grading.grade_field(f)
        assert f.grade == FieldGrade.D
        assert "复核未完成" in f.conflict_reason
