"""字段级可信度分级模块。

输入：一个字段的多引擎候选结果（EngineResult 列表）。
输出：FieldGrade A/B/C/D 与冲突原因。

规则（需求 §8）：
- 关键字段（金额/日期/姓名/编号/合同条款/表格数字/公式）冲突时不得自动降级为 A/B。
- A：已确认或高度一致（多引擎文本完全一致）
- B：高可信，仅格式差异（归一化后一致；行/列/数字格式差异）
- C：存在候选冲突（候选值不一致）
- D：无法可靠识别（低置信 / 无候选 / 质量不合格）
"""
from __future__ import annotations

from ..core.constants import FieldGrade, KEY_FIELD_TYPES
from ..core.models import EngineResult, Field


def _normalized(c: EngineResult) -> str:
    from ..core.normalize import to_half_width, normalize_spaces
    return normalize_spaces(to_half_width(c.text or "")).strip()


def grade_field(field: Field) -> Field:
    """对字段做可信度分级，返回更新后的副本（原地更新并返回）。"""
    cands = field.candidates
    # A/B 不是“模型自信”的同义词：没有至少两个独立引擎的结果，
    # 不能自动放行到高可信等级。用户已确认的内容才可升为 A。
    if field.user_confirmed is not None:
        field.grade = FieldGrade.A
        field.conflict_reason = "confirmed_by_user"
        return field
    if not cands:
        field.grade = FieldGrade.D
        field.conflict_reason = "无任何识别候选"
        return field

    valid = [c for c in cands if (c.text or "").strip()]
    if not valid:
        field.grade = FieldGrade.D
        field.conflict_reason = "候选均为空"
        return field

    independent_engines = {c.engine for c in valid}
    if len(independent_engines) < 2:
        field.grade = FieldGrade.C
        field.conflict_reason = "仅有单一识别引擎结果，尚未完成独立复核"
        return field

    texts = [_normalized(c) for c in valid]
    unique_norm = set(texts)

    # 完全一致 → A
    if len(unique_norm) == 1:
        confs = [c.confidence for c in valid if c.confidence is not None]
        if confs and min(confs) < 0.5:
            field.grade = FieldGrade.D if field.is_key_field else FieldGrade.C
            field.conflict_reason = "候选一致但置信度过低"
            return field
        field.grade = FieldGrade.A
        field.conflict_reason = ""
        return field

    if _format_difference_only(valid):
        field.grade = FieldGrade.B
        field.conflict_reason = "候选数值一致，仅格式差异（全半角/单位/千分位）"
        return field

    field.grade = FieldGrade.C
    field.conflict_reason = _conflict_summary(valid)
    return field


def _format_difference_only(cands) -> bool:
    def canon(t: str):
        from ..core.normalize import to_half_width
        import re
        t = to_half_width(t or "")
        t = re.sub(r"[,\s]", "", t)
        return t
    bases = {canon(c.text) for c in cands if c.text}
    return len(bases) == 1


def _conflict_summary(cands) -> str:
    parts = []
    for i, c in enumerate(cands):
        conf = f" (conf={c.confidence:.2f})" if c.confidence is not None else ""
        parts.append(f"候选{i+1}({c.engine}): '{c.text}'{conf}")
    return "候选不一致: " + "; ".join(parts)


def grade_all(doc) -> None:
    """对整份文档所有字段分级。"""
    for f in doc.all_fields():
        grade_field(f)


def is_key_field(field_type: str) -> bool:
    return field_type in KEY_FIELD_TYPES
