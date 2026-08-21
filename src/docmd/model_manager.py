"""模型与运行环境管理。

模型权重不打入 EXE：本模块负责探测本机可用引擎/GPU、给出可执行安装命令。
（模型选择/状态由调用方在 SQLite 中管理；下载模型权重由各官方 SDK 在首次真实推理时完成。）
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass


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

    def status(self, key: str, ai_configured: bool = False) -> ModelStatus:
        title, args = self.SPECS[key]
        if key == "paddle_ocr":
            ok = importlib.util.find_spec("paddleocr") is not None
            if ok:
                try:
                    from paddleocr import PaddleOCRVL  # noqa: F401
                    return ModelStatus(key, title, True, "已安装，首次推理会下载官方模型权重。", args)
                except Exception:
                    return ModelStatus(key, title, False, "检测到 PaddleOCR，但缺少 PaddleOCRVL 文档解析管线。", args)
            return ModelStatus(key, title, False, "未安装。", args)
        if key == "mineru":
            ok = importlib.util.find_spec("mineru") is not None or shutil.which("mineru") is not None
            return ModelStatus(key, title, ok, "已安装。" if ok else "未安装。", args)
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

    @staticmethod
    def install(key: str) -> subprocess.CompletedProcess:
        _, args = ModelManager.SPECS[key]
        if not args:
            raise ValueError("该模型通过 API 配置，不支持本地 pip 安装。")
        return subprocess.run(
            [sys.executable, "-m", "pip", "install", *args], capture_output=True, text=True, check=False,
        )
