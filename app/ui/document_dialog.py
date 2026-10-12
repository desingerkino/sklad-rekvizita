# -*- coding: utf-8 -*-
"""Просмотр документа: предпросмотр, печать, сохранение PDF, отправка по email."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewDialog, QPrinter
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QInputDialog, QPushButton,
                               QTextBrowser, QVBoxLayout)

from ..errors import AppError
from ..services.documents_service import documents_service
from ..services.email_service import email_service
from ..services.rentals_service import rentals_service
from .errors import confirm, info, show_error


class DocumentDialog(QDialog):
    def __init__(self, parent, document_id: int):
        super().__init__(parent)
        self.document_id = document_id
        self.document = documents_service.get(document_id)
        self.setWindowTitle(f"Документ {self.document['number']}")
        self.resize(900, 760)
        self.html = documents_service.render_html(document_id)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        self.view = QTextBrowser()
        self.view.setHtml(self.html)
        root.addWidget(self.view, 1)

        buttons = QHBoxLayout()
        preview_btn = QPushButton("Предварительный просмотр")
        preview_btn.clicked.connect(self._preview)
        buttons.addWidget(preview_btn)

        print_btn = QPushButton("  Печать")
        from .widgets.common import icon as _icon2
        print_btn.setIcon(_icon2("printer"))
        print_btn.setMinimumHeight(42)
        print_btn.clicked.connect(self._print)
        buttons.addWidget(print_btn)

        pdf_btn = QPushButton("  Сохранить PDF…")
        pdf_btn.setObjectName("Primary")
        from .widgets.common import icon as _icon
        pdf_btn.setIcon(_icon("file", "white"))
        pdf_btn.setMinimumHeight(42)
        pdf_btn.clicked.connect(self._save_pdf)
        buttons.addWidget(pdf_btn)

        open_btn = QPushButton("Открыть PDF")
        open_btn.clicked.connect(self._open_pdf)
        buttons.addWidget(open_btn)

        email_btn = QPushButton("  Отправить по email…")
        from .widgets.common import icon as _icon3
        email_btn.setIcon(_icon3("mail"))
        email_btn.setMinimumHeight(42)
        email_btn.clicked.connect(self._send_email)
        buttons.addWidget(email_btn)

        buttons.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    # ---------------------------------------------------------------- печать
    def _qt_document(self) -> QTextDocument:
        doc = QTextDocument()
        doc.setHtml(self.html)
        return doc

    def _preview(self) -> None:
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintPreviewDialog(printer, self)
        dialog.setWindowTitle("Предварительный просмотр")
        dialog.paintRequested.connect(lambda p: self._qt_document().print_(p))
        dialog.exec()

    def _print(self) -> None:
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Печать документа")
        if dialog.exec():
            self._qt_document().print_(printer)

    # ------------------------------------------------------------------ PDF
    def _ensure_pdf(self) -> Path:
        """PDF формируется библиотекой генерации документов — это настоящий PDF."""
        doc = documents_service.get(self.document_id)
        if doc.get("abs_path") and Path(doc["abs_path"]).exists():
            return Path(doc["abs_path"])
        return Path(documents_service.render_pdf(self.document_id))

    def _save_pdf(self) -> None:
        try:
            source = self._ensure_pdf()
        except Exception as exc:
            show_error(self, exc, "Не удалось сформировать PDF.")
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Сохранить документ", f"{self.document['number']}.pdf", "PDF (*.pdf)")
        if not target:
            return
        try:
            shutil.copy2(source, target)
            info(self, f"Документ сохранён:\n{target}")
        except OSError:
            show_error(self, AppError("Не удалось сохранить файл в выбранную папку."))

    def _open_pdf(self) -> None:
        try:
            path = self._ensure_pdf()
        except Exception as exc:
            show_error(self, exc, "Не удалось открыть PDF.")
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))          # noqa: S606 — штатное открытие в Windows
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:
            show_error(self, exc, "Не удалось открыть файл.")

    # ---------------------------------------------------------------- email
    def _send_email(self) -> None:
        if not email_service.is_configured():
            show_error(self, AppError(
                "Почта не настроена. Откройте «Настройки → Email» и укажите SMTP-сервер, "
                "адрес и пароль."))
            return
        rental = rentals_service.get(self.document["rental_id"]) if self.document["rental_id"] else {}
        default = rental.get("client_email") or ""
        address, ok = QInputDialog.getText(self, "Отправка документа", "E-mail получателя:", text=default)
        if not ok or not address.strip():
            return
        try:
            pdf = self._ensure_pdf()
            email_service.send(
                address.strip(),
                subject=f"Документ {self.document['number']}",
                body=f"Здравствуйте!\n\nВо вложении документ {self.document['number']}.\n\n"
                     "Это письмо отправлено автоматически программой учёта склада реквизита.",
                attachments=[pdf])
            info(self, f"Документ отправлен на {address.strip()}.")
        except AppError as exc:
            show_error(self, exc)
        except Exception as exc:
            show_error(self, exc, "Не удалось отправить письмо.")
