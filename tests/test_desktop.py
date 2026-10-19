from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from photo_deduplicator import engine
from photo_deduplicator.desktop import DesktopAPI
from photo_deduplicator.settings import DEFAULTS, SettingsStore
from test_engine import make_scene


class SettingsTests(unittest.TestCase):
    def test_yaml_round_trip_preserves_paths_and_independent_mode_options(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yml"
            store = SettingsStore(path)
            values = {**store.get(), "photo_base_folder": r"H:\照片 #1\目录", "reference_path": "C:/含空格的路径/图片.png",
                      "compare_second_folder": r"C:\对比目录", "video_base_folder": r"D:\视频", "video_reference_path": "D:/a.mp4",
                      "video_second_folder": "D:/b", "photo_mode": 2, "video_mode": 1, "media_tab": 1,
                      "folder_crop": True, "reference_recursive": False, "reference_sensitivity": "宽松",
                      "video_compare_sensitivity": "严格"}
            store.save(values)
            self.assertEqual(SettingsStore(path).get(), values)
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(document["photo"]["folder"], values["photo_base_folder"])
            self.assertEqual(document["photo"]["options"]["folder"], {"crop": True})
            self.assertEqual(document["photo"]["options"]["reference"], {"recursive": False, "sensitivity": "宽松"})
            self.assertFalse(path.with_name("settings.yml.tmp").exists())

    def test_invalid_config_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yml"
            text = 'photo:\n  folder: [wrong, type]\n'
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                SettingsStore(path)
            self.assertEqual(path.read_text(), text)

    def test_default_values_are_omitted_and_restored_when_options_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yml"
            store = SettingsStore(path)
            store.save({"folder_crop": True})
            self.assertIn("options:", path.read_text())
            store.save({"folder_crop": False})
            self.assertEqual(yaml.safe_load(path.read_text()), {"media":"photo", "photo":{"mode":"folder"}, "video":{"mode":"folder"}})
            self.assertEqual(SettingsStore(path).get(), DEFAULTS)

    def test_invalid_save_keeps_previous_file_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yml"
            store = SettingsStore(path)
            store.save({"photo_base_folder": "photos"})
            previous = path.read_bytes()
            with self.assertRaises(ValueError):
                store.save({"media_tab": True})
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(store.get()["media_tab"], 0)


class BridgeTests(unittest.TestCase):
    def test_all_six_modes_route_with_the_shared_folder_and_independent_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, other = root / "基准", root / "对比"
            base.mkdir(); other.mkdir()
            image, video = root / "基准.png", root / "基准.mp4"
            image.touch(); video.touch()
            api = DesktopAPI(root / "settings.yml")
            settings = api.get_settings()
            settings.update(photo_base_folder=str(base), video_base_folder=str(base),
                            compare_second_folder=str(other), video_second_folder=str(other),
                            reference_path=str(image), video_reference_path=str(video))
            names = (("scan_folder", "scan_reference", "scan_folders"), ("scan_video_folder", "scan_video_reference", "scan_video_folders"))
            for media in range(2):
                for mode in range(3):
                    with self.subTest(media=media, mode=mode):
                        settings.update(media_tab=media, photo_mode=mode, video_mode=mode)
                        with patch.object(engine, names[media][mode], return_value=engine.ScanResult([], 2, 1, [])) as scan:
                            self.assertEqual(api.start_scan(settings), {"ok": True})
                            api._worker.join(timeout=5)
                            self.assertFalse(api._worker.is_alive())
                            arguments = scan.call_args.kwargs
                            self.assertEqual(arguments["folder" if mode < 2 else "first_folder"], base)
                            self.assertEqual("detect_crops" in arguments, media == 0)
                            self.assertEqual(arguments["sensitivity"], "balanced")
                            if mode == 1:
                                self.assertEqual(arguments["reference"], image if media == 0 else video)
                            self.assertFalse(api.get_state()["scanning"])
                            self.assertEqual(api.get_state()["scanned"], 2)

    def test_real_results_preview_and_recycle_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "照片"
            base.mkdir()
            source = make_scene(base / "原图.png")
            source.resize((450, 300)).save(base / "压缩.jpg", quality=45)
            api = DesktopAPI(root / "settings.yml")
            settings = api.get_settings()
            settings.update(photo_base_folder=str(base), folder_recursive=False)
            self.assertEqual(api.start_scan(settings), {"ok": True})
            api._worker.join(timeout=15)
            result = api.get_state()
            self.assertEqual(len(result["groups"]), 1)
            files = result["groups"][0]["files"]
            self.assertEqual(len(files), 2)
            self.assertEqual(sum(f["recommended"] for f in files), 1)
            self.assertTrue(api.get_preview(files[0]["id"]).startswith("data:image/jpeg;base64,"))
            self.assertNotIn("groups", api.get_state(result["revision"]))
            self.assertIn("error", api.get_preview("arbitrary-path"))
            with patch("photo_deduplicator.desktop.send2trash") as recycle:
                self.assertIn("error", api.recycle(["unknown"]))
                recycle.assert_not_called()
                answer = api.recycle([files[0]["id"], files[0]["id"]])
                recycle.assert_called_once()
                self.assertIn("1 个文件", answer["message"])
            self.assertEqual(api.get_state()["groups"], [])

    def test_empty_path_is_rejected_instead_of_scanning_the_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopAPI(Path(directory) / "settings.yml")
            self.assertIn("error", api.start_scan(api.get_settings()))
            self.assertFalse(api.get_state()["scanning"])

    def test_cancel_does_not_publish_partial_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = DesktopAPI(root / "settings.yml")
            settings = {**api.get_settings(), "photo_base_folder": str(root)}
            def wait_for_cancel(**arguments):
                arguments["cancel"].wait(timeout=5)
                raise engine.ScanCancelled()
            with patch.object(engine, "scan_folder", side_effect=wait_for_cancel):
                api.start_scan(settings)
                api.cancel_scan()
                api._worker.join(timeout=5)
                result = api.get_state()
                self.assertFalse(result["scanning"])
                self.assertEqual(result["stage"], "扫描已停止")
                self.assertEqual(result["groups"], [])


if __name__ == "__main__":
    unittest.main()
