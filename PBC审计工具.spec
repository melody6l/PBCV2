# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules
import os

archive_hiddenimports = collect_submodules('py7zr')
ocr_hiddenimports = collect_submodules('alibabacloud_ocr_api20210707')
archive_binaries = []
if os.path.isfile(os.path.join('tools', '7zip', '7zz.exe')):
    archive_binaries.append((os.path.join('tools', '7zip', '7zz.exe'), os.path.join('tools', '7zip')))
a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=archive_binaries,
    datas=[('templates', 'templates'), ('static', 'static'), ('资料表.xlsx', '.')],
    hiddenimports=['msal', 'rarfile'] + archive_hiddenimports + ocr_hiddenimports,
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
    [],
    exclude_binaries=True,
    name='PBC审计工具',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PBC审计工具',
)
