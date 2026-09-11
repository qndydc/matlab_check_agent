# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(SPECPATH).parents[1]
APP_VERSION = os.environ.get("MATLAB_ATLAS_BUILD_VERSION", "0.1.0")
SEMANTIC_DIST = ROOT / "apps" / "semantic" / "frontend" / "dist"
MIGRATION_DIST = ROOT / "apps" / "migration" / "frontend" / "dist"
for directory in (SEMANTIC_DIST, MIGRATION_DIST):
    if not (directory / "index.html").is_file():
        raise SystemExit(f"前端尚未构建：{directory}")

maxx_datas, maxx_binaries, maxx_hidden = collect_all("maxx")
hidden_imports = sorted(set(
    maxx_hidden
    + collect_submodules("matlab_refactor_agent")
    + collect_submodules("uvicorn")
    + collect_submodules("langgraph")
))

a = Analysis(
    [str(ROOT / "src" / "matlab_refactor_agent" / "apps" / "desktop.py")],
    pathex=[str(ROOT / "src")],
    binaries=maxx_binaries,
    datas=[
        (str(ROOT / ".env.example"), "."),
        (str(SEMANTIC_DIST), "apps/semantic/frontend/dist"),
        (str(MIGRATION_DIST), "apps/migration/frontend/dist"),
        *maxx_datas,
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"MATLAB-Atlas-v{APP_VERSION}-win-x64",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(ROOT / "packaging" / "windows" / "version_info.txt"),
)
