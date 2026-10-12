# -*- mode: python ; coding: utf-8 -*-
r"""
Конфигурация PyInstaller.

Сборка:  pyinstaller build\sklad.spec --noconfirm
Итог:    dist\SkladRekvizita\SkladRekvizita.exe  (папочная сборка — запускается быстрее
         и удобнее для установщика, чем onefile)
"""
from pathlib import Path

import sys

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from app.version import DISPLAY_NAME, VERSION  # noqa: E402

block_cipher = None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),                 # шрифты, иконка
        (str(ROOT / "app" / "db" / "schema.sql"), "app/db"),
    ],
    hiddenimports=[
        "cryptography.hazmat.primitives.ciphers.aead",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtPrintSupport",
        "PySide6.QtSvg",
        "reportlab.pdfbase._fontdata_enc_winansi",
        "reportlab.pdfbase._fontdata_widths_helvetica",
        "openpyxl.cell._writer",          # выгрузка в Excel: PyInstaller не видит его сам
        "pillow_heif",                    # чтение HEIC с айфона
        "onnxruntime",                    # нейросетевой поиск по фото
        "onnxruntime.capi._pybind_state",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "matplotlib",
              "numpy.distutils", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SkladRekvizita",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                                  # без чёрного окна консоли
    icon=str(ROOT / "assets" / "app.ico") if (ROOT / "assets" / "app.ico").exists() else None,
    version=str(ROOT / "build" / "version_info.txt")
    if (ROOT / "build" / "version_info.txt").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="SkladRekvizita",
)
