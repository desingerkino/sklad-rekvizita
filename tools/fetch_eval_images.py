# -*- coding: utf-8 -*-
"""
Скачивает открытые снимки предметов для проверки поиска по фотографии.

Запуск:  python tools/fetch_eval_images.py ПАПКА

Снимки — по одному на класс из набора ImageNet (репозиторий
EliSchwartz/imagenet-sample-images на GitHub): телефоны, стулья, лампы, часы,
чемоданы, посуда и другие предметы, похожие на реквизит. В программу они не
попадают и нужны только для измерения точности (tools/eval_photo_search.py).
"""
from __future__ import annotations

import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://raw.githubusercontent.com/EliSchwartz/imagenet-sample-images/master/"


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "eval_images")
    target.mkdir(parents=True, exist_ok=True)
    names = [line.strip() for line in (Path(__file__).parent / "eval_images.txt").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    try:
        import certifi
        import ssl
        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:                                              # noqa: BLE001
        context = None

    def fetch(name: str) -> bool:
        path = target / name.replace("'", "_")
        if path.exists() and path.stat().st_size > 1000:
            return True
        for _attempt in range(3):
            try:
                with urllib.request.urlopen(BASE + urllib.parse.quote(name), timeout=40,
                                            context=context) as response:
                    path.write_bytes(response.read())
                return True
            except Exception:                                      # noqa: BLE001
                continue
        return False

    with ThreadPoolExecutor(8) as pool:
        done = sum(pool.map(fetch, names))
    print(f"Снимков для проверки: {done} из {len(names)} → {target}")
    return 0 if done >= len(names) * 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
