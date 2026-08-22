"""模型与运行环境管理。

模型权重不打入 EXE：本模块负责探测本机可用引擎/GPU、给出可执行安装命令。
（模型选择/状态由调用方在 SQLite 中管理；下载模型权重由各官方 SDK 在首次真实推理时完成。）
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelStatus:
    key: str
    title: str
    available: bool
    detail: str
    install_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeviceProfile:
    """运行时自动选择的设备策略；CPU 永远是受支持的回退路径。"""
    mode: str
    label: str
    detail: str
    gpu_name: str = ""
    memory_mb: int = 0


class ModelManager:
    SPECS = {
        "paddle_ocr": ("PaddleOCR-VL 1.6", ("-U", "paddleocr", "paddlepaddle", "paddlex")),
        "mineru": ("MinerU", ("-U", "mineru")),
        "deepseek_ocr": ("DeepSeek-OCR", ()),
    }

    @staticmethod
    def runtime_python() -> Path:
        appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return appdata / "docmd" / "ocr-runtime" / "Scripts" / "python.exe"

    @staticmethod
    def _bootstrap_python() -> Path:
        """Find a supported Python to create the optional OCR environment."""
        configured = os.environ.get("DOCMD_BOOTSTRAP_PYTHON")
        candidates = [Path(configured)] if configured else []
        # uv keeps downloaded CPython runtimes here even when its CLI is not on PATH.
        candidates.extend(Path.home().glob("AppData/Roaming/uv/python/*/python.exe"))
        if not getattr(sys, "frozen", False) and sys.version_info[:2] <= (3, 12):
            candidates.append(Path(sys.executable))
        return next((path for path in candidates if path.is_file()), Path())

    @classmethod
    def _runtime_has(cls, module: str) -> bool:
        runtime = cls.runtime_python()
        if not runtime.is_file():
            return False
        return subprocess.run([str(runtime), "-c", f"import {module}"], capture_output=True,
                              timeout=15, check=False).returncode == 0

    def status(self, key: str, ai_configured: bool = False) -> ModelStatus:
        title, args = self.SPECS[key]
        if key == "paddle_ocr":
            ok = self._runtime_has("paddleocr") or (not getattr(sys, "frozen", False) and importlib.util.find_spec("paddleocr") is not None)
            if ok:
                try:
                    from paddleocr import PaddleOCRVL  # noqa: F401
                    return ModelStatus(key, title, True, "已安装，首次推理会下载官方模型权重。", args)
                except Exception:
                    return ModelStatus(key, title, False, "检测到 PaddleOCR，但缺少 PaddleOCRVL 文档解析管线。", args)
            return ModelStatus(key, title, False, "未安装。", args)
        if key == "mineru":
            ok = self._runtime_has("mineru") or (not getattr(sys, "frozen", False) and importlib.util.find_spec("mineru") is not None)
            detail = "已安装于用户本地 OCR 环境。" if ok else "未安装；必须与主引擎同时就绪，才允许开始处理。"
            return ModelStatus(key, title, ok, detail, args)
        return ModelStatus(key, title, ai_configured, "已配置视觉 API。" if ai_configured else "需在 AI 设置中配置视觉 API 与模型。", args)

    def all_status(self, ai_configured: bool = False) -> list[ModelStatus]:
        return [self.status(key, ai_configured) for key in self.SPECS]

    @staticmethod
    def gpu_summary() -> str:
        return ModelManager.device_profile().detail

    @staticmethod
    def device_profile() -> DeviceProfile:
        """探测 NVIDIA GPU，但不要求 GPU 才能运行。

        MinerU pipeline 与 Paddle 的默认配置均可在 CPU 上执行；GPU 被检测到
        时 SDK 会使用可用 CUDA 路径，界面只展示策略而不让普通用户选模型。
        """
        command = shutil.which("nvidia-smi")
        if not command:
            return DeviceProfile("cpu", "CPU 模式", "未检测到 NVIDIA GPU；将使用 CPU，速度较慢但功能完整。")
        try:
            output = subprocess.run(
                [command, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8, check=False,
            ).stdout.strip()
            if not output:
                return DeviceProfile("cpu", "CPU 模式", "检测到 NVIDIA GPU，但无法读取显存；为保证稳定将使用 CPU。")
            first = output.splitlines()[0]
            name, _, memory = first.partition(",")
            try:
                memory_mb = int(memory.strip())
            except ValueError:
                memory_mb = 0
            return DeviceProfile("gpu", "NVIDIA GPU 模式",
                f"已检测到 {name.strip()}（约 {memory_mb} MiB 显存）；将优先使用 GPU 加速。",
                name.strip(), memory_mb)
        except OSError:
            return DeviceProfile("cpu", "CPU 模式", "GPU 信息读取失败；将使用 CPU，功能不受限制。")

    @classmethod
    def install(cls, key: str) -> subprocess.CompletedProcess:
        _, args = cls.SPECS[key]
        if not args:
            raise ValueError("该模型通过 API 配置，不支持本地 pip 安装。")
        runtime = cls.runtime_python()
        if not runtime.is_file():
            bootstrap = cls._bootstrap_python()
            if not bootstrap.is_file():
                raise RuntimeError("未找到 Python 3.10–3.12，无法创建 OCR 环境。请安装受支持的 Python 后重试。")
            runtime.parent.parent.mkdir(parents=True, exist_ok=True)
            created = subprocess.run([str(bootstrap), "-m", "venv", str(runtime.parent.parent)],
                                     capture_output=True, text=True, check=False)
            if created.returncode:
                raise RuntimeError((created.stderr or created.stdout or "创建 OCR 环境失败")[-3000:])
        os.environ["DOCMD_OCR_PYTHON"] = str(runtime)
        return subprocess.run(
            [str(runtime), "-m", "pip", "install", *args], capture_output=True, text=True, check=False,
        )
