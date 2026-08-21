"""Stable local MinerU API client (avoids the unreliable CLI health wrapper)."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path


class MineruLocalApi:
    def __init__(self, python: str, port: int = 33942):
        self.python, self.port, self.proc = python, port, None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def ensure_started(self) -> None:
        if self._healthy():
            return
        self.proc = subprocess.Popen([self.python, "-m", "mineru.cli.fast_api", "--host", "127.0.0.1", "--port", str(self.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if self._healthy():
                return
            time.sleep(1)
        raise RuntimeError("MinerU 本地服务启动失败")

    def _healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=2) as r:
                return r.status == 200
        except OSError:
            return False

    def parse(self, source: str) -> dict:
        self.ensure_started()
        boundary = "----DocMDMinerU"
        data = Path(source).read_bytes()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{Path(source).name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + data + (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"backend\"\r\n\r\npipeline\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"return_md\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
        req = urllib.request.Request(self.base_url + "/file_parse", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.loads(r.read().decode("utf-8"))
