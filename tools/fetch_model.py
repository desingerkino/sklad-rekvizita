# -*- coding: utf-8 -*-
"""
Кладёт файл нейросети CLIP в assets/models/clip_image.onnx, чтобы он попал в exe/dmg.

Программа уже умеет брать модель «из поставки» (ClipOnnxProvider.bundled_path), поэтому
код программы не меняется: достаточно положить файл перед сборкой.
Берётся сжатая версия (~90 МБ): на складских снимках она работает почти так же, как полная.

    python tools/fetch_model.py            # скачать (если ещё нет) и проверить
    python tools/fetch_model.py --verify   # только проверить уже лежащий файл

Проверка настоящая: файл открывается движком onnxruntime, через тот же код программы
считается отпечаток тестовой картинки, длина вектора должна быть 512.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import ssl
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGET = ROOT / "assets" / "models" / "clip_image.onnx"
URLS = [
    "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model_quantized.onnx",
]
MIN_BYTES = 40 * 1024 * 1024        # сжатая модель ~90 МБ; всё, что меньше, — страница ошибки


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def download() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    part = TARGET.with_suffix(".part")
    last = None
    for url in URLS:
        for attempt in range(1, 4):
            try:
                print(f"Скачиваю {url} (попытка {attempt})", flush=True)
                req = urllib.request.Request(url, headers={"User-Agent": "SkladRekvizita-build"})
                with urllib.request.urlopen(req, timeout=120, context=_context()) as resp, \
                        open(part, "wb") as out:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                if part.stat().st_size < MIN_BYTES:
                    raise RuntimeError(f"файл слишком мал: {part.stat().st_size} байт")
                part.replace(TARGET)
                return
            except Exception as exc:                     # noqa: BLE001
                last = exc
                print("  не удалось:", exc, flush=True)
                time.sleep(5 * attempt)
    raise SystemExit(f"Модель скачать не удалось: {last}")


def verify() -> None:
    if not TARGET.exists() or TARGET.stat().st_size < MIN_BYTES:
        raise SystemExit("Файл модели отсутствует или слишком мал")
    sha = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    print(f"Файл: {TARGET.stat().st_size / 1048576:.1f} МБ, sha256 {sha}")

    tmp = Path(tempfile.mkdtemp(prefix="sklad_model_"))
    os.environ["SKLAD_DATA_DIR"] = str(tmp)
    os.environ["SKLAD_KEY_DIR"] = str(tempfile.mkdtemp(prefix="sklad_modelkey_"))
    from app import paths
    from app.db.database import Database, set_db
    paths.ensure_dirs()
    db = Database(paths.DATA_DIR / "data.db")
    db.initialize()
    set_db(db)

    from PIL import Image
    from app.services.image_search_providers import ClipOnnxProvider
    provider = ClipOnnxProvider()
    ok, reason = provider.available()
    print("available:", ok, reason)
    if not ok:
        raise SystemExit("Нейросеть не готова: " + reason)
    if not provider.is_bundled():
        raise SystemExit("Модель не распознана как встроенная")
    img = tmp / "test.jpg"
    Image.effect_noise((300, 200), 60).convert("RGB").save(img)
    vec = provider.embed(img)
    print("длина вектора:", len(vec))
    if len(vec) != 512:
        raise SystemExit(f"Ожидалась длина 512, получено {len(vec)}")
    print("Проверка модели пройдена")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not args.verify and not (TARGET.exists() and TARGET.stat().st_size >= MIN_BYTES):
        download()
    verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
