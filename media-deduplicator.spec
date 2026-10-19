# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas = [(str(root / 'photo_deduplicator' / 'web'), 'photo_deduplicator/web'),
         (str(root / 'photo_deduplicator' / 'app.ico'), 'photo_deduplicator')]
binaries, hiddenimports = [], ['webview.platforms.winforms', 'webview.platforms.edgechromium', 'clr', 'pythonnet']
for module in ('cv2', 'pillow_heif'):
    d, b, h = collect_all(module)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis([str(root / 'run.py')], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3', 'webview.platforms.qt'],
             noarchive=False, optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='本地媒体去重',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root / 'photo_deduplicator' / 'app.ico'), version=str(root / 'version_info.txt'))
