from pathlib import Path
from types import SimpleNamespace

import pytest

from windows_build_support import collect_poppler_binaries


class FakePeFile:
    DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_IMPORT": 1}

    def __init__(self, imports: dict[str, list[str]]) -> None:
        self.imports = imports
        self.closed: list[str] = []

    def PE(self, path: str, fast_load: bool) -> object:
        name = Path(path).name
        owner = self

        class FakePE:
            DIRECTORY_ENTRY_IMPORT = [
                SimpleNamespace(dll=dll.encode("ascii"))
                for dll in owner.imports.get(name, [])
            ]

            def parse_data_directories(self, directories: list[int]) -> None:
                assert fast_load
                assert directories == [1]

            def close(self) -> None:
                owner.closed.append(name)

        return FakePE()


def test_collects_standalone_poppler_dependencies(tmp_path: Path) -> None:
    executable = tmp_path / "pdftotext.exe"
    library = tmp_path / "poppler.dll"
    executable.touch()
    library.touch()
    pefile = FakePeFile({
        executable.name: [library.name, "KERNEL32.dll"],
        library.name: [executable.name]
    })

    assert collect_poppler_binaries(executable, pefile) == {executable, library}
    assert set(pefile.closed) == {executable.name, library.name}


@pytest.mark.parametrize("dependency", ["MiKTeX230400-app.dll", "miktex-core.dll"])
def test_rejects_miktex_imports_without_requiring_dll_to_exist(
    tmp_path: Path,
    dependency: str
) -> None:
    executable = tmp_path / "pdftotext.exe"
    executable.touch()
    pefile = FakePeFile({executable.name: [dependency]})

    with pytest.raises(ValueError, match="MiKTeX.*standalone Poppler"):
        collect_poppler_binaries(executable, pefile)

    assert pefile.closed == [executable.name]


def test_rejects_miktex_in_transitive_dependencies(tmp_path: Path) -> None:
    executable = tmp_path / "pdftotext.exe"
    library = tmp_path / "poppler.dll"
    executable.touch()
    library.touch()
    pefile = FakePeFile({
        executable.name: [library.name],
        library.name: ["MiKTeX-core.dll"]
    })

    with pytest.raises(ValueError, match="MiKTeX"):
        collect_poppler_binaries(executable, pefile)

    assert set(pefile.closed) == {executable.name, library.name}
