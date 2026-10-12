# -*- coding: utf-8 -*-
"""
Работа с веб-камерой Windows: живое изображение, снимок, сохранение файла.

Используется QtMultimedia (входит в PySide6) — отдельных драйверов не требуется.
Если камеры нет, диалог честно об этом сообщает и предлагает выбрать файл.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from .. import paths
from .theme import Color, Space
from ..utils import now

log = logging.getLogger(__name__)


class CameraDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Съёмка с камеры")
        self.resize(760, 620)
        self.captured_path: Path | None = None
        self._session = None
        self._camera = None
        self._capture = None
        self._build()
        self._start_camera()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        self.device_box = QComboBox()
        root.addWidget(self.device_box)

        self.viewfinder_holder = QVBoxLayout()
        root.addLayout(self.viewfinder_holder, 1)

        self.hint = QLabel("Наведите камеру на предмет и нажмите «Сделать снимок».")
        self.hint.setAlignment(Qt.AlignCenter)
        root.addWidget(self.hint)

        buttons = QHBoxLayout()
        self.shot_btn = QPushButton("Сделать снимок")
        self.shot_btn.setObjectName("Primary")
        self.shot_btn.setMinimumHeight(44)
        self.shot_btn.clicked.connect(self._take_shot)
        buttons.addWidget(self.shot_btn)
        file_btn = QPushButton("Выбрать файл…")
        file_btn.clicked.connect(self._pick_file)
        buttons.addWidget(file_btn)
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        root.addLayout(buttons)

    def _start_camera(self) -> None:
        try:
            from PySide6.QtMultimedia import (QCamera, QImageCapture, QMediaCaptureSession,
                                              QMediaDevices)
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except ImportError:
            self._no_camera("Модуль работы с камерой недоступен в этой сборке.")
            return

        devices = QMediaDevices.videoInputs()
        if not devices:
            self._no_camera("Камера не найдена. Подключите веб-камеру или выберите файл.")
            return
        for device in devices:
            self.device_box.addItem(device.description(), device)
        self.device_box.currentIndexChanged.connect(self._switch_device)

        self.video = QVideoWidget()
        self.viewfinder_holder.addWidget(self.video)
        self._session = QMediaCaptureSession()
        self._capture = QImageCapture()
        self._session.setImageCapture(self._capture)
        self._session.setVideoOutput(self.video)
        self._capture.imageSaved.connect(self._on_saved)
        self._capture.errorOccurred.connect(
            lambda *_: self.hint.setText("Не удалось сделать снимок. Попробуйте ещё раз."))
        self._switch_device(0)

    def _switch_device(self, index: int) -> None:
        from PySide6.QtMultimedia import QCamera
        if self._camera:
            self._camera.stop()
        device = self.device_box.itemData(index)
        if device is None:
            return
        self._camera = QCamera(device)
        self._session.setCamera(self._camera)
        self._camera.start()

    def _no_camera(self, message: str) -> None:
        label = QLabel(message)
        label.setAlignment(Qt.AlignCenter)
        label.setObjectName("EmptyNote")
        self.viewfinder_holder.addWidget(label)
        self.shot_btn.setEnabled(False)
        self.device_box.setVisible(False)

    def _take_shot(self) -> None:
        paths.ensure_dirs()
        target = paths.TEMP_DIR / f"shot_{now():%Y%m%d_%H%M%S}.jpg"
        try:
            self._capture.captureToFile(str(target))
        except Exception:
            log.exception("Ошибка захвата кадра")
            self.hint.setText("Не удалось сделать снимок.")

    def _on_saved(self, _id: int, file_name: str) -> None:
        self.captured_path = Path(file_name)
        QTimer.singleShot(150, self.accept)

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите изображение", "", "Изображения (*.jpg *.jpeg *.png *.bmp *.webp *.heic *.heif)")
        if path:
            self.captured_path = Path(path)
            self.accept()

    def closeEvent(self, event):
        if self._camera:
            self._camera.stop()
        super().closeEvent(event)
