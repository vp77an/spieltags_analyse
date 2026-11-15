$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot ".build-venv\Scripts\python.exe"

if (-not $env:POPPLER_BIN) {
    $pdftotextCommand = Get-Command pdftotext.exe -ErrorAction SilentlyContinue
    if (-not $pdftotextCommand) {
        $pdftotextCommand = Get-Command pdftotext -ErrorAction SilentlyContinue
    }
    if (-not $pdftotextCommand) {
        throw @"
Could not find pdftotext.exe. Install standalone Poppler for Windows and set
POPPLER_BIN to its bin directory, or add that directory to PATH. Do not use
the MiKTeX version, which requires a separate MiKTeX installation.
"@
    }
    $env:POPPLER_BIN = Split-Path $pdftotextCommand.Source
}

if (-not (Test-Path -LiteralPath (Join-Path $env:POPPLER_BIN "pdftotext.exe"))) {
    throw "pdftotext.exe was not found in POPPLER_BIN: $env:POPPLER_BIN"
}

if (-not (Test-Path $python)) {
    py -3.12 -m venv (Join-Path $projectRoot ".build-venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the Python build environment."
    }
}

& $python -c "import struct, sys, tkinter; assert sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8; print('Using 64-bit Python', sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) {
    throw "The build requires 64-bit Python 3.12 with Tcl/Tk. Install Python 3.12 with Tcl/Tk and IDLE, then remove .build-venv and rerun this script."
}

Write-Host "Using Poppler from: $env:POPPLER_BIN"
& $python -m pip install -r (Join-Path $projectRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Could not install application/build dependencies."
}
& $python -c "import pefile, sys; from pathlib import Path; sys.path.insert(0, sys.argv[2]); from windows_build_support import collect_poppler_binaries; collect_poppler_binaries(Path(sys.argv[1]), pefile)" (Join-Path $env:POPPLER_BIN "pdftotext.exe") $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Poppler validation failed. Select a standalone Poppler bin directory using POPPLER_BIN."
}
& $python -m PyInstaller --clean --noconfirm (
    Join-Path $projectRoot "spieltaganalyse.spec"
)
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the Windows executable."
}

$executable = Join-Path $projectRoot "dist\Spieltaganalyse.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    throw "PyInstaller completed but the expected executable is missing: $executable"
}
Write-Host ""
Write-Host "Build complete."
Write-Host "Executable: $executable"
