"""视觉识别适配器（真实 OCR）。

原则：
- PaddleOCR 结果必须转换为统一 ContentBlock（含 bbox、页码、来源图片），不得返回空 blocks。
- 未安装/未配置时，available()=False，recognize_page() 抛明确异常，绝不伪装成已识别。
- 扫描 PDF / 图片在真实 OCR 不可用时，由 pipeline 向上抛出并提示安装/配置，不产出模拟内容或空文档。
- MinerU、DeepSeek 保留适配器接口；未配置时明确不可用。
"""
from __future__ import annotations

import os
import re
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from ..core.constants import BlockType, EngineName
from ..core.document import ContentBlock
from ..core.models import BoundingBox
from ..core.normalize import normalize
from .base import BaseVisionAdapter

# 明确、可操作的缺失说明
MISSING_PADDLE = (
    "PaddleOCR-VL 文档解析管线未安装。请执行:\n"
    "  pip install -U paddleocr paddlepaddle paddlex\n"
    "然后重新运行/打包。未接通前，扫描件与图片无法真实识别。"
)
MISSING_MINERU = "MinerU 复核引擎未安装或未配置：`pip install mineru`。未接通前不伪装。"
MISSING_DEEPSEEK = "DeepSeek-OCR 需在配置中启用并设置 API；未配置时不可用。"


class VisionUnavailable(Exception):
    """真实视觉识别不可用。"""


class MockVisionAdapter(BaseVisionAdapter):
    """显式演示引擎：仅当调用方显式选择 mock 时启用，绝不静默兜底。"""
    engine = "mock_vision"
    is_mock = True
    mock = True

    def available(self) -> bool:
        return True

    def recognize_page(self, image_path: str, page: int, extra: Optional[dict] = None) -> dict:
        # 明确标注这是模拟，绝不与真实 OCR 混淆
        content = (
            f"[MOCK-DEMO 模拟识别，非真实 OCR] 页 {page} 示例内容\n"
            f"金额: 100.00\n日期: 2024-01-01"
        )
        blocks = [
            ContentBlock(id=f"mock_p{page}_b1", type="paragraph", text=content,
                         page=page, bbox=BoundingBox(page=page)),
        ]
        return {"content": content, "blocks": blocks, "raw": {"engine": self.engine, "mock": True},
                "confidence": 0.5, "mock": True}


class PaddleOcrAdapter(BaseVisionAdapter):
    """主视觉解析模型 PaddleOCR-VL；把原始输出转为统一 ContentBlock。"""
    engine = EngineName.PADDLE_OCR.value
    is_mock = False

    def __init__(self):
        self._pipeline = None
        self.config_fingerprint = "PaddleOCR-VL-1.6:layout+markdown"

    @staticmethod
    def _sidecar_python() -> Path | None:
        """Find the optional, user-local OCR runtime for both real engines."""
        candidates = []
        if os.environ.get("DOCMD_OCR_PYTHON"):
            candidates.append(Path(os.environ["DOCMD_OCR_PYTHON"]))
        # Optional runtime installed after the user enables dual-engine OCR.
        appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        candidates.append(appdata / "docmd" / "ocr-runtime" / "Scripts" / "python.exe")
        # Development layout.
        candidates.append(Path(__file__).resolve().parents[3] / ".venv-ocr" / "Scripts" / "python.exe")
        return next((item for item in candidates if item.is_file()), None)

    def available(self) -> bool:
        try:
            from paddleocr import PaddleOCRVL  # noqa: F401
            return True
        except Exception:
            try:
                from paddlex import create_pipeline  # noqa: F401
                return True
            except Exception:
                return self._sidecar_python() is not None

    def _require(self) -> None:
        if not self.available():
            raise VisionUnavailable(MISSING_PADDLE)

    def recognize_page(self, image_path: str, page: int, extra: Optional[dict] = None) -> dict:
        self._require()
        raw = self._predict_vl(os.fspath(image_path))
        blocks, content_lines = self._convert_vl_result(raw, page)
        if not blocks:
            raise VisionUnavailable(
                f"PaddleOCR-VL 在页面 {page} 未识别到任何内容块，无法产出可靠内容。")
        return {
            "content": "\n".join(content_lines),
            "blocks": blocks,
            "raw": raw,
            "confidence": self._estimate_confidence(raw),
        }

    def _predict_vl(self, image_path: str):
        """调用完整 PaddleOCR-VL 管线，而非传统逐行 PaddleOCR。

        优先使用 PaddleOCR 3.x 的 PaddleOCRVL；其次兼容 PaddleX 的
        `create_pipeline(pipeline="PaddleOCR-VL-1.6")`。模型只在首次调用时加载。
        """
        if self._pipeline is None and self._sidecar_python() is not None and not self._direct_runtime_available():
            return self._predict_in_sidecar(image_path)
        if self._pipeline is None:
            try:
                from paddleocr import PaddleOCRVL
                self._pipeline = ("paddleocr", PaddleOCRVL())
            except ImportError:
                from paddlex import create_pipeline
                self._pipeline = ("paddlex", create_pipeline(pipeline="PaddleOCR-VL-1.6"))
        _, pipeline = self._pipeline
        output = pipeline.predict(image_path)
        result = next(iter(output), None)
        if result is None:
            return {}
        return self._result_mapping(result)

    @staticmethod
    def _direct_runtime_available() -> bool:
        try:
            from paddleocr import PaddleOCRVL  # noqa: F401
            return True
        except Exception:
            return False

    def _predict_in_sidecar(self, image_path: str) -> dict:
        runtime = self._sidecar_python()
        helper = Path(__file__).with_name("ocr_sidecar.py")
        if runtime is None or not helper.is_file():
            raise VisionUnavailable(MISSING_PADDLE)
        result = subprocess.run(
            [str(runtime), str(helper), image_path], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=900, check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "unknown sidecar error")[-2000:]
            raise VisionUnavailable(f"PaddleOCR-VL 运行失败：{detail}")
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError as exc:
            raise VisionUnavailable("PaddleOCR-VL 返回格式异常，未生成可信结果。") from exc

    @staticmethod
    def _result_mapping(result):
        """把 Paddle 结果对象转换为可序列化映射，兼容 3.x/PaddleX 返回类型。"""
        if isinstance(result, dict):
            return result
        for attr in ("json", "res"):
            value = getattr(result, attr, None)
            if callable(value):
                value = value()
            if isinstance(value, dict):
                return value
        if hasattr(result, "to_dict"):
            value = result.to_dict()
            if isinstance(value, dict):
                return value
        return {}

    def _convert_vl_result(self, raw, page: int):
        """将完整文档管线的 reading-order blocks 转换为统一结构。"""
        root = raw.get("res", raw) if isinstance(raw, dict) else {}
        items = root.get("parsing_res_list") or root.get("parsing_res") or []
        blocks, lines = [], []
        for index, item in enumerate(sorted(items, key=lambda x: (x.get("block_order") is None, x.get("block_order") or 0)), start=1):
            text = str(item.get("block_content") or "").strip()
            if not text:
                continue
            coords = item.get("block_bbox") or item.get("coordinate") or []
            bbox = self._bbox_from_flat(coords, page)
            label = str(item.get("block_label") or item.get("label") or "text")
            block_type = BlockType.PARAGRAPH
            if label in {"doc_title", "paragraph_title", "title"}:
                block_type = BlockType.HEADING
            elif "table" in label:
                block_type = BlockType.TABLE
            elif "formula" in label:
                block_type = BlockType.FORMULA
            score = item.get("score")
            try:
                score = float(score) if score is not None else 0.5
            except (TypeError, ValueError):
                score = 0.5
            blocks.append(ContentBlock(
                id=f"paddle_vl_p{page}_b{index}", type=block_type, text=normalize(text),
                page=page, bbox=bbox,
                meta={"layout_label": label, "block_order": item.get("block_order"), "confidence": score},
            ))
            lines.append(text)
        return blocks, lines

    @staticmethod
    def _bbox_from_flat(coords, page: int):
        if not isinstance(coords, (list, tuple)) or len(coords) < 4:
            return None
        try:
            values = [float(x) for x in coords[:4]]
        except (TypeError, ValueError):
            return None
        return BoundingBox(page=page, x0=values[0], y0=values[1], x1=values[2], y1=values[3])

    # ---------- 结果转换 ----------
    def _convert_result(self, raw, page: int):
        """把 PaddleOCR 原始输出解析为统一 ContentBlock。

        兼容两种常见形态：
        1) PaddleOCR 2.x：result = [ [ [box, (text, conf)], ... ] ]  （可能嵌套第一层页）
        2) PaddleOCR 3.x：result = [ box, conf, [text, score]? ] 或 dict
        逐条生成 PARAGRAPH 块，绑定 bbox（四角坐标 → x0/y0/x1/y1）。
        """
        blocks: list[ContentBlock] = []
        lines: list[str] = []
        items = self._flatten_items(raw)

        for idx, item in enumerate(items, start=1):
            box, text, conf = self._parse_item(item)
            if text is None or not str(text).strip():
                continue
            text = normalize(str(text)).strip()
            lines.append(text)
            bbox = None
            box = _normalize_polygon(box)
            if box:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                bbox = BoundingBox(page=page, x0=float(min(xs)), y0=float(min(ys)),
                                   x1=float(max(xs)), y1=float(max(ys)))
            blocks.append(ContentBlock(
                id=f"ocr_p{page}_b{idx}", type="paragraph", text=text,
                page=page, bbox=bbox,
            ))
        return blocks, lines

    @staticmethod
    def _flatten_items(raw, _depth: int = 0):
        """兼容多页/单页嵌套，摊平到一列候选 item；带深度保护避免恶意深嵌套。"""
        if _depth > 8:
            return []
        out = []
        if isinstance(raw, dict):
            # PaddleOCR 3.x 可能是 {'res': [...]}
            raw = raw.get("res") or raw.get("result")
        if isinstance(raw, dict):
            return out
        if not isinstance(raw, (list, tuple)):
            return out
        for entry in raw:
            if isinstance(entry, dict):
                # 端到端/表单类 dict
                out.append(entry)
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                # 常规：entry = [ box, ... ] 或 [ [box,(text,conf)], ... ]
                # 若第一个元素本身是 [[x,y],...] 则是单条
                if _is_box(entry[0]):
                    out.append(entry)
                else:
                    # 仍是列表嵌套（多页），递归摊平
                    out.extend(PaddleOcrAdapter._flatten_items(entry, _depth + 1))
        return out

    @staticmethod
    def _parse_item(item):
        if isinstance(item, dict):
            box = item.get("boxes") or item.get("boxes") or item.get("box")
            text = item.get("text") or item.get("rec_text") or item.get("res")
            conf = item.get("score") or item.get("rec_score")
            if isinstance(box, (list, tuple)) and len(box) >= 4 and isinstance(box[0], (int, float)):
                # 形如 [x0,y0,x1,y1]
                box = [[box[0], box[1]], [box[2], box[3]]]
            return box, text, conf
        if isinstance(item, (list, tuple)):
            # [ [box], (text, conf) ] 或 [box, conf, text] 或 [text, conf]
            box, text, conf = None, None, None
            if len(item) >= 2 and _is_box(item[0]):
                box = item[0]
                rest = item[1]
                if isinstance(rest, (list, tuple)):
                    if len(rest) >= 2:
                        text, conf = rest[0], rest[1]
                    elif rest:
                        text = rest[0]
                elif isinstance(rest, str):
                    text = rest
            elif len(item) >= 2:
                # [text, conf] 或 [text, bbox]
                if isinstance(item[0], str):
                    text, conf = item[0], (item[1] if len(item) > 1 else None)
                    if isinstance(item[1], (list, tuple)) and _is_box(item[1]):
                        box, conf = item[1], None
            return box, text, conf
        return None, None, None

    @staticmethod
    def _estimate_confidence(raw) -> float:
        """从原始结果里诚实估算置信度；未知则给 0.5（不加权重）。"""
        import statistics
        confs = []
        for item in PaddleOcrAdapter._flatten_items(raw):
            _, _, conf = PaddleOcrAdapter._parse_item(item)
            if isinstance(conf, (int, float)) and 0 <= conf <= 1:
                confs.append(float(conf))
        if not confs:
            return 0.5
        return round(statistics.mean(confs), 3)


def _is_box(obj) -> bool:
    """判断是否为四角坐标 [[x,y],...] 或 [x,y,x,y]。"""
    if not isinstance(obj, (list, tuple)):
        return False
    if not obj:
        return False
    if isinstance(obj[0], (list, tuple)):
        return len(obj) >= 3 and all(_is_point(p) for p in obj[:4] if isinstance(p, (list, tuple)))
    return all(isinstance(x, (int, float)) for x in obj) and len(obj) >= 4


def _normalize_polygon(box):
    """把扁平坐标 [x0,y0,x1,y1]（或带多余坐标）规整为四角多边形；非坐标返回 None。"""
    if not isinstance(box, (list, tuple)) or not box:
        return None
    if isinstance(box[0], (list, tuple)):
        points = [p for p in box if _is_point(p)]
        return points[:4] if len(points) >= 3 else None
    if all(isinstance(x, (int, float)) for x in box):
        # 扁平 [x0, y0, x1, y1, ...]
        return [[box[0], box[1]], [box[2], box[3]]]
    return None


def _is_point(p) -> bool:
    return isinstance(p, (list, tuple)) and len(p) >= 2 and all(
        isinstance(c, (int, float)) for c in p)


class MineruAdapter(BaseVisionAdapter):
    """MinerU 文档级复核引擎。

    MinerU 的可靠入口是本地 FastAPI 服务，而不是会自行轮询失败的 CLI
    包装器。它处理完整源文件，结果仅进入交叉验证和证据链，不会重复写进
    面向用户的正文 Markdown。
    """
    engine = EngineName.MINERU.value
    is_mock = False

    @staticmethod
    def _sidecar_python() -> Path | None:
        return PaddleOcrAdapter._sidecar_python()

    def available(self) -> bool:
        try:
            import mineru  # noqa: F401
            return True
        except Exception:
            runtime = self._sidecar_python()
            if runtime is None:
                return False
            probe = subprocess.run(
                [str(runtime), "-c", "import mineru"], capture_output=True,
                timeout=15, check=False,
            )
            return probe.returncode == 0

    def recognize_document(self, source_path: str) -> dict:
        """返回 MinerU 的真实 Markdown 与原始结构。"""
        if not self.available():
            raise VisionUnavailable(MISSING_MINERU)
        from .mineru_api import MineruLocalApi
        runtime = self._sidecar_python()
        if runtime is None:
            raise VisionUnavailable(MISSING_MINERU)
        try:
            raw = MineruLocalApi(str(runtime)).parse(source_path)
        except Exception as exc:  # noqa: BLE001
            raise VisionUnavailable(f"MinerU 独立复核失败：{exc}") from exc
        if raw.get("status") != "completed":
            raise VisionUnavailable(f"MinerU 未完成独立复核：{raw.get('status', 'unknown')}")
        results = raw.get("results") or {}
        payload = next(iter(results.values()), {}) if isinstance(results, dict) else {}
        markdown = str(payload.get("md_content") or "").strip()
        if not markdown:
            raise VisionUnavailable("MinerU 未返回 Markdown，不能视为已复核。")
        return {"markdown": markdown, "raw": raw}

    def recognize_page(self, image_path: str, page: int, extra: Optional[dict] = None) -> dict:
        if not self.available():
            raise VisionUnavailable(MISSING_MINERU)
        # 真实调用按 MinerU 文档级 API（返回 Markdown 结构）；此处保留接口，接通后解析。
        raise VisionUnavailable("MinerU 已接通需实现文档级调用；当前未配置，不作为单页主识别。")


class DeepSeekOcrAdapter(BaseVisionAdapter):
    """DeepSeek-OCR-2 第三识别适配器（异常页/冲突字段仲裁）。"""
    engine = EngineName.DEEPSEEK_OCR.value
    is_mock = False

    def available(self) -> bool:
        # 默认禁用，需在配置中显式开启
        return False

    def recognize_page(self, image_path: str, page: int, extra: Optional[dict] = None) -> dict:
        raise VisionUnavailable(MISSING_DEEPSEEK)


def make_vision_adapter(engine: str) -> BaseVisionAdapter:
    """按名称构造视觉适配器。未知名称抛 ValueError。"""
    if engine == "mock":
        return MockVisionAdapter()
    if engine in (EngineName.PADDLE_OCR.value, "paddle", "paddle_ocr"):
        return PaddleOcrAdapter()
    if engine == EngineName.MINERU.value:
        return MineruAdapter()
    if engine == EngineName.DEEPSEEK_OCR.value:
        return DeepSeekOcrAdapter()
    raise ValueError(f"未知视觉识别引擎: {engine}")
