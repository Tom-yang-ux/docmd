"""PaddleOCR-VL sidecar entrypoint.

The desktop UI runs on the packaged Python runtime.  PaddleOCR-VL currently
requires a supported Python version on Windows, so this deliberately tiny
program runs the installed OCR runtime and emits one JSON object to stdout.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys


def _mapping(result):
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


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "missing image path"}))
        return 2
    # Keep third-party startup chatter out of stdout: parent expects JSON only.
    noisy = io.StringIO()
    with contextlib.redirect_stdout(noisy):
        from paddleocr import PaddleOCRVL
        pipeline = PaddleOCRVL()
        result = next(iter(pipeline.predict(sys.argv[1])), None)
    print(json.dumps(_mapping(result), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
