from pathlib import Path
from typing import Any


def collect_poppler_binaries(pdftotext: Path, pefile: Any) -> set[Path]:
    pending = [pdftotext]
    binaries: set[Path] = set()
    while pending:
        binary = pending.pop()
        if binary in binaries:
            continue
        if "miktex" in binary.name.lower():
            raise ValueError(
                "MiKTeX's pdftotext cannot be distributed standalone. "
                "Set POPPLER_BIN to a standalone Poppler for Windows bin folder."
            )
        binaries.add(binary)
        pe = pefile.PE(str(binary), fast_load=True)
        try:
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]
                ]
            )
            for imported_dll in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
                name = imported_dll.dll.decode("ascii")
                if "miktex" in name.lower():
                    raise ValueError(
                        f"{binary.name} depends on {name}, a MiKTeX library. "
                        "Set POPPLER_BIN to a standalone Poppler for Windows "
                        "bin folder instead."
                    )
                dependency = pdftotext.parent / name
                if dependency.is_file() and dependency not in binaries:
                    pending.append(dependency)
        finally:
            pe.close()
    return binaries
