# -*- coding: utf-8 -*-
"""Настройки приложения (ключ-значение в БД). Пароли — только в защищённом виде."""
from __future__ import annotations

from typing import Optional

from ..db.database import get_db
from .secrets_service import protect, unprotect


class SettingsService:
    def get(self, key: str, default: str = "") -> str:
        row = get_db().query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, str(default)))
        except ValueError:
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        return self.get(key, "1" if default else "0") in ("1", "true", "True")

    def set(self, key: str, value: str) -> None:
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?,?,datetime('now','localtime')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now','localtime')",
                (key, str(value)),
            )

    def set_protected(self, values: dict[str, str]) -> None:
        """Настройки склада, почты и резервного копирования меняет только администратор."""
        from .audit_service import audit_service
        from .auth_service import auth_service
        auth_service.require_admin("Изменять настройки программы может только администратор.")
        self.set_many(values)
        audit_service.log("Изменены настройки", entity="settings",
                          details=", ".join(sorted(values)))

    def set_many(self, values: dict[str, str]) -> None:
        db = get_db()
        with db.transaction() as conn:
            for key, value in values.items():
                conn.execute(
                    "INSERT INTO settings(key, value, updated_at) VALUES (?,?,datetime('now','localtime')) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now','localtime')",
                    (key, str(value)),
                )

    # ------- пароль SMTP хранится зашифрованным средствами Windows (DPAPI)
    def set_secret(self, key: str, plain: str) -> None:
        self.set(key, protect(plain))

    def get_secret(self, key: str) -> Optional[str]:
        raw = self.get(key, "")
        return unprotect(raw) if raw else None

    def warehouse(self) -> dict:
        """Реквизиты склада — попадают в шапку каждого документа."""
        keys = ["name", "address", "phone", "email", "inn", "ogrn", "bank_name",
                "bank_account", "bank_bik", "bank_corr", "logo_path", "extra"]
        return {k: self.get(f"warehouse.{k}", "") for k in keys}


settings_service = SettingsService()
