"""小熊桌宠素材定位。"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_dir() -> Path:
    """兼容源码运行和 PyInstaller 打包后的资源目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets" / "pet"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[3] / "assets" / "pet"


def pet_asset_path() -> Path:
    """返回唯一的小熊高分辨率精灵图。"""
    source = resource_dir()
    return source / "bear_3d_sheet_v1.png"
