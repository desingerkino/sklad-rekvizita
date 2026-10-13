# -*- coding: utf-8 -*-
"""
СКЛАД РЕКВИЗИТА 2.0 — точка входа приложения.

Запуск в разработке:  python main.py
Собранное приложение: SkladRekvizita.exe

Порядок запуска: подготовка папок → журнал → база и миграции → экран входа →
главное окно. Без успешного входа в основную программу попасть нельзя.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    # Служебный запуск: собранная программа вызывает саму себя с этим ключом,
    # чтобы проверить движок нейросети в отдельном процессе. Окно не нужно,
    # база не нужна — только проверка и выход.
    if "--probe-onnx" in sys.argv:
        from app.services.onnx_probe import run_probe_inline
        return run_probe_inline()

    from app import paths
    from app.logging_setup import setup as setup_logging
    from app.version import APP_ID, DISPLAY_NAME, VERSION

    paths.ensure_dirs()
    if sys.platform == "darwin":
        # у Python на macOS нет системных сертификатов: без этого любая загрузка
        # по https (например, смена модели поиска по фото) падает с CERTIFICATE_VERIFY_FAILED
        try:
            import certifi
            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        except Exception:                                    # noqa: BLE001
            pass
    log = setup_logging()
    log.info("Запуск %s (версия %s)", DISPLAY_NAME, VERSION)

    from PySide6.QtCore import Qt, qVersion
    from PySide6.QtWidgets import QApplication, QMessageBox

    # Начиная с Qt 6 масштабирование под высокое разрешение включено всегда,
    # а старый флаг AA_EnableHighDpiScaling объявлен устаревшим и печатает
    # предупреждение при каждом запуске. Ставим его только там, где он ещё
    # что-то значит, — на Qt 5.
    if qVersion().split(".")[0] == "5" and hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(DISPLAY_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("SkladRekvizita")

    from app.paths import assets_dir
    icon_path = assets_dir() / "app.ico"
    if icon_path.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))

    from app.ui.brand import default_font, load_fonts
    if load_fonts():
        app.setFont(default_font())

    from app.ui.theme import QSS
    app.setStyleSheet(QSS)

    try:
        from app.db.database import get_db
        from app.db.seed import seed_if_empty
        db = get_db()            # здесь же выполняются миграции с версии 1.x
        seed_if_empty(db)
    except Exception:
        log.exception("Не удалось открыть базу данных")
        QMessageBox.critical(
            None, "База данных недоступна",
            "Не удалось открыть базу данных.\n\n"
            f"Проверьте папку:\n{paths.DATA_DIR}\n\n"
            "Если база повреждена, восстановите её из резервной копии в папке backups.")
        return 1

    from app.services.auth_service import auth_service
    from app.ui.login_dialog import run_login
    from app.ui.main_window import MainWindow

    # Цикл «вход → работа → выход»: после выхода снова показывается экран входа.
    while True:
        if not run_login():
            log.info("Вход не выполнен, выход из программы")
            return 0

        window = MainWindow()
        state = {"relogin": False}
        window.logout_requested.connect(lambda: state.update(relogin=True))
        window.show()
        app.exec()

        if not state["relogin"]:
            break
        auth_service.logout()

    try:
        from app.db.database import get_db as close_db
        close_db().close()
    except Exception:
        log.exception("Ошибка при закрытии базы")
    return 0


if __name__ == "__main__":
    sys.exit(main())
