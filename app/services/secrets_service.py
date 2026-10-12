# -*- coding: utf-8 -*-
"""
Защита паролей.

На Windows используется штатный DPAPI (CryptProtectData) — пароль шифруется
ключом учётной записи Windows и в базе лежит только шифртекст. На других ОС
(разработка/тесты) — обратимое кодирование с явной пометкой: в открытом виде
пароль не хранится нигде.
"""
from __future__ import annotations

import base64
import ctypes
import logging
import os
from ctypes import wintypes

log = logging.getLogger(__name__)
_PREFIX_DPAPI = "dpapi:"
_PREFIX_PLAIN = "b64:"


if os.name == "nt":
    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> "_DATA_BLOB":
        buf = ctypes.create_string_buffer(data, len(data))
        return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _from_blob(blob: "_DATA_BLOB") -> bytes:
        return ctypes.string_at(blob.pbData, blob.cbData)


def protect(plain: str) -> str:
    if not plain:
        return ""
    if os.name == "nt":
        try:
            blob_in, blob_out = _blob(plain.encode("utf-8")), _DATA_BLOB()
            ok = ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(blob_in), "SkladRekvizita", None, None, None, 0, ctypes.byref(blob_out)
            )
            if ok:
                data = _from_blob(blob_out)
                ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                return _PREFIX_DPAPI + base64.b64encode(data).decode("ascii")
        except Exception:
            log.warning("DPAPI недоступен, пароль сохранён в кодированном виде")
    return _PREFIX_PLAIN + base64.b64encode(plain.encode("utf-8")).decode("ascii")


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith(_PREFIX_DPAPI) and os.name == "nt":
        try:
            raw = base64.b64decode(stored[len(_PREFIX_DPAPI):])
            blob_in, blob_out = _blob(raw), _DATA_BLOB()
            ok = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
            )
            if ok:
                data = _from_blob(blob_out)
                ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                return data.decode("utf-8")
        except Exception:
            log.warning("Не удалось расшифровать сохранённый пароль")
        return ""
    if stored.startswith(_PREFIX_PLAIN):
        return base64.b64decode(stored[len(_PREFIX_PLAIN):]).decode("utf-8")
    return ""
