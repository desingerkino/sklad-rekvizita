# -*- coding: utf-8 -*-
"""
Запускает набор тестов с ограничением по времени:
    python tools/run_suite.py МОДУЛЬ ФАЙЛ_ЖУРНАЛА СЕКУНД [аргументы…]

Нужен на CI: на macOS нет команды timeout, а один зависший набор не должен
останавливать все остальные проверки. Код возврата 124 — время вышло.
"""
from __future__ import annotations

import subprocess
import sys


def main() -> int:
    module, log_path, seconds = sys.argv[1], sys.argv[2], int(sys.argv[3])
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        try:
            return subprocess.run([sys.executable, "-u", "-m", module, *sys.argv[4:]],
                                  stdout=log, stderr=subprocess.STDOUT,
                                  timeout=seconds).returncode
        except subprocess.TimeoutExpired:
            log.write(f"\nПРЕРВАНО: набор не уложился в {seconds} с\n")
            return 124


if __name__ == "__main__":
    sys.exit(main())
