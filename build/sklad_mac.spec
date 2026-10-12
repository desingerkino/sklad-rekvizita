# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller для macOS (Intel, macOS 10.15+). Windows-сборку (sklad.spec) не заменяет.

Сборка:  pyinstaller build/sklad_mac.spec --noconfirm
Итог:    dist/SkladRekvizita.app
"""
from pathlib import Path
import sys

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from app.version import DISPLAY_NAME, VERSION  # noqa: E402

icns = ROOT / "assets" / "app.icns"

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),
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
        "openpyxl.cell._writer",
        "pillow_heif",
        "onnxruntime",
        "onnxruntime.capi._pybind_state",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "matplotlib",
              "numpy.distutils", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SkladRekvizita",
    debug=False, strip=False, upx=False,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False,
               name="SkladRekvizita")

app = BUNDLE(
    coll,
    name="SkladRekvizita.app",
    icon=str(icns) if icns.exists() else None,
    bundle_identifier="ru.sklad.rekvizita",
    version=VERSION,
    info_plist={
        "CFBundleName": "Склад Реквизита",
        "CFBundleDisplayName": DISPLAY_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "10.15",
        "NSHighResolutionCapable": True,
        "NSCameraUsageDescription":
            "Камера нужна, чтобы фотографировать реквизит и искать его по фото.",
        "NSPrincipalClass": "NSApplication",
    },
)
