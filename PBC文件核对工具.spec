# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules
import os

archive_hiddenimports = collect_submodules('py7zr')
archive_binaries = []
if os.path.isfile(os.path.join('tools', '7zip', '7zz.exe')):
    archive_binaries.append((os.path.join('tools', '7zip', '7zz.exe'), os.path.join('tools', '7zip')))
a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=archive_binaries,
    datas=[('templates', 'templates'), ('static', 'static'), ('资料表.xlsx', '.')],
    hiddenimports=['msal', 'rarfile'] + archive_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PBC文件核对工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
