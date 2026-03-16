# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['config_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\agbec\\Documents\\Mix_Robo\\icon.png', '.'), ('C:\\Users\\agbec\\Documents\\Mix_Robo\\fader.png', '.'), ('C:\\Users\\agbec\\Documents\\Mix_Robo\\fader_buttom.png', '.')],
    hiddenimports=['soundfile', 'sounddevice', 'pyloudnorm'],
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
    name='AlexStudioMix',
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
    icon=['C:\\Users\\agbec\\Documents\\Mix_Robo\\build\\icon_256.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AlexStudioMix',
)
