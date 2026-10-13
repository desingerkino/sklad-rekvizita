# -*- coding: utf-8 -*-
"""Проверка: все исполняемые файлы Mach-O внутри .app рассчитаны не новее заданной версии macOS.
Запуск (на macOS): python tools/check_macho_minos.py dist/SkladRekvizita.app 10.15
Это единственная проверка совместимости с Catalina, доступная без самого Mac с Catalina."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def ver(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split("."))


def macho_minos(path: Path) -> list[str]:
    """Версии macOS, указанные в заголовках всех архитектур файла."""
    try:
        out = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True,
                             timeout=60).stdout
    except Exception:
        return []
    found = []
    for block in re.split(r"Load command \d+", out):
        if "LC_BUILD_VERSION" in block:
            m = re.search(r"minos\s+([\d.]+)", block)
            if m:
                found.append(m.group(1))
        elif "LC_VERSION_MIN_MACOSX" in block:
            m = re.search(r"version\s+([\d.]+)", block)
            if m:
                found.append(m.group(1))
    return found


def is_macho(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return False
    return magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
                     b"\xbe\xba\xfe\xca")


def main() -> int:
    app = Path(sys.argv[1])
    limit = ver(sys.argv[2])
    total = bad = 0
    top = (0,)
    for p in sorted(app.rglob("*")):
        if p.is_symlink() or not p.is_file() or not is_macho(p):
            continue
        total += 1
        for v in macho_minos(p):
            top = max(top, ver(v))
            if ver(v) > limit:
                bad += 1
                print(f"  НОВЕЕ {sys.argv[2]}: {v}  {p.relative_to(app)}")
                break
    print(f"Mach-O файлов: {total}, максимальная требуемая macOS: {'.'.join(map(str, top))}, "
          f"превышений: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
