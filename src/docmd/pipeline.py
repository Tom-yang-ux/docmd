"""DocMD pipeline 编排：导入 → 预处理 → 识别 → 分级 → 待确认 → 确认 → 最终导出。

对外暴露的核心入口：Pipeline 对象封装数据库、缓存、导入器、预处理器与引擎。

门控规则（不可绕过）：
- 视觉识别仅在真实引擎可用时执行；不可用时抛错并提示安装/配置，不用模拟内容兜底。
- finalize() 必须同时满足：全部待确认字段已由用户明确确认 + AI 已完整汇总提问。
- confirm_field() 要求显式确认值 + 真实来源，拒绝占位文本与自动采用首候选。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Iterable, Optional

from .core.cache import HashCache
from .core.constants import BlockType, FieldGrade, TaskState
from .core.document import ContentBlock, Document
from .core.models import EngineResult, Field
from .extractors.text import get_text_extractor, PdfTextExtractor
from .extractors.vision import MineruAdapter, VisionUnavailable, make_vision_adapter
from .grading import grade_all
from .importer import Importer
from .preprocess import Preprocessor
from .storage.database import Database

# 明确禁止当作确认值的占位文本（用户必须给出真实值）
_PLACEHOLDER_CONFIRM = {"手动填写值", "手动值", "candidate_a", "candidate_b"}


class Pipeline:
    def __init__(self, data_dir: str, db: Optional[Database] = None,
                 vision_engine: str = "paddle_ocr"):
        self.data_dir = Path(data_dir)
        self.out_dir = self.data_dir / "output"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.db = db or Database(str(self.data_dir / "docmd.db"))
        self.cache = HashCache(str(self.data_dir))
        self.importer = Importer(str(self.data_dir))
        self.preprocessor = Preprocessor(str(self.data_dir))
        self.vision_engine = vision_engine
        # vision/vision_adapter 延迟解析；可通过 set_vision_engine 切换
        self.vision = make_vision_adapter(vision_engine)
        self.verifier = MineruAdapter()

    def set_vision_engine(self, engine: str) -> "Pipeline":
        self.vision_engine = engine
        self.vision = make_vision_adapter(engine)
        return self

    def ensure_dual_engine_ready(self, file_type: str, source_path: str | None = None) -> None:
        """在创建任务前验证主识别与独立复核均已就绪。"""
        errors: list[str] = []
        needs_primary_vision = file_type == "image"
        if file_type in {"pdf", ".pdf"} and source_path:
            try:
                needs_primary_vision = not PdfTextExtractor().build_blocks(source_path)
            except Exception:
                # 无法验证 PDF 是否带文本时，按扫描件处理，绝不放行单引擎路径。
                needs_primary_vision = True
        if needs_primary_vision and not self.vision.available():
            errors.append(f"主识别引擎「{self.vision_engine}」不可用：{_engine_hint(self.vision)}")
        verifier_available = getattr(self.verifier, "available", lambda: True)
        if not verifier_available():
            errors.append("独立复核引擎 MinerU 不可用。请在「模型与环境」完成安装后再开始处理。")
        if errors:
            raise VisionUnavailable("\n".join(errors))

    # ---------- 主流程 ----------
    def import_files(self, paths: Iterable[str]) -> list[dict]:
        """导入并登记任务，返回任务摘要列表。"""
        out = []
        for f in self.importer.import_many(paths):
            task = self.db.get_task(f.task_id)
            if task is None:
                self.db.upsert_task(f.task_id, f.source_path, f.title, f.file_type, TaskState.IMPORTED)
            out.append({"task_id": f.task_id, "title": f.title,
                        "file_type": f.file_type, "source": f.source_path})
        return out

    def process_one(self, task_id: str, zoom: float = 2.0) -> Document:
        """对单个任务执行预处理→识别→分级，产出待确认 Document 并保存。"""
        task = self.db.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        # 避免重新处理失败后仍把上一次的审阅稿当成本次可用结果打开。
        self.out_dir.joinpath(f"{task_id}_review_required.md").unlink(missing_ok=True)
        started = time.perf_counter()
        self.db.set_state(task_id, TaskState.PREPROCESSING)
        run = {"pages": 0, "calls": 0, "cache_hits": 0}

        file_type = task["file_type"]
        stored = task["source_path"]
        doc = Document(task_id, stored, task["title"])

        self.db.set_state(task_id, TaskState.RECOGNIZING)
        text_blocks: list[ContentBlock] = []
        extractor = get_text_extractor(file_type)
        if extractor is not None:
            text_blocks = self._extract_text_cached(stored, file_type, extractor)

        # 判断是否需要视觉识别
        pdf_scanned = False
        if file_type in ("pdf", ".pdf"):
            pdf_scanned = not text_blocks and PdfTextExtractor().is_scanned(stored)
        needs_visual = (file_type == "image") or pdf_scanned

        if needs_visual:
            pre = self.preprocessor.preprocess(file_type, stored, task_id, zoom)
            run["pages"] = len(pre.pages)
            run.update(self._apply_visual(doc, pre.pages, stored))
        else:
            self.db.set_state(task_id, TaskState.GRADING)
            for b in text_blocks:
                b.meta["engine"] = "text_extractor"
                doc.add_block(b)

        self.db.set_state(task_id, TaskState.GRADING)
        verifier_blocks, verifier_error = self._run_independent_verifier(stored, task_id)
        # 双引擎是处理前提，不是给 C/D 审阅稿补充的一项可选信息。
        # 第二引擎未运行、失败或返回空内容时，本次任务必须失败，不能输出
        # review_required.md，更不能把主引擎的结果伪装成“待用户确认”。
        if verifier_error:
            self.db.set_state(task_id, TaskState.RECOGNIZING)
            raise VisionUnavailable(f"独立复核失败，已停止输出：{verifier_error}")
        self._extract_fields(doc, verifier_blocks, verifier_error)
        grade_all(doc)
        doc.status = "awaiting_confirmation"
        self.db.save_doc(task_id, doc)
        self.db.set_state(task_id, TaskState.AWAITING_CONFIRM)
        self.db.save_metrics(
            task_id, page_count=run["pages"], elapsed_seconds=round(time.perf_counter() - started, 3),
            model_calls=run["calls"] + (1 if verifier_blocks else 0), cache_hits=run["cache_hits"],
            engine=f"{self.vision_engine}+mineru",
        )

        self._write_review(task_id, doc)
        return doc

    def _apply_visual(self, doc: Document, pages, stored: str) -> dict[str, int]:
        """调用视觉适配器识别每一页；真实引擎不可用时立即抛错，不产出模拟/空结果。

        使用 HashCache：同一图片+同一引擎+同一配置不重复识别。
        """
        stats = {"calls": 0, "cache_hits": 0}
        for pi in pages:
            img = pi.enhanced or pi.original
            if not self.vision.available():
                raise VisionUnavailable(
                    f"视觉识别引擎「{self.vision_engine}」不可用：{_engine_hint(self.vision)}")
            cache_key = self.cache.key_for(img, self.vision_engine,
                                           config_fp=self._engine_config_fp())
            cached = self.cache.get(cache_key)
            if cached is not None:
                blocks = self._blocks_from_cached(cached, pi.index)
                if not blocks:
                    cached = None
                else:
                    stats["cache_hits"] += 1
            if cached is None:
                try:
                    res = self.vision.recognize_page(img, pi.index, {"quality": pi.quality})
                except VisionUnavailable:
                    raise
                except Exception as e:  # noqa: BLE001
                    raise VisionUnavailable(
                        f"视觉识别失败（页 {pi.index}）: {e}") from e
                blocks = res.get("blocks") or []
                stats["calls"] += 1
                if not blocks:
                    raise VisionUnavailable(
                        f"视觉识别在页面 {pi.index} 未产出内容块，无法可靠处理。")
                # 缓存序列化结果
                self.cache.put(cache_key, {
                    "blocks": [b.to_dict() for b in blocks],
                })
            for b in blocks:
                b.page = pi.index
                b.meta["quality"] = pi.quality
                b.meta["source_image"] = pi.original
                b.meta["engine"] = self.vision_engine
                doc.add_block(b)
        return stats

    def _run_independent_verifier(self, stored: str, task_id: str) -> tuple[list[ContentBlock], str]:
        """执行第二个项目 MinerU，失败即中止任务，不产出审阅稿。

        mock 是测试专用引擎；正式任务和测试任务都必须提供一个独立复核结果。
        调用方收到错误后必须中止，不能继续分级或导出。
        """
        if getattr(self.vision, "is_mock", False):
            return [], "演示引擎不执行真实双引擎复核"
        try:
            result = self.verifier.recognize_document(stored)
            md = result["markdown"]
            raw_path = self.out_dir / f"{task_id}_mineru_raw.md"
            raw_path.write_text(md, encoding="utf-8")
            blocks = self._markdown_blocks(md, "mineru")
            if not blocks:
                return [], "MinerU 返回内容为空，未完成独立复核"
            return blocks, ""
        except VisionUnavailable as exc:
            return [], str(exc)

    @staticmethod
    def _markdown_blocks(markdown: str, engine: str) -> list[ContentBlock]:
        """把第二引擎 Markdown 规整为仅供字段比对的块。"""
        blocks: list[ContentBlock] = []
        paragraph: list[str] = []
        index = 0
        def emit() -> None:
            nonlocal index, paragraph
            text = "\n".join(paragraph).strip()
            paragraph = []
            if text:
                index += 1
                blocks.append(ContentBlock(id=f"{engine}_b{index}", type=BlockType.PARAGRAPH,
                    text=text, page=1, meta={"engine": engine, "confidence": 0.85}))
        for line in markdown.splitlines():
            if not line.strip():
                emit()
            else:
                paragraph.append(line)
        emit()
        return blocks

    def _engine_config_fp(self) -> str:
        """引擎配置指纹：用于缓存键，确保不同配置不串缓存。"""
        return repr(getattr(self.vision, "config_fingerprint", "") or self.vision_engine)

    def _extract_text_cached(self, stored: str, file_type: str, extractor) -> list[ContentBlock]:
        """文本提取结果按 文件内容+引擎 缓存，同文件同引擎不重复解析。"""
        key = self.cache.key_for(stored, extractor.engine, file_type)
        cached = self.cache.get(key)
        if cached is not None:
            return self._blocks_from_cached(cached, 1)
        blocks = extractor.build_blocks(stored)
        self.cache.put(key, {"blocks": [b.to_dict() for b in blocks]})
        return blocks

    @staticmethod
    def _blocks_from_cached(cached: dict, page: int) -> list[ContentBlock]:
        from .core.document import ContentBlock as _CB
        from .core.models import BoundingBox
        blocks = []
        for bd in cached.get("blocks", []):
            bbox = BoundingBox(**bd["bbox"]) if bd.get("bbox") and any(bd["bbox"].values()) else None
            blocks.append(_CB(
                id=bd["id"], type=BlockType(bd["type"]), text=bd["text"],
                page=bd.get("page") or page, bbox=bbox,
                source_image=bd.get("source_image"), meta=bd.get("meta", {}),
            ))
        return blocks

    # ---------- 字段对齐与分级 ----------
    # 关键字段识别规则（金额/日期/姓名/编号/合同条款/表格数字/公式）
    _KEY_EXTRACTORS = {
        "amount": [
            r"(?:金额|价[格款]|合计|总价|货款|总金额)[:：]?\s*([¥￥$]?\s*[0-9][0-9,]*\.?[0-9]*)",
            r"\$?\s*([0-9][0-9,]*\.[0-9]{2})\s*(?:元|美元)?",
        ],
        "date": [
            r"(?:日期|时间|Date)[:：]?\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)",
        ],
        "number_id": [
            r"(?:编号|单号|号码|No\.?|Invoice\s*#?)[:：]?\s*([A-Z0-9][A-Z0-9-]{2,})",
        ],
        "person_name": [
            r"(?:客户|甲方|乙方|收货人|经办人|联系人)[:]?[:：]?\s*([\u4e00-\u9fa5]{2,6})",
        ],
        "contract_term": [
            r"(?:合同|条款|交付期|账期|质保)[:]?[:：]?\s*([0-9一二三四五六七八九十百]+[天年月日天月])",
        ],
        "formula": [
            r"[=＝]\s*([A-Za-z0-9_+\-*/^().,%\s]{4,})",
        ],
    }
    _KEY_NAMES = {
        "amount": "金额", "date": "日期", "number_id": "编号",
        "person_name": "姓名", "contract_term": "合同条款", "formula": "公式",
    }

    def _extract_fields(self, doc: Document, verifier_blocks: list[ContentBlock] | None = None,
                        verifier_error: str = "") -> None:
        """按类型、页码、出现次序对齐两份独立结果，形成一个多候选字段。"""
        grouped: dict[tuple[str, int, int], Field] = {}
        occurrences: dict[tuple[str, str, int], int] = {}
        # 主结果必须是正文块；第二结果只参与比对，不能重复输出到 Markdown。
        for b in [*doc.blocks, *(verifier_blocks or [])]:
            if b.type not in (BlockType.PARAGRAPH, BlockType.TABLE, BlockType.KEY_FIELD):
                continue
            page = b.page or 1
            text = self._field_text(b)
            engine = str(b.meta.get("engine") or self.vision_engine)
            for ftype, patterns in self._KEY_EXTRACTORS.items():
                for pat in patterns:
                    for m in re.finditer(pat, text):
                        val = m.group(1).strip()
                        if not val:
                            continue
                        occurrence_key = (engine, ftype, page)
                        nth = occurrences.get(occurrence_key, 0) + 1
                        occurrences[occurrence_key] = nth
                        key = (ftype, page, nth)
                        f = grouped.get(key)
                        if f is None:
                            f = Field(id=f"field_{ftype}_p{page}_{nth}", key=self._KEY_NAMES[ftype],
                                field_type=ftype, block_id=b.id, is_key_field=True,
                                grade=FieldGrade.D, page=page,
                                source_image=b.meta.get("source_image"))
                            grouped[key] = f
                            # 只把字段标注写回主文档块。
                            if b in doc.blocks:
                                b.fields.append(f)
                        confidence = b.meta.get("confidence", 0.95)
                        try:
                            confidence = float(confidence)
                        except (TypeError, ValueError):
                            confidence = 0.5
                        candidate = EngineResult(engine=engine, text=val, confidence=confidence,
                                                 bbox=b.bbox)
                        if not any(c.engine == candidate.engine and c.text == candidate.text for c in f.candidates):
                            f.candidates.append(candidate)
        # MinerU may find fields absent from primary: keep them visible as D in review.
        for f in grouped.values():
            if f.block_id and any(b.id == f.block_id for b in doc.blocks):
                continue
            holder = ContentBlock(id=f"unmatched_{f.id}", type=BlockType.KEY_FIELD,
                                  text=f"[仅第二引擎发现] {f.key}", page=f.page)
            holder.fields.append(f)
            doc.add_block(holder)
        if verifier_error:
            doc.engine_version["mineru_verification"] = f"failed: {verifier_error}"
            for f in doc.all_fields():
                if len({c.engine for c in f.candidates}) < 2:
                    f.conflict_reason = f"第二引擎复核未完成：{verifier_error}"
        else:
            doc.engine_version["mineru_verification"] = "completed"

    @staticmethod
    def _field_text(block: ContentBlock) -> str:
        """正文与表格数据均进入关键字段检查，不能只检查“工作表名称”。"""
        if block.type != BlockType.TABLE:
            return block.text
        rows = [block.meta.get("header", []), *block.meta.get("rows", [])]
        table_text = "\n".join(" | ".join(str(v) for v in row) for row in rows)
        return "\n".join(part for part in (block.text, table_text) if part)

    # ---------- 确认（真实用户确认） ----------
    def confirm_field(self, task_id: str, field_id: str, user_value: str,
                      source: str) -> None:
        """写入用户确认值，并登记确认日志。

        source 必须是 'candidate_a' / 'candidate_b' / 'manual' 之一。
        user_value 不得为空、不得是占位文本。
        """
        value = (user_value or "").strip()
        if not value:
            raise ValueError("确认值不能为空：用户必须给出明确值")
        if value in _PLACEHOLDER_CONFIRM:
            raise ValueError("确认值为占位文本，未获得真实确认，已拒绝。")
        if source not in ("candidate_a", "candidate_b", "manual"):
            raise ValueError(f"非法确认来源: {source}")

        doc = self.db.load_doc(task_id)
        if doc is None:
            raise KeyError(task_id)
        target = None
        for f in doc.all_fields():
            if f.id == field_id:
                target = f
                break
        if target is None:
            raise KeyError(field_id)

        target.user_confirmed = value
        target.confirm_source = source
        from datetime import datetime, timezone
        target.confirmed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        self.db.append_confirm(
            task_id, target.key, target.field_type, str(target.grade.value),
            [c.text for c in target.candidates], value, source)
        self.db.save_doc(task_id, doc)

    def ai_question(self, task_id: str, ai: "AiAsker", mode: str = "auto") -> dict:
        """让 AI 完整阅读 review_required.md 并汇总提问；成功后持久化门控标记。

        返回 {"payload": str/questions, "engine": str}
        """
        review_path = self.review_path(task_id)
        text = Path(review_path).read_text(encoding="utf-8")
        result = ai.ask(text, mode=mode)
        self.db.set_ai_questioned(task_id, True)
        return {"questions": result.questions, "engine": result.engine}

    def finalize(self, task_id: str) -> str:
        """导出最终 Markdown 与 evidence.json。

        门控：必须
        1) 所有待确认字段已确认（无 C/D 残留）；
        2) AI 已完整汇总提问（任务 ai_questioned=True）。
        任一未满足即抛 RuntimeError，不允许绕过。
        """
        doc = self.db.load_doc(task_id)
        if doc is None:
            raise KeyError(task_id)
        if doc.has_pending():
            raise RuntimeError("存在未确认字段，禁止进入最终文件生成")
        if not self.db.ai_questioned(task_id):
            raise RuntimeError("未完成「AI 汇总提问」，禁止生成最终文件（确认前门控）")

        doc.status = "confirmed"
        final_md = self._render_final(doc)
        fd = self.out_dir / f"{task_id}_final.md"
        fd.write_text(final_md, encoding="utf-8")
        ev = self.out_dir / f"{task_id}_evidence.json"
        ev.write_text(_json(doc.to_dict()), encoding="utf-8")
        self.db.save_doc(task_id, doc)
        self.db.set_state(task_id, TaskState.CONFIRMED)
        self.db.set_state(task_id, TaskState.DONE)
        return str(fd)

    def _render_final(self, doc: Document) -> str:
        return doc.to_markdown(for_confirmation=False)

    def _write_review(self, task_id: str, doc: Document) -> str:
        md = doc.to_markdown(for_confirmation=True)
        p = self.out_dir / f"{task_id}_review_required.md"
        p.write_text(md, encoding="utf-8")
        return str(p)

    def review_path(self, task_id: str) -> str:
        return str(self.out_dir / f"{task_id}_review_required.md")

    def final_path(self, task_id: str) -> str:
        return str(self.out_dir / f"{task_id}_final.md")

    # ---------- 缓存 ----------
    def _cache(self) -> HashCache:
        return self.cache

    def close(self):
        self.db.close()


def _engine_hint(adapter) -> str:
    from .extractors.vision import MISSING_PADDLE, MISSING_MINERU, MISSING_DEEPSEEK
    engine = getattr(adapter, "engine", str(adapter))
    return {
        "paddle_ocr": MISSING_PADDLE,
        "mineru": MISSING_MINERU,
        "deepseek_ocr": MISSING_DEEPSEEK,
    }.get(engine, "请安装并配置对应的真实 OCR 引擎；不能在真实识别不可用时用模拟或空结果代替。") if not getattr(adapter, "is_mock", False) else "（演示引擎可用，但非真实 OCR）"


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
