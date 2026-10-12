# -*- coding: utf-8 -*-
"""
Провайдеры визуального поиска: чем именно считать «похожесть» фотографий.

Встроенный провайдер (LocalVisualProvider из image_search) работает офлайн и
без зависимостей: гистограммы, перцептивный хэш, сетка цветов. Он узнаёт тот
же самый предмет на другом снимке, но не понимает смысла: «красный деревянный
стул» и «похожий красный деревянный стул другой модели» для него разные вещи.

Нейросетевая модель (CLIP и подобные) сравнивает изображения по смыслу и
находит похожие предметы под другим углом, при другом свете и на другом фоне.
Но она весит сотни мегабайт и требует своей библиотеки, поэтому в поставку не
входит — здесь описано, как её подключить, и предусмотрено место.

Что здесь есть на самом деле:
  • local  — работает всегда, это текущий поиск версии 2.3;
  • clip   — работает после разовой загрузки файла модели (кнопка в настройках);
             дальше всё считается на этом компьютере, без интернета;
  • remote — работает, ЕСЛИ указан адрес совместимого сервиса и ключ доступа.

Ни один из них не притворяется работающим: available() возвращает причину, а
поиск при недоступном провайдере откатывается на встроенный, а не молчит.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Protocol

from .. import paths
from ..errors import AppError, ValidationError
from .audit_service import audit_service
from .settings_service import settings_service

log = logging.getLogger(__name__)

SETTING_PROVIDER = "imagesearch.provider"
SETTING_MODEL_PATH = "imagesearch.model_path"
SETTING_ENDPOINT = "imagesearch.endpoint"
SETTING_KEY_NAME = "imagesearch.api_key"
REQUEST_TIMEOUT = 20


class VisualProvider(Protocol):
    """
    Общий интерфейс для любого способа считать вектор изображения.

    Требования к реализации:
      • embed() возвращает список чисел одинаковой длины для любых картинок;
      • available() честно сообщает, готов ли провайдер и чего не хватает;
      • ошибки поднимаются, а не проглатываются: молчаливый провайдер, который
        возвращает мусор, хуже отсутствующего.
    """

    key: str
    name: str          # идентификатор для хранения векторов в базе
    title: str         # что видит пользователь
    dim: int

    def available(self) -> tuple[bool, str]:
        ...

    def embed(self, image_path: Path) -> list[float]:
        ...


# Готовые ONNX-сборки CLIP ViT-B/32 (энкодер изображений). Порядок — от
# самого лёгкого к самому «полному»: квантованная модель весит примерно
# впятеро меньше и на складских снимках работает практически так же.
MODEL_SOURCES = [
    {
        "key": "clip-vit-b32-q",
        "title": "CLIP ViT-B/32, сжатая (~90 МБ)",
        "url": "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
               "onnx/vision_model_quantized.onnx",
        "size_mb": 90,
    },
    {
        "key": "clip-vit-b32",
        "title": "CLIP ViT-B/32, полная (~350 МБ)",
        "url": "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
               "onnx/vision_model.onnx",
        "size_mb": 350,
    },
    {
        "key": "clip-vit-b32-qdrant",
        "title": "CLIP ViT-B/32, зеркало Qdrant (~350 МБ)",
        "url": "https://huggingface.co/Qdrant/clip-ViT-B-32-vision/resolve/main/model.onnx",
        "size_mb": 350,
    },
]
MIN_MODEL_BYTES = 5 * 1024 * 1024      # меньше — точно не модель, а страница ошибки


class ClipOnnxProvider:
    """
    Локальная нейросеть CLIP в формате ONNX: поиск по смыслу изображения.

    Работает офлайн. Интернет нужен один раз — скачать файл модели; дальше он
    лежит в папке данных и никуда не отправляется. Отпечаток считается на
    процессоре, видеокарта не нужна.

    Предобработка сделана как у оригинального CLIP: короткая сторона снимка
    приводится к 224 пикселям, из центра берётся квадрат, значения
    нормализуются. Если этого не сделать, а просто сжать кадр в квадрат,
    пропорции поедут и модель начнёт путать предметы.
    """

    key = "clip"
    title = "Нейросеть CLIP (локально)"
    dim = 512

    _MEAN = (0.48145466, 0.4578275, 0.40821073)
    _STD = (0.26862954, 0.26130258, 0.27577711)
    SIZE = 224

    def __init__(self):
        self._session = None
        self._session_path: Optional[Path] = None

    @property
    def name(self) -> str:
        """Векторы разных моделей несовместимы, поэтому имя включает файл."""
        model = self.model_path()
        return f"clip-onnx:{model.stem}" if model else "clip-onnx"

    # ------------------------------------------------------------- файл модели
    @staticmethod
    def models_dir() -> Path:
        return paths.DATA_DIR / "models"

    @classmethod
    def default_path(cls) -> Path:
        return cls.models_dir() / "clip_image.onnx"

    @staticmethod
    def bundled_path() -> Path:
        """
        Модель, поставляемая вместе с программой.

        Если при сборке в assets/models положили файл модели, он попадает в
        установщик, и на новом компьютере нейросетевой поиск работает сразу,
        без загрузки из интернета.
        """
        return paths.assets_dir() / "models" / "clip_image.onnx"

    @classmethod
    def model_path(cls) -> Optional[Path]:
        """
        Какой файл модели использовать. Порядок: путь, заданный вручную →
        скачанная модель в папке данных → модель из поставки. Скачанная
        главнее встроенной: если пользователь сам загрузил другую, значит,
        хотел именно её.
        """
        configured = settings_service.get(SETTING_MODEL_PATH, "")
        if configured:
            path = Path(configured)
            return path if path.exists() else None
        for candidate in (cls.default_path(), cls.bundled_path()):
            if candidate.exists() and candidate.stat().st_size >= MIN_MODEL_BYTES:
                return candidate
        return None

    @classmethod
    def is_bundled(cls) -> bool:
        """Модель пришла с установщиком, а не скачана пользователем."""
        model = cls.model_path()
        return bool(model) and model == cls.bundled_path()

    def available(self) -> tuple[bool, str]:
        # Порядок важен. Сначала — есть ли файл модели: если нет, движок
        # проверять незачем. Потом — запускается ли движок, и это делается в
        # ОТДЕЛЬНОМ процессе: на старом процессоре onnxruntime роняет процесс
        # целиком, и проверка внутри программы закрыла бы её без сообщения.
        # Только после этого библиотеки импортируются здесь.
        if not self.model_path():
            return False, ("Файл модели не скачан. Откройте «Настройки → Поиск по "
                           "фото» и нажмите «Скачать модель» — это один раз.")
        from .onnx_probe import runtime_ok

        ready, reason = runtime_ok()
        if not ready:
            return False, reason + " Поиск по фото работает встроенным алгоритмом."
        try:
            import numpy                                            # noqa: F401
        except Exception as exc:                                    # noqa: BLE001
            return False, ("Библиотека numpy не загружается на этом компьютере "
                           f"({exc}). Нейросетевой поиск недоступен, работает "
                           "встроенный.")
        try:
            import onnxruntime                                      # noqa: F401
        except Exception as exc:                                    # noqa: BLE001
            return False, ("Библиотека onnxruntime не загружается на этом "
                           f"компьютере ({exc}). Нейросетевой поиск недоступен, "
                           "работает встроенный.")
        return True, ""

    # -------------------------------------------------------------- загрузка
    def download(self, source_key: str = "", progress=None) -> Path:
        """
        Скачивает модель в папку данных.

        Файл пишется под временным именем и переименовывается только целиком:
        оборванная закачка не оставит после себя огрызок, который потом примут
        за рабочую модель. Прогресс отдаётся в progress(получено, всего).
        """
        import urllib.error
        import urllib.request

        source = next((m for m in MODEL_SOURCES if m["key"] == source_key),
                      MODEL_SOURCES[0])
        target = self.default_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".onnx.part")

        try:
            request = urllib.request.Request(
                source["url"], headers={"User-Agent": "SkladRekvizita/2.4"})
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0)
                received = 0
                with open(temporary, "wb") as out:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
                        received += len(chunk)
                        if progress and not progress(received, total):
                            raise AppError("Загрузка отменена.")
        except urllib.error.HTTPError as exc:
            temporary.unlink(missing_ok=True)
            raise AppError(f"Сервер не отдал файл модели: {exc.code} {exc.reason}. "
                           "Попробуйте другой источник в списке.") from exc
        except urllib.error.URLError as exc:
            temporary.unlink(missing_ok=True)
            raise AppError(f"Не удалось скачать модель: {exc.reason}. "
                           "Проверьте подключение к интернету.") from exc
        except AppError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise AppError(f"Не удалось сохранить файл модели: {exc}") from exc

        if temporary.stat().st_size < MIN_MODEL_BYTES:
            size = temporary.stat().st_size
            temporary.unlink(missing_ok=True)
            raise AppError(
                f"Скачанный файл слишком мал ({size // 1024} КБ) — это не модель, "
                "а сообщение об ошибке. Попробуйте другой источник.")

        self._session = None                    # старая модель больше не нужна
        temporary.replace(target)
        settings_service.set(SETTING_MODEL_PATH, "")   # используем путь по умолчанию
        audit_service.log("Скачана модель поиска по фото",
                          details=f"{source['title']}, "
                                  f"{target.stat().st_size // 1_048_576} МБ")
        log.info("Модель поиска по фото сохранена: %s", target)
        return target

    def remove_model(self) -> None:
        """
        Удаляет скачанную модель — если место на диске нужнее. Модель из
        поставки не трогаем: она лежит в папке программы и уйдёт вместе с ней.
        """
        self._session = None
        self.default_path().unlink(missing_ok=True)
        settings_service.set(SETTING_MODEL_PATH, "")

    # ------------------------------------------------------------- вычисление
    def _load(self):
        model = self.model_path()
        if self._session is not None and self._session_path == model:
            return self._session
        ready, reason = self.available()
        if not ready:
            raise AppError(reason)
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3          # модель не должна сорить в консоль
        try:
            self._session = onnxruntime.InferenceSession(
                str(model), sess_options=options, providers=["CPUExecutionProvider"])
        except Exception as exc:                                   # noqa: BLE001
            raise AppError(
                f"Файл модели не читается ({exc}). Скачайте модель заново.") from exc
        self._session_path = model
        log.info("Модель визуального поиска загружена: %s", model)
        return self._session

    def _preprocess(self, image_path: Path):
        """Кадр → тензор 1×3×224×224 по правилам CLIP."""
        import numpy as np
        from PIL import Image

        from .photo_service import enable_heic

        enable_heic()
        with Image.open(image_path) as image:
            image = image.convert("RGB")
            width, height = image.size
            scale = self.SIZE / min(width, height)
            image = image.resize((max(self.SIZE, round(width * scale)),
                                  max(self.SIZE, round(height * scale))),
                                 Image.BICUBIC)
            width, height = image.size
            left = (width - self.SIZE) // 2
            top = (height - self.SIZE) // 2
            image = image.crop((left, top, left + self.SIZE, top + self.SIZE))
            data = np.asarray(image, dtype=np.float32) / 255.0

        data = (data - np.array(self._MEAN, dtype=np.float32)) / \
            np.array(self._STD, dtype=np.float32)
        return data.transpose(2, 0, 1)[None, ...].astype(np.float32)

    def embed(self, image_path: Path) -> list[float]:
        import numpy as np

        session = self._load()
        tensor = self._preprocess(Path(image_path))
        input_name = session.get_inputs()[0].name
        try:
            outputs = session.run(None, {input_name: tensor})
        except Exception as exc:                                   # noqa: BLE001
            raise AppError(f"Модель не смогла обработать снимок: {exc}") from exc

        # У разных сборок CLIP выходов бывает несколько (вектор и карта
        # признаков). Берём первый двумерный — это и есть отпечаток кадра.
        vector = None
        for output in outputs:
            array = np.asarray(output)
            if array.size and array.ndim >= 2:
                vector = array.reshape(array.shape[0], -1)[0]
                break
        if vector is None:
            raise AppError("Модель вернула пустой результат.")
        vector = vector.astype(np.float64)
        norm = float(np.linalg.norm(vector)) or 1.0
        return (vector / norm).tolist()

    # ---------------------------------------------------------- самопроверка
    def check_model(self) -> tuple[bool, str]:
        """
        Проверяет, что модель действительно работает и различает изображения.

        Считает отпечатки трёх картинок: две похожие и одна совсем другая.
        Похожие должны оказаться ближе друг к другу, чем к третьей. Если это
        не так, модель загрузилась, но пользы от неё нет, и лучше сказать об
        этом сразу, чем после переиндексации всего склада.
        """
        from .onnx_probe import runtime_ok

        runtime_ok(force=True)          # по кнопке «Проверить» — всегда заново
        ready, reason = self.available()
        if not ready:
            return False, reason
        from PIL import Image, ImageDraw

        folder = paths.TEMP_DIR / "clip_selftest"
        folder.mkdir(parents=True, exist_ok=True)
        samples = {}
        for name, color, box in (("a", (200, 40, 30), (40, 40, 200, 200)),
                                 ("b", (205, 45, 35), (45, 45, 205, 205)),
                                 ("c", (30, 60, 200), (10, 120, 60, 140))):
            path = folder / f"{name}.png"
            image = Image.new("RGB", (400, 300), (245, 245, 245))
            ImageDraw.Draw(image).rectangle(box, fill=color)
            image.save(path)
            samples[name] = path

        try:
            vectors = {name: self.embed(path) for name, path in samples.items()}
        except AppError as exc:
            return False, str(exc)
        finally:
            for path in samples.values():
                path.unlink(missing_ok=True)

        length = len(vectors["a"])
        if length < 32:
            return False, (f"Модель вернула слишком короткий вектор ({length} чисел) — "
                           "похоже, файл не является энкодером изображений.")

        def cosine(first, second):
            return sum(x * y for x, y in zip(first, second))

        near = cosine(vectors["a"], vectors["b"])
        far = cosine(vectors["a"], vectors["c"])
        if near <= far:
            return False, ("Модель загрузилась, но не отличает похожие снимки от "
                           "разных. Скачайте модель заново или выберите другой "
                           "источник.")
        return True, (f"Модель работает: вектор из {length} чисел, похожие снимки "
                      f"{near:.2f}, разные {far:.2f}.")


class RemoteApiProvider:
    """
    Внешний сервис, считающий векторы изображений.

    Контракт простой и намеренно не привязан к конкретному поставщику: на
    указанный адрес отправляется POST с JSON вида
    {"image": "<файл в base64>"}, в ответ ожидается {"embedding": [числа]}.
    Ключ доступа уходит в заголовке Authorization и хранится в базе
    зашифрованным, как пароль почты.

    ЧЕСТНО: совместимого сервиса в поставке нет и проверить эту ветку не на
    чем. Пока адрес и ключ не заданы, провайдер сообщает, что не настроен, и
    поиск работает встроенным алгоритмом.
    """

    key = "remote"
    title = "Внешний сервис (API)"
    dim = 0            # выясняется по первому ответу

    @property
    def name(self) -> str:
        endpoint = settings_service.get(SETTING_ENDPOINT, "")
        host = endpoint.split("//")[-1].split("/")[0] if endpoint else "none"
        return f"remote:{host}"

    @staticmethod
    def endpoint() -> str:
        return (settings_service.get(SETTING_ENDPOINT, "") or "").strip()

    @staticmethod
    def api_key() -> str:
        return (settings_service.get_secret(SETTING_KEY_NAME) or "").strip()

    def available(self) -> tuple[bool, str]:
        if not self.endpoint():
            return False, ("Не указан адрес сервиса. Введите его в настройках "
                           "поиска по фото.")
        if not self.endpoint().startswith(("http://", "https://")):
            return False, "Адрес сервиса должен начинаться с http:// или https://"
        if not self.api_key():
            return False, "Не задан ключ доступа к сервису."
        return True, ""

    def embed(self, image_path: Path) -> list[float]:
        import base64
        import urllib.error
        import urllib.request

        ready, reason = self.available()
        if not ready:
            raise AppError(reason)

        payload = json.dumps({
            "image": base64.b64encode(Path(image_path).read_bytes()).decode("ascii"),
        }).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint(), data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key()}"})
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                answer = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AppError(f"Сервис отказал: {exc.code} {exc.reason}. "
                           "Проверьте ключ доступа.") from exc
        except urllib.error.URLError as exc:
            raise AppError(f"Не удалось связаться с сервисом: {exc.reason}. "
                           "Проверьте адрес и подключение к интернету.") from exc
        except (ValueError, TypeError) as exc:
            raise AppError("Сервис вернул неожиданный ответ.") from exc

        vector = answer.get("embedding") or answer.get("vector")
        if not isinstance(vector, list) or not vector:
            raise AppError("Сервис не вернул вектор признаков.")
        try:
            vector = [float(v) for v in vector]
        except (TypeError, ValueError) as exc:
            raise AppError("Сервис вернул вектор в неизвестном формате.") from exc
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    def check_connection(self) -> tuple[bool, str]:
        """Проверка связи: отправляет крошечную картинку и смотрит на ответ."""
        ready, reason = self.available()
        if not ready:
            return False, reason
        from PIL import Image

        probe = paths.TEMP_DIR / "imagesearch_probe.png"
        probe.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 32), (128, 128, 128)).save(probe)
        try:
            vector = self.embed(probe)
        except AppError as exc:
            return False, str(exc)
        finally:
            probe.unlink(missing_ok=True)
        return True, f"Связь есть, сервис вернул вектор из {len(vector)} чисел."


_PROVIDERS: dict[str, VisualProvider] = {}


def register(provider: VisualProvider) -> None:
    _PROVIDERS[provider.key] = provider


def providers() -> list[VisualProvider]:
    return list(_PROVIDERS.values())


def get(key: str) -> VisualProvider:
    provider = _PROVIDERS.get(key)
    if not provider:
        raise ValidationError(f"Способ поиска «{key}» программе неизвестен.")
    return provider


def selected_key() -> str:
    """
    Выбранный способ поиска. Если пользователь ничего не выбирал, а модель
    пришла вместе с программой, по умолчанию работает нейросеть — ради этого
    её и положили в установщик. Явный выбор в настройках всегда главнее.
    """
    chosen = settings_service.get(SETTING_PROVIDER, "")
    if chosen:
        return chosen
    try:
        clip = _PROVIDERS.get("clip")
        if clip is not None and ClipOnnxProvider.bundled_path().exists():
            ready, _ = clip.available()
            if ready:
                return "clip"
    except Exception:                                              # noqa: BLE001
        log.exception("Не удалось проверить встроенную модель")
    return "local"


def status() -> list[dict]:
    """Состояние всех провайдеров — для экрана настроек."""
    current = selected_key()
    out = []
    for provider in providers():
        ready, reason = provider.available()
        out.append({"key": provider.key, "title": provider.title, "ready": ready,
                    "reason": reason, "selected": provider.key == current})
    return out


def _register_defaults() -> None:
    from .image_search import LocalVisualProvider

    local = LocalVisualProvider()
    local.key = "local"
    local.title = "Встроенный (без интернета)"
    local.available = lambda: (True, "")          # работает всегда
    register(local)
    register(ClipOnnxProvider())
    register(RemoteApiProvider())


_register_defaults()
