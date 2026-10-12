# -*- coding: utf-8 -*-
"""
Проверка: запускается ли движок нейросети на этом компьютере.

numpy и onnxruntime — это скомпилированные библиотеки. На старом процессоре
без нужных наборов инструкций они не бросают понятную ошибку, а роняют весь
процесс целиком: Windows просто закрывает программу, без сообщения и без
записи в журнал. Если проверять их прямо в программе, оператор потеряет
несохранённую работу и не поймёт почему.

Поэтому проверка идёт в отдельном процессе: он запускает движок на крошечной
модели и завершается. Упал он — упал только он, программа это видит и честно
говорит, что нейросеть на этом компьютере недоступна, а поиск по фото
продолжает работать встроенным алгоритмом.

Результат запоминается на время работы программы: проверка занимает пару
секунд, повторять её на каждый поиск незачем.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROBE_FLAG = "--probe-onnx"
PROBE_TIMEOUT = 60
OK_MARK = "ONNX-PROBE-OK"

_cached: Optional[tuple[bool, str]] = None


def run_probe_inline() -> int:
    """
    Сама проверка. Выполняется в ОТДЕЛЬНОМ процессе: вызывается из main.py при
    запуске с ключом --probe-onnx или через python -c в режиме разработки.
    """
    try:
        import numpy as np
        import onnxruntime

        from app.services.onnx_tiny import model_bytes

        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        session = onnxruntime.InferenceSession(
            model_bytes(), sess_options=options, providers=["CPUExecutionProvider"])
        tensor = np.zeros((1, 3, 224, 224), dtype=np.float32)
        output = session.run(None, {session.get_inputs()[0].name: tensor})[0]
        if output.shape[-1] != 48:
            print(f"неожиданный ответ модели: {output.shape}")
            return 2
    except Exception as exc:                                       # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}")
        return 1
    print(OK_MARK)
    return 0


def _command() -> list[str]:
    if getattr(sys, "frozen", False):
        # собранная программа: запускаем саму себя с ключом проверки
        return [sys.executable, PROBE_FLAG]
    root = Path(__file__).resolve().parent.parent.parent
    code = (f"import sys; sys.path.insert(0, {str(root)!r}); "
            "from app.services.onnx_probe import run_probe_inline; "
            "raise SystemExit(run_probe_inline())")
    return [sys.executable, "-c", code]


def runtime_ok(force: bool = False) -> tuple[bool, str]:
    """
    Можно ли пользоваться нейросетью на этом компьютере.
    Возвращает (да/нет, причина для человека).
    """
    global _cached
    if _cached is not None and not force:
        return _cached

    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)    # без мелькания консоли
    try:
        result = subprocess.run(
            _command(), capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            creationflags=flags, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        _cached = (False, "Движок нейросети не ответил за минуту — на этом "
                          "компьютере он слишком медленный.")
        log.warning("Проверка onnxruntime: превышено время ожидания")
        return _cached
    except OSError as exc:
        _cached = (False, f"Не удалось запустить проверку движка нейросети: {exc}")
        log.warning("Проверка onnxruntime не запустилась: %s", exc)
        return _cached

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0 and OK_MARK in output:
        _cached = (True, "")
        log.info("Проверка onnxruntime пройдена")
        return _cached

    last_line = next((line for line in reversed(output.strip().splitlines())
                      if line.strip()), "")
    if result.returncode in (1, 2) and last_line:
        reason = (f"Библиотеки нейросети не загружаются на этом компьютере "
                  f"({last_line[:160]}).")
    else:
        # процесс упал без сообщения — типично для процессора без нужных
        # наборов инструкций: библиотека просто не может на нём исполняться
        reason = ("Движок нейросети аварийно завершается на этом компьютере — "
                  "обычно так бывает на старых процессорах, которые не умеют "
                  f"нужные ему инструкции (код {result.returncode}).")
    _cached = (False, reason)
    log.warning("Проверка onnxruntime не пройдена: код %s, %s",
                result.returncode, last_line[:200])
    return _cached


def reset_cache() -> None:
    global _cached
    _cached = None
