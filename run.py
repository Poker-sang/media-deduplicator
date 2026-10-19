from __future__ import annotations

import ctypes

from photo_deduplicator.desktop import run


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        run()
    except Exception:
        import traceback
        from pathlib import Path
        import os
        log = Path(os.environ.get("LOCALAPPDATA", ".")) / "MediaDeduplicator" / "startup-error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(0, f"启动失败。错误详情已保存到：\n{log}", "本地媒体去重", 0x10)
