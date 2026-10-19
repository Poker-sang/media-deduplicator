from __future__ import annotations

import base64
import hashlib
import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from send2trash import send2trash

from . import engine

from .settings import SettingsStore
from .windowing import start_window_drag


def settings_path() -> Path:
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    return root / "settings.yml"


class DesktopAPI:
    def __init__(self, path: Path | None = None) -> None:
        self._store = SettingsStore(path or settings_path())
        self._window = None
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._cancel = threading.Event()
        self._files: dict[str, Any] = {}
        self._preview_cache: dict[str, str] = {}
        self._started = 0.0
        self._maximized = False
        self._worker: threading.Thread | None = None
        self._state = {
            "scanning": False, "cancelling": False, "stage": "准备就绪",
            "detail": "选择基准文件夹，然后开始扫描", "progress": 0,
            "groups": [], "errors": [], "scanned": 0, "compared": 0,
            "elapsed": 0, "revision": 0,
        }

    def get_settings(self) -> dict:
        return self._store.get()

    def save_settings(self, values: dict) -> dict:
        try:
            self._store.save(values)
            return {"ok": True}
        except (OSError, ValueError) as exc:
            return {"error": f"设置保存失败：{exc}"}

    def get_state(self, revision: int = -1) -> dict:
        with self._lock:
            data = dict(self._state)
            if data["scanning"]:
                data["elapsed"] = round(time.perf_counter() - self._started, 1)
            if revision == data["revision"]:
                data.pop("groups")
            return data

    def choose_path(self, kind: str, current: str = "") -> Any:
        import webview
        if self._window is None:
            return {"error": "窗口尚未就绪"}
        path = Path(current) if current else Path.home()
        directory = path if path.is_dir() else path.parent
        while not directory.is_dir() and directory != directory.parent:
            directory = directory.parent
        if kind == "folder":
            selected = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(directory))
        else:
            extensions = engine.SUPPORTED_EXTENSIONS if kind == "image" else engine.SUPPORTED_VIDEO_EXTENSIONS
            patterns = ";".join("*" + ext for ext in sorted(extensions))
            title = "图片" if kind == "image" else "视频"
            selected = self._window.create_file_dialog(webview.FileDialog.OPEN, directory=str(directory), file_types=(f"{title} ({patterns})", "所有文件 (*.*)"))
        return str(selected[0]) if selected else None

    @staticmethod
    def _folder(value: str) -> Path:
        if not value.strip():
            raise ValueError("请先选择有效的基准文件夹")
        path = Path(value.strip()).expanduser()
        if not path.is_dir():
            raise ValueError(f"文件夹不存在或无法访问：{path}")
        return path

    def _scan_arguments(self, s: dict) -> tuple[Any, dict]:
        photo = s["media_tab"] == 0
        mode = s["photo_mode"] if photo else s["video_mode"]
        base = self._folder(s["photo_base_folder" if photo else "video_base_folder"])
        prefix = (("folder", "reference", "compare") if photo else ("video_folder", "video_reference", "video_compare"))[mode]
        arguments: dict = {
            "recursive": s[f"{prefix}_recursive"],
            "sensitivity": {"严格": "strict", "平衡": "balanced", "宽松": "loose"}[s[f"{prefix}_sensitivity"]],
        }
        if photo:
            arguments["detect_crops"] = s[f"{prefix}_crop"]
        if mode == 0:
            arguments["folder"] = base
            fn = engine.scan_folder if photo else engine.scan_video_folder
        elif mode == 1:
            raw = s["reference_path" if photo else "video_reference_path"].strip()
            reference = Path(raw).expanduser()
            if not raw or not reference.is_file():
                raise ValueError("请选择有效的基准图片" if photo else "请选择有效的基准视频")
            extensions = engine.SUPPORTED_EXTENSIONS if photo else engine.SUPPORTED_VIDEO_EXTENSIONS
            if reference.suffix.lower() not in extensions:
                raise ValueError("基准文件的格式不受支持")
            arguments.update(reference=reference, folder=base)
            fn = engine.scan_reference if photo else engine.scan_video_reference
        else:
            second = self._folder(s["compare_second_folder" if photo else "video_second_folder"])
            if base.resolve() == second.resolve():
                raise ValueError("两个文件夹相同，请使用“文件夹内去重”模式")
            arguments.update(first_folder=base, second_folder=second)
            fn = engine.scan_folders if photo else engine.scan_video_folders
        return fn, arguments

    def start_scan(self, settings: dict) -> dict:
        if not self._operation.acquire(blocking=False):
            return {"error": "正在处理文件，请稍后重试"}
        try:
            with self._lock:
                if self._state["scanning"]:
                    return {"error": "扫描正在进行"}
                s = self._store.save(settings)
                fn, arguments = self._scan_arguments(s)
                self._reset()
                self._cancel.clear()
                self._started = time.perf_counter()
                self._state.update(scanning=True, stage="正在扫描", detail="准备读取文件…", progress=0, cancelling=False)
            self._worker = threading.Thread(target=self._scan_worker, args=(fn, arguments), daemon=True, name="media-scan")
            self._worker.start()
            return {"ok": True}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            self._operation.release()

    def _scan_worker(self, fn, arguments: dict) -> None:
        def progress(stage: str, current: int, total: int, detail: str) -> None:
            with self._lock:
                self._state.update(stage=stage, detail=f"{current}/{total} · {detail}", progress=100 * current / total if total else 0)
        try:
            result = fn(**arguments, progress=progress, cancel=self._cancel)
            if self._cancel.is_set():
                raise engine.ScanCancelled()
            with self._lock:
                groups = []
                for index, group in enumerate(result.groups):
                    files = []
                    for feature in group.images:
                        file_id = hashlib.sha256(str(feature.path).encode("utf-8")).hexdigest()[:24]
                        self._files[file_id] = feature
                        files.append({
                            "id": file_id, "name": feature.path.name, "path": str(feature.path),
                            "size": feature.file_size, "dimensions": feature.dimensions,
                            "format": feature.display_format, "quality": feature.quality_score,
                            "recommended": feature.path == group.recommended.path,
                            "stats": f"{feature.duration_label} / {feature.bitrate_label}" if hasattr(feature, "duration_label") else "",
                        })
                    groups.append({"id": f"{self._state['revision']}-{index}", "index": index, "files": files, "confidence": group.confidence, "kind": group.kind, "reasons": group.recommendation_reasons})
                self._state.update(
                    scanning=False, cancelling=False, stage="扫描完成", detail=f"发现 {len(groups)} 个重复分组" if groups else "没有发现符合条件的重复文件",
                    groups=groups, errors=result.errors, scanned=result.scanned_count, compared=result.compared_count,
                    elapsed=round(time.perf_counter() - self._started, 1), progress=100, revision=self._state["revision"] + 1,
                )
        except engine.ScanCancelled:
            with self._lock:
                self._state.update(scanning=False, cancelling=False, stage="扫描已停止", detail="可调整参数后重新扫描", progress=0, elapsed=round(time.perf_counter() - self._started, 1))
        except Exception as exc:
            with self._lock:
                self._state.update(scanning=False, cancelling=False, stage="扫描失败", detail=str(exc), errors=[str(exc)], progress=0, elapsed=round(time.perf_counter() - self._started, 1))

    def cancel_scan(self) -> dict:
        with self._lock:
            if self._state["scanning"]:
                self._cancel.set()
                self._state.update(cancelling=True, stage="正在停止", detail="等待当前文件处理完成…")
        return {"ok": True}

    def _reset(self) -> None:
        self._files.clear()
        self._preview_cache.clear()
        self._state.update(groups=[], errors=[], scanned=0, compared=0, elapsed=0, revision=self._state["revision"] + 1)

    def clear_results(self) -> dict:
        with self._lock:
            if not self._state["scanning"]:
                self._reset()
                self._state.update(stage="准备就绪", detail="选择基准文件夹，然后开始扫描", progress=0)
        return {"ok": True}

    def dismiss_errors(self) -> dict:
        with self._lock:
            self._state["errors"] = []
        return {"ok": True}

    def get_preview(self, file_id: str) -> Any:
        with self._lock:
            if file_id in self._preview_cache:
                return self._preview_cache[file_id]
            feature = self._files.get(file_id)
        if feature is None:
            return {"error": "文件不在当前结果中"}
        try:
            if hasattr(feature, "preview_rgb"):
                image = Image.fromarray(feature.preview_rgb).convert("RGB")
            else:
                with Image.open(feature.path) as source:
                    # Resize before copying to bound memory for large camera photos.
                    image = ImageOps.exif_transpose(source)
                    image.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
                    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                        rgba = image.convert("RGBA")
                        image = Image.new("RGB", rgba.size, "#15171c")
                        image.paste(rgba, mask=rgba.getchannel("A"))
                    else:
                        image = image.convert("RGB")
            image.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=88)
            data = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
            with self._lock:
                if file_id in self._files:
                    if len(self._preview_cache) >= 24:
                        self._preview_cache.pop(next(iter(self._preview_cache)))
                    self._preview_cache[file_id] = data
            return data
        except Exception as exc:
            return {"error": f"无法预览：{exc}"}

    def open_file(self, file_id: str, folder: bool = False) -> dict:
        with self._lock:
            feature = self._files.get(file_id)
        if feature is None or not feature.path.exists():
            return {"error": "文件不存在或不在当前结果中"}
        try:
            if folder:
                subprocess.Popen(["explorer.exe", "/select,", str(feature.path)])
            else:
                os.startfile(str(feature.path))
            return {"ok": True}
        except OSError as exc:
            return {"error": str(exc)}

    def recycle(self, file_ids: list[str]) -> dict:
        if not self._operation.acquire(blocking=False):
            return {"error": "正在处理文件，请稍后重试"}
        try:
            with self._lock:
                if self._state["scanning"]:
                    return {"error": "扫描期间无法清理文件"}
                if not isinstance(file_ids, list) or not file_ids or any(not isinstance(i, str) or i not in self._files for i in file_ids):
                    return {"error": "勾选的文件不在当前结果中，请重新扫描"}
                paths = list(dict.fromkeys(self._files[i].path for i in file_ids))
            errors, moved = [], 0
            for path in paths:
                try:
                    if not path.exists():
                        raise FileNotFoundError("文件已不存在")
                    send2trash(str(path))
                    moved += 1
                except Exception as exc:
                    errors.append(f"{path.name}：{exc}")
            with self._lock:
                self._reset()
                self._state.update(stage="清理完成", detail=f"已将 {moved} 个文件移入回收站，请重新扫描确认", errors=errors, progress=0)
            return {"message": f"已将 {moved} 个文件移入系统回收站" + (f"，{len(errors)} 个文件未移动" if errors else "")}
        finally:
            self._operation.release()

    def window_action(self, action: str) -> dict:
        if self._window is None:
            return {"error": "窗口尚未就绪"}
        if action == "minimize":
            self._window.minimize()
        elif action == "maximize":
            self._window.restore() if self._maximized else self._window.maximize()
        elif action == "close":
            self._cancel.set()
            self._window.destroy()
        return {"ok": True}

    def drag_window(self) -> dict:
        if self._window is not None:
            start_window_drag(self._window)
        return {"ok": True}

    def resize_window(self, width: int, height: int) -> dict:
        if self._window is not None and not self._maximized:
            self._window.resize(max(1000, min(7680, int(width))), max(680, min(4320, int(height))))
        return {"ok": True}


def run() -> None:
    import webview
    api = DesktopAPI()
    html = Path(__file__).resolve().parent / "web" / "index.html"
    if not html.is_file():
        raise RuntimeError("缺少界面资源，请先在 frontend 中运行 npm run build")
    window = webview.create_window(
        "本地媒体去重", str(html), js_api=api, width=1280, height=840,
        min_size=(1000, 680), frameless=True, easy_drag=False, shadow=True,
        background_color="#101115", text_select=False,
    )
    api._window = window
    def maximized():
        api._maximized = True
    def restored():
        api._maximized = False
    def closing():
        api._cancel.set()
    window.events.maximized += maximized
    window.events.restored += restored
    window.events.closing += closing
    webview.start(gui="edgechromium", debug=False, private_mode=True, icon=str(Path(__file__).parent / "app.ico"))
