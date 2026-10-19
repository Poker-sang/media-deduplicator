from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from photo_deduplicator.engine import (
    SENSITIVITIES,
    compare_images,
    extract_features,
    scan_folder,
    scan_folders,
    scan_reference,
    scan_video_folder,
    scan_video_folders,
    scan_video_reference,
)


def make_scene(path: Path, width: int = 900, height: int = 600) -> Image.Image:
    rng = np.random.default_rng(20260716)
    pixels = rng.integers(25, 220, size=(height, width, 3), dtype=np.uint8)
    image = Image.fromarray(pixels, "RGB")
    draw = ImageDraw.Draw(image)
    for index in range(12):
        left = 30 + index * 63
        top = 35 + (index % 4) * 115
        draw.rectangle((left, top, left + 92, top + 68), outline=(255, 255, 255), width=5)
        draw.line((left, top + 68, left + 92, top), fill=(5, 20, 30), width=4)
    draw.ellipse((310, 170, 570, 430), outline=(240, 30, 45), width=12)
    image.save(path)
    return image


def make_video(path: Path, width: int = 320, height: int = 240) -> None:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 15, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV MP4 writer unavailable")
    for index in range(30):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (25 + index * 2, 85, 150)
        scale_x = width / 320
        scale_y = height / 240
        cv2.rectangle(
            frame,
            (int(30 * scale_x), int(45 * scale_y)),
            (int(180 * scale_x), int(175 * scale_y)),
            (230, 230, 230),
            -1,
        )
        cv2.putText(
            frame,
            f"{index:02d}",
            (int(120 * scale_x), int(220 * scale_y)),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(0.5, scale_x),
            (20, 20, 20),
            2,
        )
        writer.write(frame)
    writer.release()


class EngineTests(unittest.TestCase):
    def test_folder_and_reference_modes_recommend_higher_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            original = make_scene(folder / "original.png")
            original.resize((450, 300), Image.Resampling.LANCZOS).save(
                folder / "compressed.jpg", quality=45
            )
            Image.new("RGB", (900, 600), (12, 110, 180)).save(folder / "different.png")

            folder_result = scan_folder(folder, recursive=False)
            self.assertEqual(len(folder_result.groups), 1)
            self.assertEqual(folder_result.groups[0].recommended.path.name, "original.png")
            self.assertEqual(len(folder_result.groups[0].images), 2)

            reference_result = scan_reference(folder / "compressed.jpg", folder, recursive=False)
            self.assertEqual(len(reference_result.groups), 1)
            self.assertEqual(reference_result.groups[0].recommended.path.name, "original.png")

    def test_crop_matching_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            original_path = folder / "original.png"
            crop_path = folder / "crop.png"
            original = make_scene(original_path)
            original.crop((145, 90, 735, 510)).save(crop_path)
            original_feature = extract_features(original_path)
            crop_feature = extract_features(crop_path)

            without_crop = compare_images(
                original_feature, crop_feature, SENSITIVITIES["balanced"], detect_crops=False
            )
            with_crop = compare_images(
                original_feature, crop_feature, SENSITIVITIES["balanced"], detect_crops=True
            )
            self.assertIsNone(without_crop)
            self.assertIsNotNone(with_crop)
            self.assertEqual(with_crop.kind, "疑似局部/构图变化")

    def test_folder_to_folder_only_compares_across_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_folder = root / "first"
            second_folder = root / "second"
            first_folder.mkdir()
            second_folder.mkdir()

            original = make_scene(first_folder / "original.png")
            original.resize((450, 300), Image.Resampling.LANCZOS).save(
                first_folder / "same_folder_duplicate.jpg", quality=50
            )
            Image.new("RGB", (900, 600), (18, 125, 195)).save(
                second_folder / "different.png"
            )

            no_cross_match = scan_folders(first_folder, second_folder, recursive=False)
            self.assertEqual(no_cross_match.groups, [])

            original.resize((600, 400), Image.Resampling.LANCZOS).save(
                second_folder / "cross_folder_duplicate.jpg", quality=55
            )
            cross_match = scan_folders(first_folder, second_folder, recursive=False)
            self.assertEqual(len(cross_match.groups), 1)
            self.assertEqual(cross_match.groups[0].recommended.path.name, "original.png")
            self.assertIn(
                "cross_folder_duplicate.jpg",
                {image.path.name for image in cross_match.groups[0].images},
            )

    def test_video_modes_compare_three_frames_and_recommend_higher_bitrate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            high = first / "high.mp4"
            low = second / "low.mp4"
            make_video(high, 640, 480)
            make_video(low, 320, 240)

            folder_result = scan_video_folder(first, recursive=False)
            self.assertEqual(folder_result.groups, [])
            cross_result = scan_video_folders(first, second, recursive=False)
            self.assertEqual(len(cross_result.groups), 1)
            self.assertEqual(cross_result.groups[0].recommended.path.name, "high.mp4")
            reference_result = scan_video_reference(high, second, recursive=False)
            self.assertEqual(len(reference_result.groups), 1)
            group = reference_result.groups[0]
            self.assertEqual(group.recommended.path.name, "high.mp4")
            self.assertTrue(any("码率更高" in reason for reason in group.recommendation_reasons))


if __name__ == "__main__":
    unittest.main()
