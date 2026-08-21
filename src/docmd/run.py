"""DocMD 桌面应用入口。

支持两种启动方式：
    python -m docmd.run                 # 从源码运行（相对包结构 / 开发者模式）
    ./dist/DocMD/DocMD.exe              # PyInstaller 打包产物（run.py 被当作顶层脚本）

为兼容 PyInstaller 把 run.py 当顶层脚本执行的情形，这里统一使用「绝对导入」，
并在脚本被顶层执行时把包所在 src 目录临时加入 sys.path。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def default_data_dir() -> str:
    base = os.environ.get("DOCMD_DATA") or os.path.join(
        Path.home(), "AppData", "Roaming", "docmd")
    return base


def _ensure_src_on_path() -> None:
    """当 run.py 被当作顶层脚本（PyInstaller）执行时，把 src 加入 sys.path。"""
    here = Path(__file__).resolve()
    # run.py 位于 <root>/src/docmd/run.py
    src_dir = here.parent.parent
    # 也兼容开发时以 src 布局运行；若 docmd 已可导入则不重复添加
    try:
        import docmd  # noqa: F401
        return
    except Exception:
        pass
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def main(argv=None) -> int:
    _ensure_src_on_path()
    parser = argparse.ArgumentParser(description="DocMD 高可信 Markdown 桌面工具")
    parser.add_argument("--data-dir", default=default_data_dir(), help="数据目录（默认 ~/AppData/Roaming/docmd）")
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication
    from docmd.ui.main_window import MainWindow

    Path(args.data_dir).mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    win = MainWindow(args.data_dir)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
