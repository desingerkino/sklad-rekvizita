# -*- coding: utf-8 -*-
"""Отправка документов по email через SMTP склада. Пароль берётся из защищённого хранилища."""
from __future__ import annotations

import logging
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from ..errors import AppError
from .settings_service import settings_service

log = logging.getLogger(__name__)


class EmailService:
    def is_configured(self) -> bool:
        return bool(settings_service.get("email.smtp_host") and settings_service.get("email.from"))

    def _connect(self):
        host = settings_service.get("email.smtp_host")
        port = settings_service.get_int("email.smtp_port", 465)
        login = settings_service.get("email.login")
        password = settings_service.get_secret("email.password") or ""
        use_ssl = settings_service.get_bool("email.use_ssl", True)
        if not host:
            raise AppError("Не настроен почтовый сервер. Откройте «Настройки → Email».")
        context = ssl.create_default_context()
        try:
            if use_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
            else:
                server = smtplib.SMTP(host, port, timeout=30)
                server.starttls(context=context)
            if login:
                server.login(login, password)
            return server
        except smtplib.SMTPAuthenticationError as exc:
            raise AppError("Почтовый сервер отклонил логин или пароль. Проверьте настройки Email.") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise AppError(f"Не удалось соединиться с почтовым сервером {host}:{port}.") from exc

    def send(self, to: str | Iterable[str], subject: str, body: str,
             attachments: Iterable[Path] = ()) -> None:
        recipients = [to] if isinstance(to, str) else list(to)
        recipients = [r.strip() for r in recipients if r and r.strip()]
        if not recipients:
            raise AppError("Не указан адрес получателя.")

        msg = EmailMessage()
        msg["From"] = settings_service.get("email.from")
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(body)

        for path in attachments:
            path = Path(path)
            if not path.exists():
                raise AppError(f"Файл вложения не найден: {path.name}")
            ctype, _ = mimetypes.guess_type(str(path))
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype,
                               filename=path.name)

        with self._connect() as server:
            server.send_message(msg)
        log.info("Письмо отправлено (%s получателей, вложений: %s)",
                 len(recipients), len(list(attachments)))

    def test_connection(self) -> str:
        with self._connect():
            pass
        return "Соединение с почтовым сервером установлено."


email_service = EmailService()
