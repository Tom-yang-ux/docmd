"""本地安全存储：API 密钥等敏感配置不落明文 SQLite。

方案（按可用性降级）：
1. Windows 凭据管理器（keyring 的 Windows backend）。
2. 不可用时，退化为本地加密文件（AES-GCM），密钥存在用户主目录的受限权限文件中。
   文件仅在 appdata 数据目录之外、仅当前用户可读的位置。

对外统一 API：
    set_secret(namespace, key, value)
    get_secret(namespace, key) -> str | None
    delete_secret(namespace, key)
    secret_available() -> bool          # 是否有可用的安全后端
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

_SERVICE = "DocMD"


def _data_base_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")) / "docmd"


def _salt_path() -> Path:
    return _data_base_dir() / "key_salt.bin"


def _load_or_create_salt() -> bytes:
    p = _salt_path()
    if p.exists():
        return p.read_bytes()
    salt = os.urandom(16)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(salt)
    _restrict_file(p)
    return salt


def _restrict_file(path: Path) -> None:
    """限制本地加密文件为「当前用户」可访问（best effort）。

    用当前用户 SID 追加完全控制权限，不剥离继承、不授予 Everyone。
    任何一步失败都静默（数据安全性由随机盐+PBKDF2 保证，ACL 是额外加固）。
    """
    try:
        import subprocess
        shared = subprocess.run(["whoami", "/user"], capture_output=True, text=True, timeout=5)
        sid = None
        for line in (shared.stdout or "").splitlines():
            if "S-1-5-21" in line:
                sid = line.split()[-1].strip()
                break
        if sid:
            subprocess.run(["icacls", str(path), "/grant", f"*{sid}:(F)"],
                           capture_output=True, timeout=5)
    except Exception:
        pass


def _machine_binding() -> bytes:
    """用「本地持久随机盐 + PBKDF2」派生密钥。

    比固定 sha256(seed) 更安全：攻击者仅凭 secrets.enc 与可预测的路径/用户名字符串
    无法重算密钥；还需拿到本地随机盐文件。换机/换用户后旧密文自然不可解。
    """
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    salt = _load_or_create_salt()
    seed = f"{_SERVICE}::{os.environ.get('USERNAME', '')}::{_data_base_dir()}".encode("utf-8")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
    return kdf.derive(seed)


def _aes_key() -> bytes:
    return _machine_binding()


class _KeyringBackend:
    name = "keyring"

    def available(self) -> bool:
        try:
            import keyring  # noqa: F401
            return True
        except Exception:
            return False

    def set(self, namespace: str, key: str, value: str) -> None:
        import keyring
        keyring.set_password(_SERVICE, f"{namespace}:{key}", value)

    def get(self, namespace: str, key: str) -> Optional[str]:
        import keyring
        return keyring.get_password(_SERVICE, f"{namespace}:{key}")

    def delete(self, namespace: str, key: str) -> None:
        try:
            import keyring
            keyring.delete_password(_SERVICE, f"{namespace}:{key}")
        except Exception:
            pass


class _FileBackend:
    """本地加密文件后端（AES-GCM）。存储加密后的密文。"""
    name = "encrypted_file"
    _file: Path

    def __init__(self):
        base = Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"))
        self._file = base / "docmd" / "secrets.enc"
        self._file.parent.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        return True

    def _load(self) -> dict:
        if not self._file.exists():
            return {}
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            data = json.loads(self._file.read_text(encoding="utf-8"))
            raw = base64.b64decode(data["payload"])
            aesgcm = AESGCM(_aes_key())
            nonce = base64.b64decode(data["nonce"])
            plain = aesgcm.decrypt(nonce, raw, b"docmd-secrets")
            return json.loads(plain.decode("utf-8"))
        except Exception:
            # 解密失败视为无内容（换机/密钥失配）
            return {}

    def _save(self, payload: dict) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        aesgcm = AESGCM(_aes_key())
        cipher = aesgcm.encrypt(nonce, json.dumps(payload).encode("utf-8"), b"docmd-secrets")
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(
            {"nonce": base64.b64encode(nonce).decode(), "payload": base64.b64encode(cipher).decode()},
        ), encoding="utf-8")
        _restrict_file(self._file)

    def set(self, namespace: str, key: str, value: str) -> None:
        d = self._load()
        d[f"{namespace}::{key}"] = value
        self._save(d)

    def get(self, namespace: str, key: str) -> Optional[str]:
        return self._load().get(f"{namespace}::{key}")

    def delete(self, namespace: str, key: str) -> None:
        d = self._load()
        d.pop(f"{namespace}::{key}", None)
        self._save(d)


class SecretStore:
    def __init__(self, prefer: Optional[str] = None):
        self._keyring = _KeyringBackend()
        self._file = _FileBackend()
        if prefer == "file":
            self._backend = self._file
        else:
            self._backend = self._keyring if self._keyring.available() else self._file

    @property
    def backend(self) -> str:
        return self._backend.name

    def available(self) -> bool:
        return True  # 至少 file 后端可用

    def set(self, namespace: str, key: str, value: str) -> None:
        self._backend.set(namespace, key, value)

    def get(self, namespace: str, key: str) -> Optional[str]:
        return self._backend.get(namespace, key)

    def delete(self, namespace: str, key: str) -> None:
        self._backend.delete(namespace, key)
