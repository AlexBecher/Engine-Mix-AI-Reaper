# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['config_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\icon.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\fader.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\fader_buttom.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\back.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\bass.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\drum.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\guitars.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\keys.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\lead.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\checkin.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\checkout.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\del.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\start.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\stop.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\save.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\learn.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\dry.png', 'img'), ('C:\\Users\\XB7U\\VM\\Mix_Robo\\img\\apply.png', 'img')],
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
    icon=['C:\\Users\\XB7U\\VM\\Mix_Robo\\build\\icon_256.ico'],
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
