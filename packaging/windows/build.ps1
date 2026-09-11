param(
    [string]$Version = "0.1.0",
    [string]$Python = "python",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\windows"
$BuildVenv = Join-Path $BuildRoot ".venv"
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"

Push-Location $RepoRoot
try {
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
        throw "pnpm was not found. Install Node.js 22 and pnpm first."
    }

    pnpm --dir apps/semantic/frontend install --frozen-lockfile
    pnpm --dir apps/semantic/frontend build
    pnpm --dir apps/migration/frontend install --frozen-lockfile
    pnpm --dir apps/migration/frontend build

    if (-not (Test-Path $BuildPython)) {
        & $Python -m venv $BuildVenv
    }
    & $BuildPython -m pip install --disable-pip-version-check --upgrade pip
    & $BuildPython -m pip install --disable-pip-version-check -e ".[dev]" "pyinstaller==6.22.2"

    if (-not $SkipTests) {
        & $BuildPython -m pytest -q --basetemp="$BuildRoot\pytest"
    }

    $env:MATLAB_ATLAS_BUILD_VERSION = $Version
    & $BuildPython -m PyInstaller `
        --clean `
        --noconfirm `
        --distpath "$BuildRoot\dist" `
        --workpath "$BuildRoot\work-onefile" `
        "packaging\windows\matlab-atlas-onefile.spec"

    $SingleExe = Join-Path $BuildRoot "dist\MATLAB-Atlas-v$Version-win-x64.exe"
    if (-not (Test-Path $SingleExe)) {
        throw "PyInstaller did not create the expected single-file executable: $SingleExe"
    }
    $SingleHash = (Get-FileHash -Algorithm SHA256 $SingleExe).Hash.ToLowerInvariant()
    "$SingleHash  $([IO.Path]::GetFileName($SingleExe))" | Set-Content `
        -Encoding ascii "$SingleExe.sha256"
    Write-Host "Single-file executable: $SingleExe"
    Write-Host "SHA256: $SingleHash"
    Copy-Item ".env.example" (Join-Path $BuildRoot "dist\.env.example") -Force
    $ExternalEnv = Join-Path $BuildRoot "dist\.env"
    Copy-Item ".env.example" $ExternalEnv -Force
    New-Item -ItemType Directory -Force `
        (Join-Path $BuildRoot "dist\data\jobs"), `
        (Join-Path $BuildRoot "dist\data\graphs"), `
        (Join-Path $BuildRoot "dist\logs") | Out-Null
    Write-Host "External config: $ExternalEnv"
}
finally {
    Remove-Item Env:MATLAB_ATLAS_BUILD_VERSION -ErrorAction SilentlyContinue
    Pop-Location
}
