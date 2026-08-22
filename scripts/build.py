"""PyInstaller 打包脚本。

用法（在项目根目录 / 干净虚拟环境）：
    pip install -r requirements.txt
    pip install pyinstaller
    python scripts/build.py            # 生成 dist/DocMD.exe

大型视觉模型权重与 EXE 分离：首次启动由「模型管理器」下载/导入，
不把数 GB 模型塞入安装包。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    os.chdir(ROOT)
    workdir = ROOT / "build"
    workdir.mkdir(exist_ok=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "DocMD",
        "--windowed",                      # 无控制台窗口
        "--icon", "assets/app.ico" if (ROOT / "assets/app.ico").exists() else "NONE",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(workdir),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT / "src"),          # 让分析器找到 docmd 包
        "--collect-all", "pymupdf",
        "--collect-all", "PIL",
        "--add-data", f"{ROOT / 'assets' / 'pet'};assets/pet",
        "--add-data", f"{ROOT / 'src' / 'docmd' / 'extractors' / 'ocr_sidecar.py'};docmd/extractors",
        # 打包入口（main）会创建数据目录并启动界面
        "src/docmd/run.py",
    ]
    # 去掉无效 icon 参数
    if "NONE" in cmd:
        cmd.remove("--icon"); cmd.remove("NONE")
    print(">>> " + " ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode:
        return proc.returncode
    # OCR 运行时和模型按需安装，不随主安装包分发，避免小工具膨胀到数 GB。
    print(">>> OCR runtime is external and installed on demand; it is not bundled with DocMD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
