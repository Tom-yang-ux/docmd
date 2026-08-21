"""桌宠的透明 3D 素材定位。"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_dir() -> Path:
    """兼容源码运行和 PyInstaller 打包后的资源目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets" / "pet"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[3] / "assets" / "pet"


def ensure_pet_assets(data_dir: str) -> dict[str, Path]:
    """保留旧接口；透明 PNG 已随应用打包，无需运行时生成方形 GIF。"""
    source = resource_dir()
    return {
        "bear": source / "bear_3d_frames_v1.gif",
        "girl": source / "girl_3d_frames_v2.gif",
        "final": source / "final_3d_frames_v2.gif",
        "night": source / "monet_starry_night_v1.png",
        "fairytale": source / "fairytale_final_v1.png",
        # 终极彩蛋是一张完整的统一画风场景；不再叠加任何角色 GIF。
        "fairytale_final_scene": source / "fairytale_final_scene_v2.png",
    }
