# -*- coding: utf-8 -*-
"""
Статическая проверка модулей интерфейса без запуска Qt: ищет обращения к именам,
которые не импортированы и нигде не определены (частая причина падений в UI).
Запуск: python -m tests.check_imports
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILTINS = set(dir(builtins))


def collect_defined(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            args = getattr(node, "args", None)
            if args is not None:
                names.update(a.arg for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs))
                if args.vararg:
                    names.add(args.vararg.arg)
                if args.kwarg:
                    names.add(args.kwarg.arg)
        elif isinstance(node, ast.Lambda):
            names.update(a.arg for a in node.args.args)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Global):
            names.update(node.names)
    return names


# Проверяется только код самой программы. Всё остальное, что может лежать
# внутри папки проекта, пропускается: виртуальное окружение с чужими
# библиотеками (.venv), временные каталоги сборки, кэш Python. Иначе проверка
# уходит разбирать reportlab и PySide6 и выдаёт тысячи бессмысленных замечаний
# о «ненайденных именах» в чужом коде.
OWN_CODE = ("app", "tests", "tools")
SKIP_PARTS = {".venv", "venv", "env", "__pycache__", "build", "dist", ".git",
              "site-packages", ".mypy_cache", ".pytest_cache"}


def own_files() -> list[Path]:
    files = [ROOT / "main.py"] if (ROOT / "main.py").exists() else []
    for folder in OWN_CODE:
        base = ROOT / folder
        if base.exists():
            files += [f for f in base.rglob("*.py")
                      if not SKIP_PARTS & set(f.relative_to(ROOT).parts)]
    return sorted(set(files))


def main() -> int:
    problems: list[str] = []
    files = own_files()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined = collect_defined(tree) | BUILTINS | {"__file__", "__name__", "self", "cls"}
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        missing = sorted(used - defined)
        if missing:
            problems.append(f"{path.relative_to(ROOT)}: не найдены имена {missing}")
    print(f"Проверено файлов: {len(files)}")
    for problem in problems:
        print("  ВНИМАНИЕ:", problem)
    if not problems:
        print("Необъявленных имён не найдено.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
