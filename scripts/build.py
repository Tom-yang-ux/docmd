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
import shutil
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
    # 真实 OCR 需 Python 3.10-3.12；主界面可使用更新的 Python，故把经过
    # 验证的独立运行时与主 EXE 一并安装，用户无需手工选择识别工具。
    runtime_source = ROOT / ".venv-ocr"
    runtime_target = ROOT / "dist" / "DocMD" / "ocr-runtime"
    if runtime_source.is_dir():
        if runtime_target.exists():
            shutil.rmtree(runtime_target)
        shutil.copytree(runtime_source, runtime_target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f">>> bundled OCR runtime: {runtime_target}")
    else:
        print(">>> WARNING: .venv-ocr missing; installer will not have real dual-engine OCR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
