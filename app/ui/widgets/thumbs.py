# -*- coding: utf-8 -*-
"""
Миниатюры для каталога: загрузка в фоне и кэш в памяти.

Каталог на тысячи предметов нельзя рисовать «в лоб»: чтение и уменьшение
сотни файлов в потоке интерфейса даёт заметную паузу при каждом открытии и
прокрутке. Здесь файл читается в фоновом потоке, уже уменьшенным до нужного
размера, а готовая картинка складывается в кэш с ограничением по числу —
память не растёт вместе со складом.

Оригиналы и рабочие копии фотографий при этом не меняются: уменьшается
только то, что показывается на экране.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

CACHE_LIMIT = 600          # картинок в памяти; при 300×300 — около 200 МБ в пределе,
                           # обычная плитка 240×170 — около 100 МБ


class _Signals(QObject):
    done = Signal(str, int, int, QImage)


class _Job(QRunnable):
    def __init__(self, path: str, width: int, height: int, signals: _Signals):
        super().__init__()
        self.path, self.width, self.height, self.signals = path, width, height, signals
        self.setAutoDelete(True)

    def run(self) -> None:                                   # noqa: D401 — интерфейс Qt
        image = QImage()
        try:
            source = self.path
            if not Path(source).exists():
                # миниатюра пропала — восстановим из фотографии, если она есть
                source = _restore_thumb(source) or source
            reader = QImageReader(source)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > 0 and size.height() > 0:
                scaled = size.scaled(QSize(self.width, self.height), Qt.KeepAspectRatio)
                # уменьшение при чтении: JPEG декодируется сразу в нужном размере
                reader.setScaledSize(scaled)
            image = reader.read()
        except Exception:                                    # noqa: BLE001
            image = QImage()
        try:
            self.signals.done.emit(self.path, self.width, self.height, image)
        except RuntimeError:
            pass                 # окно уже закрыто — результат никому не нужен


def _restore_thumb(thumb: str) -> str:
    """Путь к восстановленной миниатюре или пустая строка."""
    try:
        from ... import paths
        from ...services.photo_service import ensure_thumb

        path = Path(thumb)
        if not path.stem.endswith("_thumb"):
            return ""
        original = path.with_name(path.stem[:-len("_thumb")] + path.suffix)
        rel = original.relative_to(paths.PHOTOS_DIR).as_posix()
        restored = ensure_thumb(rel)
        return str(restored) if restored else ""
    except Exception:                                        # noqa: BLE001
        return ""


class ThumbnailLoader(QObject):
    """Отдаёт миниатюру из кэша, а если её там нет — заказывает загрузку."""

    loaded = Signal(str)          # путь файла, чья картинка готова

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        self._pending: set[tuple] = set()
        self._missing: set[str] = set()
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(4, QThreadPool.globalInstance().maxThreadCount())))

    def get(self, path: str, width: int, height: int) -> QPixmap | None:
        """Картинка из кэша или None (тогда она загрузится и придёт сигнал loaded)."""
        if not path or path in self._missing:
            return None
        key = (path, width, height)
        pixmap = self._cache.get(key)
        if pixmap is not None:
            self._cache.move_to_end(key)
            return pixmap
        if key not in self._pending:
            self._pending.add(key)
            self._pool.start(_Job(path, width, height, self._signals))
        return None

    def _on_done(self, path: str, width: int, height: int, image: QImage) -> None:
        key = (path, width, height)
        self._pending.discard(key)
        if image.isNull():
            self._missing.add(path)          # файла нет или он не читается
        else:
            self._cache[key] = QPixmap.fromImage(image)
            while len(self._cache) > CACHE_LIMIT:
                self._cache.popitem(last=False)
        self.loaded.emit(path)

    def forget(self, path: str = "") -> None:
        """Сбрасывает кэш файла (фото заменили) или весь кэш."""
        if not path:
            self._cache.clear()
            self._missing.clear()
            return
        self._missing.discard(path)
        for key in [k for k in self._cache if k[0] == path]:
            del self._cache[key]

    def wait(self, msecs: int = 3000) -> None:
        """Дожидается фоновых загрузок (нужно тестам и снимкам экранов)."""
        self._pool.waitForDone(msecs)

    def stop(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)


_loader: ThumbnailLoader | None = None


def thumbnails() -> ThumbnailLoader:
    """Один загрузчик на программу: каталог, поиск по фото и подбор в аренду делят кэш."""
    global _loader
    if _loader is None:
        _loader = ThumbnailLoader()
    return _loader
