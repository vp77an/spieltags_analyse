import os
from pathlib import Path
import sys

import pefile
from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPECPATH)
sys.path.insert(0, str(project_root))
from windows_build_support import collect_poppler_binaries

poppler_bin = Path(os.environ["POPPLER_BIN"])
pdftotext = poppler_bin / "pdftotext.exe"
if not pdftotext.is_file():
    raise FileNotFoundError(f"pdftotext.exe was not found in {poppler_bin}")

bundled_binaries = collect_poppler_binaries(pdftotext, pefile)
binaries = [(str(binary), "poppler") for binary in sorted(bundled_binaries)]

datas = collect_data_files("matplotlib")
poppler_data = poppler_bin.parent / "share" / "poppler"
if poppler_data.is_dir():
    datas.extend(
        (
            str(file),
            str(Path("poppler") / "share" / "poppler" / file.relative_to(poppler_data).parent)
        )
        for file in poppler_data.rglob("*")
        if file.is_file()
    )

a = Analysis(
    [str(project_root / "desktop_app.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["fastapi", "uvicorn", "httpx", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Spieltaganalyse",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
