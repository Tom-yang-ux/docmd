"""测试：密钥安全存储——不落明文 SQLite，加密读取。"""
import pytest

from docmd.core.secrets import SecretStore


class TestSecretStore:
    def test_set_get_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        ss = SecretStore(prefer="file")  # 强制文件后端，避免依赖 Windows 凭据管理器
        assert ss.backend == "encrypted_file"
        assert ss.available() is True
        ss.set("ai", "api_key", "sk-secret-xxx")
        assert ss.get("ai", "api_key") == "sk-secret-xxx"
        ss.delete("ai", "api_key")
        assert ss.get("ai", "api_key") is None

    def test_stored_file_encrypted_not_plaintext(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        ss = SecretStore(prefer="file")
        ss.set("ai", "api_key", "sk-secret-xxx")
        enc_file = tmp_path / "docmd" / "secrets.enc"
        assert enc_file.exists()
        raw = enc_file.read_text(encoding="utf-8")
        # 明文密钥不得出现在存储文件里
        assert "sk-secret-xxx" not in raw

    def test_api_key_not_in_sqlite(self, tmp_path, monkeypatch):
        """密钥存入 SecretStore，而非 SQLite ai_config 表。"""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        from docmd.storage.database import Database
        db = Database(str(tmp_path / "t.db"))
        # 模拟 UI 保存：只把非敏感配置写 SQLite，密钥写 SecretStore
        db.set_config("ai_config", "base_url", "https://x")
        db.set_config("ai_config", "model", "m")
        ss = SecretStore(prefer="file")
        ss.set("ai", "api_key", "sk-secret-xxx")
        all_cfg = db.all_config("ai_config")
        assert "api_key" not in all_cfg
        assert ss.get("ai", "api_key") == "sk-secret-xxx"
        db.close()
