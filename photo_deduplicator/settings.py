from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml


DEFAULTS: dict[str, Any] = {
    "photo_base_folder": "", "reference_path": "", "compare_second_folder": "",
    "folder_recursive": True, "folder_crop": False, "folder_sensitivity": "平衡",
    "reference_recursive": True, "reference_crop": False, "reference_sensitivity": "平衡",
    "compare_recursive": True, "compare_crop": False, "compare_sensitivity": "平衡",
    "video_base_folder": "", "video_reference_path": "", "video_second_folder": "",
    "video_folder_recursive": True, "video_folder_sensitivity": "平衡",
    "video_reference_recursive": True, "video_reference_sensitivity": "平衡",
    "video_compare_recursive": True, "video_compare_sensitivity": "平衡",
    "media_tab": 0, "photo_mode": 0, "video_mode": 0,
}
MODES = ("folder", "reference", "compare")
SECTIONS = {
    "photo": ("photo_base_folder", "reference_path", "compare_second_folder", "photo_mode", ("folder", "reference", "compare")),
    "video": ("video_base_folder", "video_reference_path", "video_second_folder", "video_mode", ("video_folder", "video_reference", "video_compare")),
}


def _validate(values: dict) -> None:
    for key, default in DEFAULTS.items():
        value = values[key]
        if type(value) is not type(default):
            raise ValueError(f"{key} 的类型无效")
        if key.endswith("_sensitivity") and value not in ("严格", "平衡", "宽松"):
            raise ValueError(f"{key} 必须为严格、平衡或宽松")
    for key, bound in (("media_tab", 2), ("photo_mode", 3), ("video_mode", 3)):
        if not 0 <= values[key] < bound:
            raise ValueError(f"{key} 的模式无效")


def _mapping(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须为键值配置")
    return value


def _decode(document: Any) -> dict:
    data = _mapping(document, "配置")
    media = data.get("media", "photo")
    if media not in ("photo", "video"):
        raise ValueError("media 必须为 photo 或 video")
    values = {**DEFAULTS, "media_tab": ("photo", "video").index(media)}
    for section, (folder, reference, target, mode_key, prefixes) in SECTIONS.items():
        content = _mapping(data.get(section, {}), section)
        for field, key in (("folder", folder), ("reference", reference), ("compare_folder", target)):
            if field in content:
                values[key] = content[field]
        mode = content.get("mode", "folder")
        if mode not in MODES:
            raise ValueError(f"{section}.mode 必须为 folder、reference 或 compare")
        values[mode_key] = MODES.index(mode)
        options = _mapping(content.get("options", {}), f"{section}.options")
        for mode_name, prefix in zip(MODES, prefixes):
            selected = _mapping(options.get(mode_name, {}), f"{section}.options.{mode_name}")
            for option in ("recursive", "crop", "sensitivity"):
                key = f"{prefix}_{option}"
                if key in DEFAULTS and option in selected:
                    values[key] = selected[option]
    _validate(values)
    return values


def _encode(values: dict) -> dict:
    data: dict = {"media": ("photo", "video")[values["media_tab"]]}
    for section, (folder, reference, target, mode_key, prefixes) in SECTIONS.items():
        content = {}
        for field, key in (("folder", folder), ("reference", reference), ("compare_folder", target)):
            if values[key]:
                content[field] = values[key]
        content["mode"] = MODES[values[mode_key]]
        options = {}
        for mode_name, prefix in zip(MODES, prefixes):
            selected = {}
            for option in ("recursive", "crop", "sensitivity"):
                key = f"{prefix}_{option}"
                if key in DEFAULTS and values[key] != DEFAULTS[key]:
                    selected[option] = values[key]
            if selected:
                options[mode_name] = selected
        if options:
            content["options"] = options
        data[section] = content
    return data


class SettingsStore:
    """Read and atomically write the compact YAML configuration."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        try:
            text = path.read_text(encoding="utf-8-sig")
            self.values = _decode(yaml.safe_load(text) if text.strip() else {})
        except FileNotFoundError:
            self.values = dict(DEFAULTS)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise RuntimeError(f"无法读取设置，请检查 {path}：{exc}") from exc

    def get(self) -> dict:
        with self.lock:
            return dict(self.values)

    def save(self, incoming: dict) -> dict:
        if not isinstance(incoming, dict):
            raise ValueError("设置格式无效")
        with self.lock:
            updated = {**self.values, **{key: value for key, value in incoming.items() if key in DEFAULTS}}
            _validate(updated)
            text = yaml.safe_dump(_encode(updated), allow_unicode=True, sort_keys=False, default_flow_style=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            try:
                with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.values = updated
            return dict(updated)
