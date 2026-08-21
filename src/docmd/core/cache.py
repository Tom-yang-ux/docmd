"""哈希缓存：同一文件、同一模型、同一配置不重复识别。

缓存键 = sha1(文件字节 + 引擎 + 配置指纹)，命中返回已保存的原始结果。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional


class HashCache:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "cache"
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key_for(file_path: str, engine: str, config_fp: str = "") -> str:
        with open(file_path, "rb") as f:
            data_hash = hashlib.sha1(f.read()).hexdigest()
        idx = hashlib.sha1(f"{data_hash}|{engine}|{config_fp}".encode("utf-8")).hexdigest()
        return idx

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> Optional[dict]:
        p = self._path(key)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def put(self, key: str, value: dict) -> None:
        self._path(key).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def clear(self) -> None:
        for p in self.dir.glob("*.json"):
            p.unlink()

    def count(self) -> int:
        """当前缓存条目数（用于测试缓存命中）。"""
        return len(list(self.dir.glob("*.json")))
