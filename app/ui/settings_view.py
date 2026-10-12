# -*- coding: utf-8 -*-
"""Настройки: склад, email, резервные копии, категории, документы, служебная информация."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFormLayout, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressDialog, QPushButton, QSpinBox,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import paths
from ..errors import AppError
from ..services.audit_service import audit_service
from ..services.auth_service import ROLE_ADMIN, ROLE_LABELS, ROLE_USER, auth_service
from ..services.backup_service import backup_service
from ..services.recovery_service import recovery_service
from ..services.categories_service import categories_service
from ..services.documents_service import documents_service
from ..services.email_service import email_service
from ..services.image_search import image_search
from ..services.settings_service import settings_service
from ..utils import fmt_dt, money_plain
from ..version import DISPLAY_NAME, RENTAL_PERIOD_DAYS, VERSION
from .errors import confirm, info, show_error
from .widgets.common import cell, make_table, muted, title_label


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(path))          # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass


class SettingsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        from .theme import Space
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        from .widgets.common import scroll_area
        tabs = QTabWidget()
        # каждая вкладка прокручивается: длинные формы не обрезаются
        tabs.addTab(scroll_area(self._warehouse_tab()), "Склад")
        tabs.addTab(scroll_area(self._categories_tab()), "Категории")
        tabs.addTab(scroll_area(self._email_tab()), "Email")
        tabs.addTab(scroll_area(self._backup_tab()), "Резервные копии")
        tabs.addTab(scroll_area(self._documents_tab()), "Документы")
        tabs.addTab(scroll_area(self._imagesearch_tab()), "Поиск по фото")
        if auth_service.is_admin():
            tabs.addTab(scroll_area(self._users_tab()), "Пользователи")
            tabs.addTab(scroll_area(self._security_tab()), "Безопасность")
            tabs.addTab(scroll_area(self._audit_tab()), "Журнал действий")
        tabs.addTab(scroll_area(self._about_tab()), "О программе")
        self._apply_permissions()
        root.addWidget(tabs, 1)
        self.tabs = tabs

    # -------------------------------------------------------------- склад
    def _warehouse_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("Реквизиты склада — автоматически попадают в шапку каждого документа")
        form = QFormLayout(box)
        self.wh_fields = {}
        for key, label in [("name", "Название:"), ("address", "Адрес:"), ("phone", "Телефон:"),
                           ("email", "E-mail:"), ("inn", "ИНН:"), ("ogrn", "ОГРН:"),
                           ("bank_name", "Банк:"), ("bank_account", "Расчётный счёт:"),
                           ("bank_bik", "БИК:"), ("bank_corr", "Корреспондентский счёт:")]:
            edit = QLineEdit(settings_service.get(f"warehouse.{key}"))
            self.wh_fields[key] = edit
            form.addRow(label, edit)

        logo_row = QHBoxLayout()
        self.logo_path = QLineEdit(settings_service.get("warehouse.logo_path"))
        logo_row.addWidget(self.logo_path, 1)
        pick_logo = QPushButton("Выбрать…")
        pick_logo.clicked.connect(self._pick_logo)
        logo_row.addWidget(pick_logo)
        logo_widget = QWidget(); logo_widget.setLayout(logo_row)
        form.addRow("Логотип:", logo_widget)

        self.wh_extra = QPlainTextEdit(settings_service.get("warehouse.extra"))
        self.wh_extra.setFixedHeight(70)
        form.addRow("Дополнительно:", self.wh_extra)
        layout.addWidget(box)

        save = QPushButton("Сохранить реквизиты")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_warehouse)
        layout.addWidget(save, 0, Qt.AlignLeft)
        layout.addStretch()
        return page

    def _pick_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Логотип склада", "", "Изображения (*.png *.jpg *.jpeg)")
        if path:
            self.logo_path.setText(path)

    def _save_warehouse(self) -> None:
        values = {f"warehouse.{k}": e.text().strip() for k, e in self.wh_fields.items()}
        values["warehouse.logo_path"] = self.logo_path.text().strip()
        values["warehouse.extra"] = self.wh_extra.toPlainText().strip()
        try:
            settings_service.set_protected(values)
        except AppError as exc:
            show_error(self, exc)
            return
        info(self, "Реквизиты склада сохранены и будут использоваться в новых документах.")

    # ---------------------------------------------------------- категории
    def _categories_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(muted("Категории хранятся в базе: их можно добавлять, переименовывать, "
                               "перемещать и удалять. Порядок задаётся кнопками «Выше» и «Ниже»."))
        self.cat_tree = QTreeWidget()
        self.cat_tree.setHeaderLabels(["Категория", "Предметов"])
        self.cat_tree.setColumnWidth(0, 320)
        layout.addWidget(self.cat_tree, 1)

        buttons = QHBoxLayout()
        for text, handler in [("Добавить категорию", self._cat_add_root),
                              ("Добавить подкатегорию", self._cat_add_child),
                              ("Переименовать", self._cat_rename),
                              ("Переместить…", self._cat_move),
                              ("Выше", lambda: self._cat_reorder(-1)),
                              ("Ниже", lambda: self._cat_reorder(1)),
                              ("Удалить", self._cat_delete)]:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            buttons.addWidget(btn)
        buttons.addStretch()
        layout.addLayout(buttons)
        self._reload_categories()
        return page

    def _reload_categories(self) -> None:
        self.cat_tree.clear()

        def add(nodes, parent=None):
            for node in nodes:
                item = QTreeWidgetItem([node["name"], str(node["count"] or "")])
                item.setData(0, Qt.UserRole, node["id"])
                (parent.addChild(item) if parent else self.cat_tree.addTopLevelItem(item))
                add(node["children"], item)

        add(categories_service.tree())
        self.cat_tree.expandToDepth(0)

    def _selected_category(self):
        item = self.cat_tree.currentItem()
        return (item, item.data(0, Qt.UserRole)) if item else (None, None)

    def _cat_add_root(self) -> None:
        name, ok = QInputDialog.getText(self, "Новая категория", "Название:")
        if ok and name:
            self._run_category(lambda: categories_service.add(name, None))

    def _cat_add_child(self) -> None:
        _, category_id = self._selected_category()
        if not category_id:
            return
        name, ok = QInputDialog.getText(self, "Новая подкатегория", "Название:")
        if ok and name:
            self._run_category(lambda: categories_service.add(name, category_id))

    def _cat_rename(self) -> None:
        item, category_id = self._selected_category()
        if not category_id:
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Название:", text=item.text(0))
        if ok and name:
            self._run_category(lambda: categories_service.rename(category_id, name))

    def _cat_move(self) -> None:
        _, category_id = self._selected_category()
        if not category_id:
            return
        options = ["— в корень —"] + [c["name"] for c in categories_service.all() if c["id"] != category_id]
        ids = [None] + [c["id"] for c in categories_service.all() if c["id"] != category_id]
        choice, ok = QInputDialog.getItem(self, "Переместить категорию", "Новый родитель:", options, 0, False)
        if ok:
            self._run_category(lambda: categories_service.move(category_id, ids[options.index(choice)]))

    def _cat_reorder(self, delta: int) -> None:
        item, category_id = self._selected_category()
        if not category_id:
            return
        current = categories_service.get(category_id)
        self._run_category(lambda: categories_service.reorder(
            category_id, max(0, (current["sort_order"] or 0) + delta)))

    def _cat_delete(self) -> None:
        item, category_id = self._selected_category()
        if not category_id:
            return
        if confirm(self, f"Удалить категорию «{item.text(0)}»?"):
            self._run_category(lambda: categories_service.delete(category_id))

    def _run_category(self, action) -> None:
        try:
            action()
        except AppError as exc:
            show_error(self, exc)
        self._reload_categories()

    # -------------------------------------------------------------- email
    def _email_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("Почта для отправки документов")
        form = QFormLayout(box)
        self.smtp_host = QLineEdit(settings_service.get("email.smtp_host"))
        self.smtp_host.setPlaceholderText("например, smtp.yandex.ru")
        self.smtp_port = QSpinBox(); self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(settings_service.get_int("email.smtp_port", 465))
        self.smtp_ssl = QCheckBox("SSL (порт 465). Без галочки — STARTTLS (обычно 587)")
        self.smtp_ssl.setChecked(settings_service.get_bool("email.use_ssl", True))
        self.smtp_login = QLineEdit(settings_service.get("email.login"))
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.Password)
        self.smtp_password.setPlaceholderText("оставьте пустым, чтобы не менять")
        self.smtp_from = QLineEdit(settings_service.get("email.from"))
        form.addRow("SMTP-сервер:", self.smtp_host)
        form.addRow("Порт:", self.smtp_port)
        form.addRow("", self.smtp_ssl)
        form.addRow("Логин:", self.smtp_login)
        form.addRow("Пароль:", self.smtp_password)
        form.addRow("Адрес отправителя:", self.smtp_from)
        layout.addWidget(box)
        layout.addWidget(muted("Пароль шифруется средствами Windows (DPAPI) и в базе "
                               "в открытом виде не хранится."))

        buttons = QHBoxLayout()
        save = QPushButton("Сохранить")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_email)
        buttons.addWidget(save)
        test = QPushButton("Проверить соединение")
        test.clicked.connect(self._test_email)
        buttons.addWidget(test)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()
        return page

    def _save_email(self) -> None:
        try:
            settings_service.set_protected({
                "email.smtp_host": self.smtp_host.text().strip(),
                "email.smtp_port": str(self.smtp_port.value()),
                "email.use_ssl": "1" if self.smtp_ssl.isChecked() else "0",
                "email.login": self.smtp_login.text().strip(),
                "email.from": self.smtp_from.text().strip(),
            })
        except AppError as exc:
            show_error(self, exc)
            return
        if self.smtp_password.text():
            settings_service.set_secret("email.password", self.smtp_password.text())
            self.smtp_password.clear()
        info(self, "Настройки почты сохранены.")

    def _test_email(self) -> None:
        self._save_email()
        try:
            info(self, email_service.test_connection())
        except AppError as exc:
            show_error(self, exc)
        except Exception as exc:
            show_error(self, exc, "Проверка соединения не удалась.")

    # ------------------------------------------------------------- бэкапы
    def _backup_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        box = QGroupBox("Автоматическое резервное копирование")
        form = QFormLayout(box)
        self.backup_enabled = QCheckBox("Создавать копию базы автоматически при первом запуске за день")
        self.backup_enabled.setChecked(settings_service.get_bool("backup.enabled", True))
        self.backup_keep = QSpinBox(); self.backup_keep.setRange(1, 365)
        self.backup_keep.setValue(settings_service.get_int("backup.keep", 14))
        dir_row = QHBoxLayout()
        self.backup_dir = QLineEdit(settings_service.get("backup.dir") or str(paths.BACKUPS_DIR))
        dir_row.addWidget(self.backup_dir, 1)
        pick = QPushButton("Папка…")
        pick.clicked.connect(self._pick_backup_dir)
        dir_row.addWidget(pick)
        dir_widget = QWidget(); dir_widget.setLayout(dir_row)
        form.addRow("", self.backup_enabled)
        form.addRow("Хранить копий:", self.backup_keep)
        form.addRow("Папка копий:", dir_widget)
        self.last_backup = QLabel(fmt_dt(settings_service.get("backup.last_run")) or "—")
        form.addRow("Последняя копия:", self.last_backup)
        layout.addWidget(box)

        buttons = QHBoxLayout()
        save = QPushButton("Сохранить настройки")
        save.clicked.connect(self._save_backup_settings)
        buttons.addWidget(save)
        create = QPushButton("Создать копию сейчас")
        create.setObjectName("Primary")
        create.clicked.connect(self._create_backup)
        buttons.addWidget(create)
        restore = QPushButton("Восстановить из копии…")
        restore.clicked.connect(self._restore_backup)
        buttons.addWidget(restore)
        open_btn = QPushButton("Открыть папку копий")
        open_btn.clicked.connect(lambda: open_folder(backup_service.backup_dir()))
        buttons.addWidget(open_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

        external = QHBoxLayout()
        verify_btn = QPushButton("Проверить выбранную копию")
        verify_btn.setToolTip("Открыть копию, расшифровать и убедиться, что из неё "
                              "действительно можно восстановиться")
        verify_btn.clicked.connect(self._verify_backup)
        external.addWidget(verify_btn)
        export_btn = QPushButton("Скопировать на внешний диск…")
        export_btn.setToolTip("Внешний HDD или SSD, флешка, сетевой диск — "
                              "любая доступная папка")
        export_btn.clicked.connect(self._export_backup)
        external.addWidget(export_btn)
        storages_btn = QPushButton("Хранилища копий")
        storages_btn.clicked.connect(self._show_storages)
        external.addWidget(storages_btn)
        external.addStretch()
        layout.addLayout(external)

        layout.addWidget(muted(
            "Копия на том же диске, что и рабочая база, спасает от ошибки, но не "
            "спасает от смерти диска. Держите хотя бы одну копию на внешнем носителе."))
        self.last_export = QLabel(
            (fmt_dt(settings_service.get("backup.last_export")) + " → "
             + settings_service.get("backup.last_export_where", ""))
            if settings_service.get("backup.last_export") else "ни разу")
        self.last_export.setWordWrap(True)
        export_form = QFormLayout()
        export_form.addRow("Выгружалась наружу:", self.last_export)
        layout.addLayout(export_form)

        self.backups_table = make_table(["Файл", "Создана", "Размер, МБ", "С фотографиями"], 0)
        layout.addWidget(self.backups_table, 1)
        self._reload_backups()
        return page

    def _pick_backup_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка для резервных копий", self.backup_dir.text())
        if path:
            self.backup_dir.setText(path)

    def _save_backup_settings(self) -> None:
        try:
            settings_service.set_protected({
                "backup.enabled": "1" if self.backup_enabled.isChecked() else "0",
                "backup.keep": str(self.backup_keep.value()),
                "backup.dir": self.backup_dir.text().strip(),
            })
        except AppError as exc:
            show_error(self, exc)
            return
        info(self, "Настройки резервного копирования сохранены.")
        self._reload_backups()

    def _reload_backups(self) -> None:
        self.backups_table.setRowCount(0)
        for backup in backup_service.list_backups():
            index = self.backups_table.rowCount()
            self.backups_table.insertRow(index)
            self.backups_table.setItem(index, 0, cell(backup["name"], data=backup["path"]))
            self.backups_table.setItem(index, 1, cell(backup["created"]))
            self.backups_table.setItem(index, 2, cell(backup["size_mb"], True))
            self.backups_table.setItem(index, 3, cell("да" if backup["photos"] else "нет"))
        self.backups_table.resizeColumnsToContents()

    def _create_backup(self) -> None:
        with_photos = QMessageBox.question(
            self, "Резервная копия", "Включить в копию все фотографии?\n"
            "Это дольше и занимает больше места, зато копия полная.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        try:
            result = backup_service.create_verified(with_photos=with_photos, tag="manual")
        except AppError as exc:
            show_error(self, exc)
            return
        self.last_backup.setText(fmt_dt(settings_service.get("backup.last_run")))
        self._reload_backups()
        self._show_verdict(result)

    def _selected_backup(self) -> str:
        row = self.backups_table.currentRow()
        if row < 0:
            info(self, "Выберите копию в списке ниже.", "Копия не выбрана")
            return ""
        return self.backups_table.item(row, 0).data(Qt.UserRole)

    def _show_verdict(self, result: dict) -> None:
        """
        Показывает результат проверки. «Успешно» не пишется никогда, если
        проверка не прошла — иначе копией нельзя будет пользоваться и никто
        об этом не узнает до самого плохого дня.
        """
        details = "\n".join(f"• {line}" for line in result.get("checks", []))
        where = result.get("exported_to") or ""
        if result["ok"] and not result["problems"]:
            text = result["summary"] + (f"\n\nВыгружена: {where}" if where else "")
            info(self, f"{text}\n\nЧто проверено:\n{details}", "Резервная копия")
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if result["ok"] else QMessageBox.Critical)
        box.setWindowTitle("Резервная копия")
        box.setText(result["summary"])
        box.setInformativeText(
            "Что проверено:\n" + details
            + ("\n\nЗамечания:\n" + "\n".join(f"• {p}" for p in result["problems"])
               if result["problems"] else "")
            + ("" if result["ok"] else
               "\n\nЭтой копией восстановиться не получится. Создайте новую и "
               "убедитесь, что она прошла проверку."))
        box.exec()

    def _verify_backup(self) -> None:
        path = self._selected_backup()
        if not path:
            return
        try:
            result = backup_service.verify(path)
        except AppError as exc:
            show_error(self, exc)
            return
        self._show_verdict(result)

    def _export_backup(self) -> None:
        """Копирование выбранной копии на внешний носитель или в сетевую папку."""
        from ..services import backup_providers

        path = self._selected_backup()
        if not path:
            return
        start = settings_service.get("backup.last_export_where", "")
        folder = QFileDialog.getExistingDirectory(
            self, "Куда скопировать: внешний диск, флешка или сетевая папка",
            str(Path(start).parent) if start else "")
        if not folder:
            return
        try:
            location = backup_service.export_to(
                path, backup_providers.local_provider(folder))
        except AppError as exc:
            show_error(self, exc)
            return
        self.last_export.setText(
            fmt_dt(settings_service.get("backup.last_export")) + " → " + location)
        info(self, f"Копия скопирована:\n{location}\n\n"
                   "Перед проверкой копия была проверена на читаемость.")

    def _show_storages(self) -> None:
        """Список хранилищ: что работает сейчас и что требует подключения."""
        from ..services import backup_providers

        lines = []
        for provider in backup_providers.providers():
            ready, reason = provider.available()
            if provider.key == "local":
                lines.append("• Папка или внешний диск — работает: "
                             "кнопка «Скопировать на внешний диск…»")
                continue
            lines.append(f"• {provider.title} — не подключено. {reason.split('. ', 1)[-1]}")
        info(self, "\n\n".join(lines) + "\n\nОблачные хранилища требуют ключей "
                   "доступа и в поставку не входят. Программа устроена так, что "
                   "такое хранилище можно добавить отдельным модулем без "
                   "переделки остальной части.",
             "Хранилища резервных копий")

    def _restore_backup(self) -> None:
        row = self.backups_table.currentRow()
        if row < 0:
            show_error(self, AppError("Выберите копию в списке."))
            return
        path = self.backups_table.item(row, 0).data(Qt.UserRole)
        if not confirm(self, "Восстановить базу из выбранной копии?\n\n"
                             "Текущая база будет сохранена отдельным файлом before_restore_… — "
                             "она не пропадёт. После восстановления программу нужно перезапустить.",
                       "Восстановление базы"):
            return
        try:
            backup_service.restore(path)
            QMessageBox.information(self, "База восстановлена",
                                    "База восстановлена. Закройте и запустите программу заново.")
        except AppError as exc:
            show_error(self, exc)

    # ---------------------------------------------------------- документы
    def _documents_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(muted("Все сформированные документы. Каждый хранит исторический слепок "
                               "данных на момент создания и не меняется задним числом."))
        self.docs_table = make_table(["Номер", "Тип", "Аренда", "Клиент", "Проект", "Создан"], 0)
        self.docs_table.doubleClicked.connect(self._open_document)
        layout.addWidget(self.docs_table, 1)

        buttons = QHBoxLayout()
        open_btn = QPushButton("Открыть документ")
        open_btn.setObjectName("Primary")
        open_btn.clicked.connect(self._open_document)
        buttons.addWidget(open_btn)
        folder_btn = QPushButton("Открыть папку документов")
        folder_btn.clicked.connect(lambda: open_folder(paths.DOCS_DIR))
        buttons.addWidget(folder_btn)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self._reload_documents)
        buttons.addWidget(refresh)
        buttons.addStretch()
        layout.addLayout(buttons)
        self._reload_documents()
        return page

    def _reload_documents(self) -> None:
        self.docs_table.setRowCount(0)
        for doc in documents_service.list_all():
            index = self.docs_table.rowCount()
            self.docs_table.insertRow(index)
            self.docs_table.setItem(index, 0, cell(doc["number"], data=doc["id"]))
            self.docs_table.setItem(index, 1, cell("Аренда" if doc["doc_type"] == "rental" else "Пролонгация"))
            self.docs_table.setItem(index, 2, cell(doc.get("rental_number") or "—"))
            self.docs_table.setItem(index, 3, cell(doc.get("client_name") or "—"))
            self.docs_table.setItem(index, 4, cell(doc.get("project_name") or "—"))
            self.docs_table.setItem(index, 5, cell(fmt_dt(doc["created_at"])))
        self.docs_table.resizeColumnsToContents()

    def _open_document(self) -> None:
        row = self.docs_table.currentRow()
        if row < 0:
            return
        from .document_dialog import DocumentDialog
        DocumentDialog(self, self.docs_table.item(row, 0).data(Qt.UserRole)).exec()

    # ------------------------------------------------------- поиск по фото
    def _imagesearch_tab(self) -> QWidget:
        """
        Чем искать по фотографии. Встроенный способ работает всегда; остальные
        подключаются пользователем и, пока не подключены, честно показывают,
        чего не хватает.
        """
        from ..services import image_search_providers as registry

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(muted(
            "Встроенный поиск сравнивает снимки по цвету, форме и структуре. Он "
            "работает без интернета и узнаёт тот же самый предмет на другом кадре.\n\n"
            "Нейросетевая модель сравнивает изображения по смыслу и находит похожие "
            "предметы под другим углом и при другом освещении. Она весит сотни "
            "мегабайт, поэтому в поставку не входит — её можно подключить отдельно."))

        box = QGroupBox("Способ поиска")
        form = QFormLayout(box)
        self.search_provider = QComboBox()
        for row in registry.status():
            label = row["title"] + ("" if row["ready"] else "  — не подключён")
            self.search_provider.addItem(label, row["key"])
            if row["selected"]:
                self.search_provider.setCurrentIndex(self.search_provider.count() - 1)
        self.search_provider.setMinimumHeight(38)
        form.addRow("Использовать:", self.search_provider)

        self.model_source = QComboBox()
        for source in registry.MODEL_SOURCES:
            self.model_source.addItem(source["title"], source["key"])
        self.model_source.setMinimumHeight(38)
        form.addRow("Какую модель скачать:", self.model_source)

        self.search_model_path = QLineEdit(
            settings_service.get(registry.SETTING_MODEL_PATH, ""))
        self.search_model_path.setPlaceholderText(
            "по умолчанию — скачанная модель в папке данных")
        model_row = QHBoxLayout()
        model_row.addWidget(self.search_model_path, 1)
        model_pick = QPushButton("Свой файл…")
        model_pick.clicked.connect(self._pick_model)
        model_row.addWidget(model_pick)
        model_widget = QWidget()
        model_widget.setLayout(model_row)
        form.addRow("Файл модели:", model_widget)

        self.search_endpoint = QLineEdit(settings_service.get(registry.SETTING_ENDPOINT, ""))
        self.search_endpoint.setPlaceholderText("https://сервис/embed")
        form.addRow("Адрес сервиса:", self.search_endpoint)
        self.search_api_key = QLineEdit()
        self.search_api_key.setEchoMode(QLineEdit.Password)
        self.search_api_key.setPlaceholderText(
            "ключ сохранён" if settings_service.get_secret(registry.SETTING_KEY_NAME)
            else "ключ доступа")
        form.addRow("Ключ доступа:", self.search_api_key)
        layout.addWidget(box)

        self.search_status = QLabel("")
        self.search_status.setWordWrap(True)
        layout.addWidget(self.search_status)

        buttons = QHBoxLayout()
        download = QPushButton("Скачать модель")
        download.setObjectName("Primary")
        download.setToolTip("Один раз скачивается файл модели; дальше поиск "
                            "работает без интернета")
        download.clicked.connect(self._download_model)
        buttons.addWidget(download)
        save = QPushButton("Сохранить")
        save.clicked.connect(self._save_imagesearch)
        buttons.addWidget(save)
        test = QPushButton("Проверить")
        test.setToolTip("Прогоняет через модель три пробных снимка и смотрит, "
                        "различает ли она их")
        test.clicked.connect(self._test_imagesearch)
        buttons.addWidget(test)
        remove_model = QPushButton("Удалить модель")
        remove_model.setObjectName("Ghost")
        remove_model.clicked.connect(self._remove_model)
        buttons.addWidget(remove_model)
        reindex = QPushButton("Переиндексировать фотографии")
        reindex.setToolTip("Нужно после смены способа поиска: векторы разных "
                           "способов между собой несовместимы")
        reindex.clicked.connect(self._reindex)
        buttons.addWidget(reindex)
        buttons.addStretch()
        layout.addLayout(buttons)

        layout.addWidget(muted(
            "Если выбранный способ окажется недоступен, поиск по фото не сломается: "
            "программа молча вернётся к встроенному алгоритму и напишет об этом "
            "в окне результатов."))
        layout.addStretch()
        self._reload_imagesearch_status()
        return page

    def _reload_imagesearch_status(self) -> None:
        from ..services import image_search_providers as registry

        lines = []
        for row in registry.status():
            mark = "выбран" if row["selected"] else "доступен" if row["ready"] else "нет"
            lines.append(f"{row['title']}: {mark}"
                         + (f" — {row['reason']}" if not row["ready"] else ""))
        clip = registry.get("clip")
        model = clip.model_path()
        if model:
            origin = ("входит в поставку программы" if clip.is_bundled()
                      else "скачана на этот компьютер")
            lines.append(f"Модель нейросети: {origin}, "
                         f"{model.stat().st_size // 1_048_576} МБ")
        stats = image_search.stats()
        lines.append(f"Проиндексировано фотографий: {stats['indexed']} из {stats['photos']}")
        self.search_status.setText("\n".join(lines))

    def _download_model(self) -> None:
        """
        Скачивает файл модели с прогрессом. Отменить можно в любой момент —
        недокачанный файл не останется и за рабочую модель принят не будет.
        """
        from ..services import image_search_providers as registry

        clip = registry.get("clip")
        source = next(m for m in registry.MODEL_SOURCES
                      if m["key"] == self.model_source.currentData())
        if not confirm(self,
                       f"Скачать «{source['title']}»?\n\n"
                       f"Это примерно {source['size_mb']} МБ и делается один раз. "
                       "Дальше поиск по фото работает без интернета — снимки "
                       "никуда не отправляются.",
                       "Загрузка модели"):
            return

        progress = QProgressDialog("Загрузка модели…", "Отмена", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def report(received: int, total: int) -> bool:
            if total:
                progress.setMaximum(total // 1024)
                progress.setValue(received // 1024)
                progress.setLabelText(
                    f"Загрузка модели: {received // 1_048_576} из "
                    f"{total // 1_048_576} МБ")
            else:
                progress.setLabelText(f"Загружено {received // 1_048_576} МБ")
            QApplication.processEvents()
            return not progress.wasCanceled()

        try:
            path = clip.download(source["key"], report)
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Не удалось скачать модель.")
            return
        finally:
            progress.close()

        settings_service.set_protected({registry.SETTING_PROVIDER: "clip"})
        self.search_model_path.clear()
        index = self.search_provider.findData("clip")
        if index >= 0:
            self.search_provider.setCurrentIndex(index)
        self._reload_imagesearch_status()

        try:
            ok, message = clip.check_model()
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Модель скачана, но проверить её не удалось.")
            return
        if not ok:
            show_error(self, AppError(message))
            return
        size = path.stat().st_size // 1_048_576
        if confirm(self, f"Модель скачана ({size} МБ) и проверена.\n{message}\n\n"
                         "Чтобы поиск заработал по всему складу, фотографии нужно "
                         "переиндексировать. Сделать это сейчас?",
                   "Модель готова"):
            self._reindex()

    def _remove_model(self) -> None:
        from ..services import image_search_providers as registry

        clip = registry.get("clip")
        if clip.is_bundled():
            info(self, "Эта модель входит в поставку программы и лежит в её папке. "
                       "Удалить её отсюда нельзя — только вместе с программой.\n\n"
                       "Если нужен встроенный поиск без нейросети, выберите его "
                       "в списке «Использовать» и нажмите «Сохранить».",
                 "Модель из поставки")
            return
        if not confirm(self, "Удалить скачанную модель?\n\nПоиск по фото вернётся "
                             "к встроенному алгоритму.", "Удаление модели"):
            return
        registry.get("clip").remove_model()
        settings_service.set_protected({registry.SETTING_PROVIDER: "local"})
        self.search_model_path.clear()
        index = self.search_provider.findData("local")
        if index >= 0:
            self.search_provider.setCurrentIndex(index)
        self._reload_imagesearch_status()

    def _pick_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Файл модели", "", "Модель ONNX (*.onnx)")
        if path:
            self.search_model_path.setText(path)

    def _save_imagesearch(self) -> None:
        from ..services import image_search_providers as registry

        try:
            settings_service.set_protected({
                registry.SETTING_PROVIDER: self.search_provider.currentData(),
                registry.SETTING_MODEL_PATH: self.search_model_path.text().strip(),
                registry.SETTING_ENDPOINT: self.search_endpoint.text().strip(),
            })
            if self.search_api_key.text().strip():
                settings_service.set_secret(registry.SETTING_KEY_NAME,
                                            self.search_api_key.text().strip())
                self.search_api_key.clear()
                self.search_api_key.setPlaceholderText("ключ сохранён")
        except AppError as exc:
            show_error(self, exc)
            return

        chosen = registry.get(self.search_provider.currentData())
        ready, reason = chosen.available()
        self._reload_imagesearch_status()
        if ready:
            info(self, "Настройки сохранены.\n\nПосле смены способа поиска "
                       "нажмите «Переиндексировать фотографии» — иначе поиск "
                       "будет работать встроенным алгоритмом.")
        else:
            info(self, f"Настройки сохранены, но «{chosen.title}» пока не работает:\n"
                       f"{reason}\n\nПоиск по фото продолжит работать встроенным "
                       "алгоритмом — как раньше.", "Способ поиска не подключён")

    def _test_imagesearch(self) -> None:
        from ..services import image_search_providers as registry

        chosen = registry.get(self.search_provider.currentData())
        if chosen.key == "local":
            info(self, "Встроенный поиск работает всегда и ничего не требует.")
            return
        checker = getattr(chosen, "check_connection", None) or \
            getattr(chosen, "check_model", None)
        if checker is None:
            ready, reason = chosen.available()
            info(self, "Готов к работе." if ready else reason, chosen.title)
            return
        try:
            ready, message = checker()
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Проверка не удалась.")
            return
        info(self, message, chosen.title if ready else "Не работает")

    # ------------------------------------------------------------ о программе
    def _about_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        stats = image_search.stats()
        box = QGroupBox("Служебная информация")
        form = QFormLayout(box)
        form.addRow("Программа:", QLabel(f"{DISPLAY_NAME}  (версия {VERSION})"))
        form.addRow("Оформление:", QLabel(
            "дизайн-система 2.3 — светлая тема, синий акцент, карточки со скруглением"))
        form.addRow("Правило аренды:", QLabel(
            f"цена позиции = аренда на срок до {RENTAL_PERIOD_DAYS} календарных дней, "
            f"далее автоматическая пролонгация"))
        user = auth_service.current_user
        form.addRow("Текущий пользователь:", QLabel(
            f"{user.full_name} — {user.role_label}" if user else "—"))
        form.addRow("База данных:", QLabel(str(paths.DB_PATH)))
        form.addRow("Фотографии:", QLabel(str(paths.PHOTOS_DIR)))
        form.addRow("Документы:", QLabel(str(paths.DOCS_DIR)))
        form.addRow("Резервные копии:", QLabel(str(paths.BACKUPS_DIR)))
        form.addRow("Журнал ошибок:", QLabel(str(paths.LOGS_DIR / "app.log")))
        form.addRow("Поиск по фото:", QLabel(
            f"{stats['provider']} · проиндексировано {stats['indexed']} из {stats['photos']} фото"))
        layout.addWidget(box)

        buttons = QHBoxLayout()
        profile_btn = QPushButton("Мой профиль…")
        profile_btn.clicked.connect(self._open_profile)
        buttons.addWidget(profile_btn)
        data_btn = QPushButton("Открыть папку данных")
        data_btn.clicked.connect(lambda: open_folder(paths.DATA_DIR))
        buttons.addWidget(data_btn)
        log_btn = QPushButton("Открыть папку журналов")
        log_btn.clicked.connect(lambda: open_folder(paths.LOGS_DIR))
        buttons.addWidget(log_btn)
        reindex = QPushButton("Переиндексировать фотографии")
        reindex.clicked.connect(self._reindex)
        buttons.addWidget(reindex)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()
        return page

    # ------------------------------------------------------- пользователи
    def _users_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(muted("Пароли хранятся только в виде криптографического хэша. "
                               "Последнего администратора нельзя удалить или отключить."))
        self.users_table = make_table(
            ["Имя / ФИО", "Должность", "Логин", "Роль", "Состояние", "Создан",
             "Последний вход"], 0)
        layout.addWidget(self.users_table, 1)

        buttons = QHBoxLayout()
        for text, handler, primary in [("Добавить пользователя", self._user_add, True),
                                       ("Изменить", self._user_edit, False),
                                       ("Сменить пароль", self._user_password, False),
                                       ("Включить / отключить", self._user_toggle, False),
                                       ("Удалить", self._user_delete, False)]:
            btn = QPushButton(text)
            if primary:
                btn.setObjectName("Primary")
            btn.clicked.connect(handler)
            buttons.addWidget(btn)
        buttons.addStretch()
        layout.addLayout(buttons)
        self._reload_users()
        return page

    def _reload_users(self) -> None:
        self.users_table.setRowCount(0)
        for user in auth_service.all_users():
            index = self.users_table.rowCount()
            self.users_table.insertRow(index)
            name = (f"{user['first_name']} {user['last_name']}".strip()
                    or user["full_name"])
            self.users_table.setItem(index, 0, cell(name, data=user["id"]))
            self.users_table.setItem(index, 1, cell(user["position"] or "—"))
            self.users_table.setItem(index, 2, cell(user["login"]))
            self.users_table.setItem(index, 3, cell(ROLE_LABELS.get(user["role"], user["role"])))
            self.users_table.setItem(index, 4, cell("активен" if user["is_active"] else "отключён"))
            self.users_table.setItem(index, 5, cell(fmt_dt(user["created_at"])))
            self.users_table.setItem(index, 6, cell(fmt_dt(user["last_login"]) if user["last_login"] else "—"))
        self.users_table.resizeColumnsToContents()

    def _selected_user(self):
        row = self.users_table.currentRow()
        return self.users_table.item(row, 0).data(Qt.UserRole) if row >= 0 else None

    def _user_add(self) -> None:
        from .user_dialog import UserDialog
        if UserDialog(self).exec():
            self._reload_users()

    def _user_edit(self) -> None:
        from .user_dialog import UserDialog
        user_id = self._selected_user()
        if user_id and UserDialog(self, user_id).exec():
            self._reload_users()

    def _user_password(self) -> None:
        user_id = self._selected_user()
        if not user_id:
            return
        password, ok = QInputDialog.getText(self, "Смена пароля", "Новый пароль:",
                                            QLineEdit.Password)
        if not ok:
            return
        confirm_text, ok = QInputDialog.getText(self, "Смена пароля", "Повторите пароль:",
                                                QLineEdit.Password)
        if not ok:
            return
        if password != confirm_text:
            show_error(self, AppError("Пароли не совпадают."))
            return
        try:
            auth_service.set_password(user_id, password)
            info(self, "Пароль изменён.")
        except AppError as exc:
            show_error(self, exc)

    def _user_toggle(self) -> None:
        user_id = self._selected_user()
        if not user_id:
            return
        user = auth_service.get_user(user_id)
        try:
            auth_service.update_user(user_id, is_active=not user["is_active"])
            self._reload_users()
        except AppError as exc:
            show_error(self, exc)

    def _user_delete(self) -> None:
        user_id = self._selected_user()
        if not user_id:
            return
        user = auth_service.get_user(user_id)
        if not confirm(self, f"Удалить пользователя «{user['full_name']}»?"):
            return
        try:
            auth_service.delete_user(user_id)
            self._reload_users()
        except AppError as exc:
            show_error(self, exc)

    # ------------------------------------------------------------- журнал
    # ------------------------------------------------------------ безопасность
    def _security_tab(self) -> QWidget:
        """
        Аварийное восстановление доступа. Вкладка видна только администратору:
        обычный пользователь не должен ни выпускать код, ни знать о его наличии.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(muted(
            "Код восстановления нужен на случай, если пароль администратора забыт. "
            "В базе хранится только его хэш — сам код программа показать не может "
            "и восстановить не может. Потерянный код заменяется новым."))

        self.security_box = QGroupBox("Код аварийного восстановления")
        form = QFormLayout(self.security_box)
        self.security_state = QLabel("")
        self.security_state.setWordWrap(True)
        self.security_issued = QLabel("")
        self.security_missing = QLabel("")
        self.security_missing.setWordWrap(True)
        self.security_lock = QLabel("")
        form.addRow("Состояние:", self.security_state)
        form.addRow("Последняя выдача:", self.security_issued)
        form.addRow("Без кода:", self.security_missing)
        form.addRow("Попытки восстановления:", self.security_lock)
        layout.addWidget(self.security_box)

        buttons = QHBoxLayout()
        issue = QPushButton("Выпустить новый код восстановления")
        issue.setObjectName("Primary")
        issue.clicked.connect(self._issue_recovery_code)
        buttons.addWidget(issue)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self._reload_security)
        buttons.addWidget(refresh)
        buttons.addStretch()
        layout.addLayout(buttons)

        layout.addWidget(muted(
            "Выпуск нового кода немедленно гасит предыдущий. Печатайте новый лист "
            "и убирайте его в сейф, старый уничтожайте."))
        layout.addStretch()
        self._reload_security()
        return page

    def _reload_security(self) -> None:
        try:
            state = recovery_service.status()
        except AppError as exc:
            self.security_state.setText(str(exc))
            return
        codes = state["active_codes"]
        self.security_state.setText(
            f"действующих кодов: {codes}" if codes
            else "действующего кода нет — выпустите его прямо сейчас")
        self.security_issued.setText(fmt_dt(state["last_issued"]) if state["last_issued"] else "—")
        missing = state["admins_without_code"]
        self.security_missing.setText(
            ", ".join(u["full_name"] for u in missing) if missing
            else "у всех администраторов код есть")
        left = state["failed_attempts"]
        locked = state["locked_until"]
        self.security_lock.setText(
            f"неудачных попыток: {left}" + (f", восстановление заблокировано до {fmt_dt(locked)}"
                                            if locked else ""))

    def _issue_recovery_code(self) -> None:
        if not confirm(self,
                       "Выпустить новый код восстановления?\n\n"
                       "Прежний код сразу перестанет работать. Новый нужно будет "
                       "распечатать и убрать в надёжное место.",
                       "Новый код восстановления"):
            return
        try:
            code = recovery_service.reissue_for_current_admin()
        except AppError as exc:
            show_error(self, exc)
            return
        user = auth_service.current_user
        from .recovery_dialog import RecoveryCodeDialog
        RecoveryCodeDialog(code, user.full_name if user else "", self).exec()
        self._reload_security()

    def _audit_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(muted("Кто, когда и что сделал. Автоматические операции "
                               "записываются от имени пользователя «Система»."))
        search_row = QHBoxLayout()
        self.audit_search = QLineEdit()
        self.audit_search.setPlaceholderText("Поиск по пользователю, действию или подробностям…")
        self.audit_search.setClearButtonEnabled(True)
        self.audit_search.textChanged.connect(self._reload_audit)
        search_row.addWidget(self.audit_search, 1)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self._reload_audit)
        search_row.addWidget(refresh)
        layout.addLayout(search_row)

        self.audit_table = make_table(
            ["Дата и время", "Пользователь", "Действие", "Объект", "ID", "Подробности", "Сумма"], 5)
        layout.addWidget(self.audit_table, 1)
        self._reload_audit()
        return page

    def _reload_audit(self) -> None:
        self.audit_table.setRowCount(0)
        for entry in audit_service.search(self.audit_search.text()):
            index = self.audit_table.rowCount()
            self.audit_table.insertRow(index)
            self.audit_table.setItem(index, 0, cell(fmt_dt(entry["created_at"])))
            self.audit_table.setItem(index, 1, cell(entry["user_name"]))
            self.audit_table.setItem(index, 2, cell(entry["action"]))
            self.audit_table.setItem(index, 3, cell(entry["entity"]))
            self.audit_table.setItem(index, 4, cell(entry["entity_id"] or "", True))
            self.audit_table.setItem(index, 5, cell(entry["details"]))
            self.audit_table.setItem(index, 6, cell(
                money_plain(entry["amount"]) if entry["amount"] else "", True))
        self.audit_table.resizeColumnsToContents()

    # -------------------------------------------------- ограничение прав
    def _apply_permissions(self) -> None:
        """Обычному пользователю административные настройки только для чтения."""
        if auth_service.is_admin():
            return
        hint = "Изменять настройки программы может только администратор"
        for widget in list(self.wh_fields.values()) + [self.logo_path]:
            widget.setReadOnly(True)
            widget.setToolTip(hint)
        self.wh_extra.setReadOnly(True)
        for widget in (self.smtp_host, self.smtp_login, self.smtp_password, self.smtp_from):
            widget.setReadOnly(True)
            widget.setToolTip(hint)
        for widget in (self.smtp_port, self.smtp_ssl, self.backup_enabled, self.backup_keep,
                       self.backup_dir):
            widget.setEnabled(False)
            widget.setToolTip(hint)

    def _open_profile(self) -> None:
        from .profile_dialog import ProfileDialog
        ProfileDialog(self).exec()

    def _reindex(self) -> None:
        progress = QProgressDialog("Индексация фотографий…", "Отмена", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)

        def report(done: int, total: int) -> None:
            progress.setMaximum(max(1, total))
            progress.setValue(done)

        try:
            count = image_search.reindex_all(report)
            progress.close()
            info(self, f"Проиндексировано фотографий: {count}")
        except Exception as exc:
            progress.close()
            show_error(self, exc, "Не удалось переиндексировать фотографии.")
