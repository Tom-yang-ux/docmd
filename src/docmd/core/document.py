"""统一文档结构层：Document 与 ContentBlock。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .constants import BlockType, FieldGrade
from .models import BoundingBox, EngineResult, Field


@dataclass
class ContentBlock:
    """统一后的内容块：按页、标题、段落、表格、图片说明、公式、关键字段拆分。"""
    id: str
    type: BlockType
    text: str
    page: Optional[int] = None
    bbox: Optional[BoundingBox] = None
    source_image: Optional[str] = None      # 原图区域路径（可追溯）
    fields: list[Field] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.type, BlockType):
            self.type = BlockType(self.type)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        if self.bbox is not None:
            d["bbox"] = self.bbox.to_dict()
        d["fields"] = [f.to_dict() for f in self.fields]
        return d


class Document:
    """一份文档的完整中间结构。

    字段分级结果保存在 block.fields 中，grade 汇总到 grades 索引。
    status 默认 awaiting_confirmation，仅当用户确认后才置为 confirmed。
    """

    def __init__(self, task_id: str, source_path: str, title: str, blocks: Optional[list[ContentBlock]] = None):
        self.task_id = task_id
        self.source_path = source_path
        self.title = title
        self.blocks: list[ContentBlock] = blocks or []
        self.status: str = "awaiting_confirmation"
        self.created_at: str = ""
        self.engine_version: dict[str, str] = {}

    # ---------- 块与字段 ----------
    def add_block(self, block: ContentBlock) -> None:
        self.blocks.append(block)

    def all_fields(self) -> list[Field]:
        out: list[Field] = []
        for b in self.blocks:
            out.extend(b.fields)
        return out

    def fields_by_grade(self, grade: FieldGrade) -> list[Field]:
        return [f for f in self.all_fields() if f.grade == grade]

    def pending_fields(self) -> list[Field]:
        """待确认字段：C/D 且尚无用户确认值。"""
        return [f for f in self.all_fields()
                if f.grade in (FieldGrade.C, FieldGrade.D) and f.user_confirmed is None]

    def has_pending(self) -> bool:
        """是否存在必须确认的字段（门控判断）。"""
        return len(self.pending_fields()) > 0

    # ---------- Markdown 输出 ----------
    def to_markdown(self, for_confirmation: bool) -> str:
        """生成 Markdown。

        for_confirmation=True → 保留全部内容与 C/D 字段说明，写门控 YAML。
        for_confirmation=False → 用用户确认值回填，标记 confirmed_by_user，删除候选说明。
        """
        lines: list[str] = []
        lines.append(f"# {self.title}")
        lines.append("")
        lines.append(f"> 来源: `{self.source_path}`  |  任务: `{self.task_id}`")
        lines.append("")
        lines.append("---")
        lines.append("")
        if for_confirmation:
            lines.append("```yaml")
            lines.append("document_status: awaiting_confirmation")
            lines.append("processing_gate: user_confirmation_required")
            lines.append("```")
            lines.append("")
            lines.append("## AI 必读规则（不可跳过）")
            lines.append("")
            lines.append("> 本文档含尚未确认的 C/D 级字段。你必须完整阅读全文及文末“待确认清单”，")
            lines.append("> 一次性汇总所有 C/D 项并仅向用户提出确认问题。未取得用户逐项确认前，")
            lines.append("> 禁止总结、分析、推断、生成报告、执行业务任务、导出正式文件或写入知识库。")
        else:
            lines.append("```yaml")
            lines.append("document_status: confirmed")
            lines.append("processing_gate: open")
            lines.append("```")
            lines.append("")
            lines.append("> 本文档已完成用户确认，可直接用于后续 AI 总结、问答、报告与知识处理。")
        lines.append("")

        for block in self.blocks:
            lines.append(self._block_markdown(block, for_confirmation))
            lines.append("")

        if for_confirmation:
            pending = self.pending_fields()
            lines.append("## 待确认清单")
            lines.append("")
            if not pending:
                lines.append("（无待确认项）")
            else:
                for f in pending:
                    cands = "；".join(
                        f"候选{idx+1}({c.engine}): {c.text}"
                        for idx, c in enumerate(f.candidates)
                    )
                    lines.append(
                        f"- `{f.key}`（{f.field_type}）等级 {f.grade.value}，第{f.bbox.page if f.bbox and f.bbox.page else '?'}页 | {cands}"
                    )
                    if f.conflict_reason:
                        lines.append(f"  - 冲突原因: {f.conflict_reason}")
            lines.append("")
        return "\n".join(lines)

    def _block_markdown(self, block: ContentBlock, for_confirmation: bool) -> str:
        if block.type == BlockType.HEADING:
            return f"## {block.text}"
        if block.type == BlockType.TABLE:
            # 文档视觉模型通常已给出 Markdown/HTML 表格；不能因无结构化 rows 而丢失它。
            if not block.meta.get("header") and block.text:
                return self._confirmed_text(block.text, block.fields, for_confirmation)
            header = [self._confirmed_text(str(cell), block.fields, for_confirmation)
                      for cell in block.meta.get("header", [])]
            lines = ["| " + " | ".join(header) + " |"]
            lines.append("|" + "|".join(["---"] * len(block.meta.get("header", []))) + "|")
            for row in block.meta.get("rows", []):
                lines.append("| " + " | ".join(
                    self._confirmed_text(str(c), block.fields, for_confirmation) for c in row) + " |")
            return "\n".join(lines)
        if block.type == BlockType.FORMULA:
            return f"> 公式: `{block.text}`"
        if block.type == BlockType.IMAGE:
            return f"![{block.text}]({block.source_image or ''})"
        # paragraph / key_field
        parts = [self._confirmed_text(block.text, block.fields, for_confirmation)]
        if block.fields:
            for f in block.fields:
                if for_confirmation and f.user_confirmed is None and f.grade in (FieldGrade.C, FieldGrade.D):
                    cands = "；".join(
                        f"候选{i+1}({c.engine}): {c.text} ({c.confidence or '?'})"
                        for i, c in enumerate(f.candidates)
                    )
                    parts.append(
                        f"\n> 🔸 **{f.key}** [{f.grade.value}] 是否确认？{cands}"
                        + (f"\n>    冲突原因: {f.conflict_reason}" if f.conflict_reason else "")
                    )
                else:
                    val = f.user_confirmed if f.user_confirmed is not None else (f.candidates[0].text if f.candidates else "")
                    if for_confirmation:
                        parts.append(f"\n> **{f.key}** = {val} ({f.grade.value})")
        return "\n".join(parts)

    @staticmethod
    def _confirmed_text(text: str, fields: list[Field], for_confirmation: bool) -> str:
        """最终导出时把用户确认值写回正文，而不只留在 evidence.json。"""
        if for_confirmation:
            return text
        rendered = text
        for field in fields:
            if field.user_confirmed is None:
                continue
            # 优先用与用户确认值不同的候选替换；每字段只替换一次，避免将
            # 文档中同值的无关位置全部改掉。无候选时不凭空修改正文。
            for candidate in field.candidates:
                old = candidate.text or ""
                if old and old != field.user_confirmed and old in rendered:
                    rendered = rendered.replace(old, field.user_confirmed, 1)
                    break
        return rendered

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "source_path": self.source_path,
            "title": self.title,
            "status": self.status,
            "engine_version": self.engine_version,
            "blocks": [b.to_dict() for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        doc = cls(data["task_id"], data["source_path"], data["title"])
        doc.status = data.get("status", "awaiting_confirmation")
        doc.engine_version = data.get("engine_version", {})
        for bd in data.get("blocks", []):
            bbox = BoundingBox(**bd["bbox"]) if bd.get("bbox") and any(bd["bbox"].values()) else None
            block = ContentBlock(
                id=bd["id"],
                type=BlockType(bd["type"]),
                text=bd["text"],
                page=bd.get("page"),
                bbox=bbox,
                source_image=bd.get("source_image"),
                meta=bd.get("meta", {}),
            )
            for fd in bd.get("fields", []):
                field = Field(
                    id=fd["id"],
                    key=fd["key"],
                    field_type=fd["field_type"],
                    block_id=fd.get("block_id"),
                    page=fd.get("page"),
                    source_image=fd.get("source_image"),
                    grade=FieldGrade(fd["grade"]),
                    conflict_reason=fd.get("conflict_reason", ""),
                    is_key_field=fd.get("is_key_field", False),
                    user_confirmed=fd.get("user_confirmed"),
                    confirm_source=fd.get("confirm_source", ""),
                    confirmed_at=fd.get("confirmed_at"),
                )
                for c in fd.get("candidates", []):
                    cb = BoundingBox(**c["bbox"]) if c.get("bbox") and any(c["bbox"].values()) else None
                    field.candidates.append(EngineResult(
                        engine=c["engine"], text=c["text"],
                        confidence=c.get("confidence"), bbox=cb,
                    ))
                block.fields.append(field)
            doc.add_block(block)
        return doc
