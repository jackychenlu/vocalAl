# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['vocalforge_ktv_studio.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'services.task_result',
        'services.task_runner',
        'services.ffmpeg_service',
        'services.download_service',
        'services.separation_service',
        'services.environment_service',
    ],
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
    name='VocalForgeKTVStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
