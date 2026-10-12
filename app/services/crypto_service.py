# -*- coding: utf-8 -*-
"""
Шифрование персональных данных (версия 2.3.1).

Что защищено: паспортные данные, телефоны, адреса, e-mail и дополнительные
сведения клиентов, контакты и банковские реквизиты продакшенов, снимки
документов и резервные копии. Значения хранятся в базе в виде
`enc1:<base64>` — AES-256-GCM с уникальным вектором на каждое значение,
поэтому украденный файл базы не превращается в таблицу с телефонами и ФИО.

Ключ шифрования рядом с базой не лежит: на Windows он запечатан DPAPI
(CryptProtectData) под учётной записью пользователя и хранится в отдельной
папке %APPDATA%\\SkladRekvizita\\keys, на других системах — в файле
с правами 0600. В исходном коде ключей нет.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PREFIX = "enc1:"
_KEY_FILE = "master.key"


def key_dir() -> Path:
    """Ключ лежит отдельно от базы данных — не в папке с data.db."""
    override = os.environ.get("SKLAD_KEY_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        root = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / "SkladRekvizita" / "keys"
    return Path.home() / ".config" / "SkladRekvizita" / "keys"


class CryptoService:
    """Симметричное шифрование значений. Ключ создаётся при первом запуске."""

    def __init__(self) -> None:
        self._key: Optional[bytes] = None
        self._backend_ready: Optional[bool] = None

    # ------------------------------------------------------------- доступность
    @property
    def available(self) -> bool:
        """Есть ли криптобиблиотека. Без неё программа работает, но без шифрования."""
        if self._backend_ready is None:
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
                self._backend_ready = True
            except Exception:
                log.error("Библиотека cryptography недоступна — персональные данные "
                          "будут храниться без шифрования")
                self._backend_ready = False
        return self._backend_ready

    # ------------------------------------------------------------------- ключ
    def _load_key(self) -> bytes:
        if self._key is not None:
            return self._key
        directory = key_dir()
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        path = directory / _KEY_FILE
        if path.exists():
            self._key = self._unseal(path.read_bytes())
        else:
            key = secrets.token_bytes(32)
            path.write_bytes(self._seal(key))
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            log.info("Создан новый ключ шифрования данных")
            self._key = key
        return self._key

    @staticmethod
    def _seal(key: bytes) -> bytes:
        """На Windows ключ дополнительно запечатывается DPAPI."""
        from .secrets_service import protect
        return protect(base64.b64encode(key).decode("ascii")).encode("utf-8")

    @staticmethod
    def _unseal(blob: bytes) -> bytes:
        from .secrets_service import unprotect
        value = unprotect(blob.decode("utf-8"))
        if not value:
            raise RuntimeError(
                "Не удалось прочитать ключ шифрования. Он привязан к учётной записи "
                "Windows, под которой была создана база.")
        return base64.b64decode(value)

    def ensure_key(self) -> bool:
        """Создаёт ключ при первом запуске программы."""
        if not self.available:
            return False
        try:
            self._load_key()
            return True
        except Exception:
            log.exception("Не удалось подготовить ключ шифрования")
            return False

    # -------------------------------------------------------------- значения
    def encrypt(self, value: Optional[str]) -> str:
        """Шифрует строку. Пустые значения остаются пустыми."""
        if value is None or value == "":
            return ""
        if self.is_encrypted(value):
            return value
        if not self.available:
            return value
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(12)
        data = AESGCM(self._load_key()).encrypt(nonce, value.encode("utf-8"), None)
        return PREFIX + base64.b64encode(nonce + data).decode("ascii")

    def decrypt(self, value: Optional[str]) -> str:
        """Расшифровывает значение. Незашифрованные строки возвращаются как есть."""
        if not value:
            return ""
        if not self.is_encrypted(value):
            return value
        if not self.available:
            return ""
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            raw = base64.b64decode(value[len(PREFIX):])
            return AESGCM(self._load_key()).decrypt(raw[:12], raw[12:], None).decode("utf-8")
        except (InvalidTag, ValueError, RuntimeError):
            log.error("Не удалось расшифровать значение — возможно, другой ключ")
            return ""

    @staticmethod
    def is_encrypted(value: Optional[str]) -> bool:
        return bool(value) and str(value).startswith(PREFIX)

    # ---------------------------------------------------------------- файлы
    def encrypt_file(self, source: Path, target: Path) -> Path:
        """Шифрует файл целиком — используется для резервных копий."""
        if not self.available:
            raise RuntimeError("Шифрование недоступно: не установлена библиотека cryptography.")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(12)
        data = AESGCM(self._load_key()).encrypt(nonce, source.read_bytes(), None)
        target.write_bytes(b"SKLADENC1" + nonce + data)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return target

    def decrypt_file(self, source: Path, target: Path) -> Path:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        blob = source.read_bytes()
        if not blob.startswith(b"SKLADENC1"):
            raise ValueError("Файл не является зашифрованной копией программы.")
        body = blob[9:]
        try:
            data = AESGCM(self._load_key()).decrypt(body[:12], body[12:], None)
        except InvalidTag as exc:
            raise ValueError("Копия зашифрована другим ключом или повреждена.") from exc
        target.write_bytes(data)
        return target

    @staticmethod
    def is_encrypted_file(path: Path) -> bool:
        try:
            with open(path, "rb") as handle:
                return handle.read(9) == b"SKLADENC1"
        except OSError:
            return False

    # ------------------------------------------------------------- служебное
    def status(self) -> dict:
        """Сводка для раздела «О программе» и для тестов."""
        self.ensure_key()
        path = key_dir() / _KEY_FILE
        return {
            "available": self.available,
            "key_path": str(path),
            "key_exists": path.exists(),
            "key_outside_data_dir": self._key_outside_data(),
        }

    @staticmethod
    def _key_outside_data() -> bool:
        from .. import paths
        try:
            key_dir().resolve().relative_to(paths.DATA_DIR.resolve())
            return False
        except (ValueError, OSError):
            return True


crypto_service = CryptoService()
