# PyInstaller one-folder build:  pyinstaller --noconfirm installer\SwarLekhak.spec
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = Path(SPECPATH).parent
MODEL = "vosk-model-small-en-us-0.15"
fixtures = [(str(p), "fixtures") for p in (ROOT / "tests" / "fixtures" / "audio").glob("demo-q*.wav")]
fixtures.append((str(ROOT / "tests" / "fixtures" / "audio" / "demo_fixtures.json"), "fixtures"))

datas = [
    (str(ROOT / "models" / MODEL), f"models/{MODEL}"),
    (str(ROOT / "models" / "manifest.json"), "models"),
    (str(ROOT / "docs" / "THIRD_PARTY_NOTICES.md"), "."),
    (str(ROOT / "docs" / "PRIVACY.md"), "."),
    (str(ROOT / "KNOWN_LIMITATIONS.md"), "."),
] + fixtures

excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtBluetooth", "PySide6.QtLocation", "PySide6.QtSql",
    "tkinter", "pytest", "PyInstaller",
]

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=collect_dynamic_libs("vosk"),  # libvosk.dll + MinGW runtime next to vosk/__init__
    datas=datas,
    hiddenimports=["win32crypt", "win32com.client", "pythoncom", "vosk"],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="SwarLekhak", console=False,
    version=str(ROOT / "installer" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SwarLekhak")
