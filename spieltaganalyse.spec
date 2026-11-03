import os
from pathlib import Path

import pefile
from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPECPATH)
poppler_bin = Path(os.environ["POPPLER_BIN"])
pdftotext = poppler_bin / "pdftotext.exe"
if not pdftotext.is_file():
    raise FileNotFoundError(f"pdftotext.exe was not found in {poppler_bin}")

pending_binaries = [pdftotext]
bundled_binaries = set()
while pending_binaries:
    binary = pending_binaries.pop()
    if binary in bundled_binaries:
        continue
    bundled_binaries.add(binary)
    pe = pefile.PE(str(binary), fast_load=True)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
    )
    for imported_dll in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        dependency = poppler_bin / imported_dll.dll.decode("ascii")
        if dependency.is_file() and dependency not in bundled_binaries:
            pending_binaries.append(dependency)

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
