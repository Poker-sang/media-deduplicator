from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".gif",
    ".heic",
    ".heif",
    ".avif",
}

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".avi",
    ".wmv",
    ".webm",
    ".3gp",
    ".ts",
    ".mts",
    ".m2ts",
}

ProgressCallback = Callable[[str, int, int, str], None]


@dataclass(frozen=True)
class Sensitivity:
    name: str
    max_hash_distance: int
    max_aspect_delta: float
    min_ssim: float
    min_score: float


SENSITIVITIES = {
    "strict": Sensitivity("严格", 7, 0.025, 0.945, 0.935),
    "balanced": Sensitivity("平衡", 13, 0.045, 0.885, 0.885),
    "loose": Sensitivity("宽松", 19, 0.080, 0.800, 0.825),
}


class ScanCancelled(RuntimeError):
    pass


@dataclass
class ImageFeature:
    path: Path
    width: int
    height: int
    file_size: int
    image_format: str
    phash: int
    gray_thumb: np.ndarray = field(repr=False)
    color_hist: np.ndarray = field(repr=False)
    sharpness: float
    exposure_score: float
    compression_score: float
    has_exif: bool
    quality_score: float
    _sha256: str | None = field(default=None, repr=False)
    _sift_keypoints: Any = field(default=None, repr=False)
    _sift_descriptors: np.ndarray | None = field(default=None, repr=False)
    _sift_size: tuple[int, int] | None = field(default=None, repr=False)
    _sift_attempted: bool = field(default=False, repr=False)

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1_000_000

    @property
    def dimensions(self) -> str:
        return f"{self.width} x {self.height}"

    @property
    def display_format(self) -> str:
        return self.image_format or self.path.suffix.lstrip(".").upper()

    def sha256(self) -> str:
        if self._sha256 is None:
            digest = hashlib.sha256()
            with self.path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            self._sha256 = digest.hexdigest()
        return self._sha256


@dataclass
class FrameFeature:
    phash: int
    gray_thumb: np.ndarray = field(repr=False)
    color_hist: np.ndarray = field(repr=False)
    sharpness: float


@dataclass
class VideoFeature:
    path: Path
    width: int
    height: int
    file_size: int
    video_format: str
    duration: float
    fps: float
    frame_count: int
    bitrate_kbps: float
    frame_features: tuple[FrameFeature, ...] = field(repr=False)
    preview_rgb: np.ndarray = field(repr=False)
    sharpness: float
    quality_score: float
    has_exif: bool = False

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1_000_000

    @property
    def dimensions(self) -> str:
        return f"{self.width} x {self.height}"

    @property
    def display_format(self) -> str:
        return self.video_format or self.path.suffix.lstrip(".").upper()

    @property
    def duration_label(self) -> str:
        total_seconds = max(0.0, self.duration)
        minutes, seconds = divmod(total_seconds, 60)
        if minutes >= 60:
            hours, minutes = divmod(minutes, 60)
            return f"{int(hours)}:{int(minutes):02d}:{seconds:04.1f}"
        return f"{int(minutes)}:{seconds:04.1f}"

    @property
    def bitrate_label(self) -> str:
        if self.bitrate_kbps >= 1000:
            return f"{self.bitrate_kbps / 1000:.1f} Mbps"
        return f"{self.bitrate_kbps:.0f} Kbps"


@dataclass(frozen=True)
class ImageMatch:
    first: ImageFeature
    second: ImageFeature
    score: float
    kind: str
    detail: str


@dataclass
class DuplicateGroup:
    images: list[ImageFeature]
    matches: list[ImageMatch]
    recommended: ImageFeature
    recommendation_reasons: list[str]

    @property
    def confidence(self) -> float:
        return max(match.score for match in self.matches)

    @property
    def kind(self) -> str:
        kinds = {match.kind for match in self.matches}
        if kinds == {"完全相同"}:
            return "完全相同"
        if "疑似局部/构图变化" in kinds:
            return "含局部匹配"
        return "近似重复"


@dataclass
class ScanResult:
    groups: list[DuplicateGroup]
    scanned_count: int
    compared_count: int
    errors: list[str]


def list_images(folder: Path, recursive: bool = True) -> list[Path]:
    folder = folder.expanduser().resolve()
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=lambda path: str(path).lower(),
    )


def list_videos(folder: Path, recursive: bool = True) -> list[Path]:
    folder = folder.expanduser().resolve()
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS),
        key=lambda path: str(path).lower(),
    )


def _open_rgb(path: Path) -> tuple[Image.Image, str, bool]:
    with Image.open(path) as source:
        try:
            source.seek(0)
        except EOFError:
            pass
        image_format = (source.format or path.suffix.lstrip(".")).upper()
        has_exif = bool(source.getexif())
        oriented = ImageOps.exif_transpose(source)
        if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
            rgba = oriented.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(background, rgba).convert("RGB")
        else:
            image = oriented.convert("RGB")
        image.load()
    return image, image_format, has_exif


def _resize_long_edge(array: np.ndarray, max_edge: int) -> np.ndarray:
    height, width = array.shape[:2]
    scale = min(1.0, max_edge / max(height, width))
    if scale == 1.0:
        return array
    return cv2.resize(
        array,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _perceptual_hash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(small)
    low_frequency = dct[:8, :8]
    median = float(np.median(low_frequency.flatten()[1:]))
    value = 0
    for bit in (low_frequency > median).flatten():
        value = (value << 1) | int(bit)
    return value


def _color_histogram(rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    return cv2.normalize(histogram, histogram).flatten().astype(np.float32)


def _frame_feature(frame_bgr: np.ndarray) -> FrameFeature:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray_thumb = cv2.resize(gray, (192, 192), interpolation=cv2.INTER_AREA)
    quality_sample = cv2.resize(gray, (512, 512), interpolation=cv2.INTER_AREA)
    return FrameFeature(
        phash=_perceptual_hash(gray),
        gray_thumb=gray_thumb,
        color_hist=_color_histogram(_resize_long_edge(rgb, 640)),
        sharpness=float(cv2.Laplacian(quality_sample, cv2.CV_64F).var()),
    )


def _video_format_score(video_format: str) -> float:
    return {
        "MP4": 0.90,
        "MOV": 0.90,
        "M4V": 0.88,
        "MKV": 0.88,
        "WEBM": 0.84,
        "AVI": 0.65,
        "WMV": 0.62,
    }.get(video_format.upper(), 0.75)


def _feature_format(feature: Any) -> str:
    return str(getattr(feature, "image_format", getattr(feature, "video_format", "")))


def _feature_format_score(feature: Any) -> float:
    if hasattr(feature, "video_format"):
        return _video_format_score(feature.video_format)
    return _format_score(_feature_format(feature))


def _compression_score(gray: np.ndarray) -> float:
    sample = cv2.resize(gray, (512, 512), interpolation=cv2.INTER_AREA)
    sample = sample.astype(np.float32)
    vertical = np.abs(sample[:, 8::8] - sample[:, 7:-1:8]).mean()
    horizontal = np.abs(sample[8::8, :] - sample[7:-1:8, :]).mean()
    ordinary_v = np.abs(sample[:, 5::8] - sample[:, 4::8]).mean()
    ordinary_h = np.abs(sample[5::8, :] - sample[4::8, :]).mean()
    excess = max(0.0, ((vertical + horizontal) - (ordinary_v + ordinary_h)) / 2)
    return float(np.clip(1.0 - excess / 12.0, 0.0, 1.0))


def _format_score(image_format: str) -> float:
    return {
        "TIFF": 1.0,
        "TIF": 1.0,
        "PNG": 0.96,
        "BMP": 0.92,
        "WEBP": 0.84,
        "HEIF": 0.83,
        "HEIC": 0.83,
        "AVIF": 0.83,
        "JPEG": 0.76,
        "JPG": 0.76,
        "GIF": 0.58,
    }.get(image_format.upper(), 0.72)


def extract_features(path: Path) -> ImageFeature:
    path = path.expanduser().resolve()
    image, image_format, has_exif = _open_rgb(path)
    rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape

    gray_thumb = cv2.resize(gray, (192, 192), interpolation=cv2.INTER_AREA)
    color_sample = _resize_long_edge(rgb, 640)
    quality_sample = cv2.resize(gray, (512, 512), interpolation=cv2.INTER_AREA)
    sharpness = float(cv2.Laplacian(quality_sample, cv2.CV_64F).var())

    clipped = float(np.mean((quality_sample <= 4) | (quality_sample >= 251)))
    exposure_score = float(np.clip(1.0 - clipped * 3.5, 0.0, 1.0))
    compression_score = _compression_score(gray)
    megapixels = width * height / 1_000_000
    resolution_score = min(1.0, math.log1p(megapixels) / math.log1p(24.0))
    sharpness_score = float(np.clip(math.log1p(sharpness) / math.log1p(1400.0), 0.0, 1.0))
    quality_score = 100 * (
        0.43 * resolution_score
        + 0.30 * sharpness_score
        + 0.14 * exposure_score
        + 0.08 * compression_score
        + 0.05 * _format_score(image_format)
    )

    return ImageFeature(
        path=path,
        width=width,
        height=height,
        file_size=path.stat().st_size,
        image_format=image_format,
        phash=_perceptual_hash(gray),
        gray_thumb=gray_thumb,
        color_hist=_color_histogram(color_sample),
        sharpness=sharpness,
        exposure_score=exposure_score,
        compression_score=compression_score,
        has_exif=has_exif,
        quality_score=quality_score,
    )


def extract_video_features(path: Path) -> VideoFeature:
    path = path.expanduser().resolve()
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("无法打开视频，可能缺少对应编码器")
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
        if width <= 0 or height <= 0:
            raise ValueError("视频没有有效的画面尺寸")

        # Avoid exact endpoints: intro/outro frames are often black or removed by edits.
        sample_positions = (0.12, 0.50, 0.88)
        frames: list[np.ndarray] = []
        for position in sample_positions:
            if frame_count > 1:
                capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int((frame_count - 1) * position)))
            elif duration > 0:
                capture.set(cv2.CAP_PROP_POS_MSEC, duration * position * 1000)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise ValueError("无法读取视频采样帧")
            frames.append(frame)

        # Average payload bitrate is more comparable across transcodes than the
        # container's nominal codec property, which is often reported unchanged.
        bitrate_kbps = (path.stat().st_size * 8 / 1000 / duration) if duration > 0 else 0.0
        frame_features = tuple(_frame_feature(frame) for frame in frames)
        sharpness = float(np.median([frame.sharpness for frame in frame_features]))
        megapixels = width * height / 1_000_000
        resolution_score = min(1.0, math.log1p(megapixels) / math.log1p(24.0))
        bitrate_score = min(1.0, math.log1p(max(0.0, bitrate_kbps)) / math.log1p(50000.0))
        sharpness_score = float(np.clip(math.log1p(sharpness) / math.log1p(1400.0), 0.0, 1.0))
        quality_score = 100 * (
            0.35 * resolution_score
            + 0.45 * bitrate_score
            + 0.15 * sharpness_score
            + 0.05 * _video_format_score(path.suffix.lstrip("."))
        )
        preview_rgb = _resize_long_edge(cv2.cvtColor(frames[1], cv2.COLOR_BGR2RGB), 640)
        return VideoFeature(
            path=path,
            width=width,
            height=height,
            file_size=path.stat().st_size,
            video_format=path.suffix.lstrip(".").upper(),
            duration=duration,
            fps=fps,
            frame_count=frame_count,
            bitrate_kbps=bitrate_kbps,
            frame_features=frame_features,
            preview_rgb=preview_rgb,
            sharpness=sharpness,
            quality_score=quality_score,
        )
    finally:
        capture.release()


def _hash_distance(first: int, second: int) -> int:
    return (first ^ second).bit_count()


def _ssim(first: np.ndarray, second: np.ndarray) -> float:
    first_f = first.astype(np.float64)
    second_f = second.astype(np.float64)
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    mu_first = cv2.GaussianBlur(first_f, (11, 11), 1.5)
    mu_second = cv2.GaussianBlur(second_f, (11, 11), 1.5)
    mu_first_sq = mu_first * mu_first
    mu_second_sq = mu_second * mu_second
    mu_both = mu_first * mu_second
    sigma_first = cv2.GaussianBlur(first_f * first_f, (11, 11), 1.5) - mu_first_sq
    sigma_second = cv2.GaussianBlur(second_f * second_f, (11, 11), 1.5) - mu_second_sq
    sigma_both = cv2.GaussianBlur(first_f * second_f, (11, 11), 1.5) - mu_both
    numerator = (2 * mu_both + c1) * (2 * sigma_both + c2)
    denominator = (mu_first_sq + mu_second_sq + c1) * (sigma_first + sigma_second + c2)
    return float(np.clip(np.mean(numerator / np.maximum(denominator, 1e-12)), -1.0, 1.0))


def _standard_match(
    first: ImageFeature, second: ImageFeature, sensitivity: Sensitivity
) -> ImageMatch | None:
    hash_distance = _hash_distance(first.phash, second.phash)

    if (
        hash_distance == 0
        and first.file_size == second.file_size
        and first.width == second.width
        and first.height == second.height
        and first.sha256() == second.sha256()
    ):
        return ImageMatch(first, second, 1.0, "完全相同", "文件内容完全一致")

    aspect_first = first.width / first.height
    aspect_second = second.width / second.height
    aspect_delta = abs(math.log(aspect_first / aspect_second))
    if aspect_delta > sensitivity.max_aspect_delta or hash_distance > sensitivity.max_hash_distance:
        return None

    structural = _ssim(first.gray_thumb, second.gray_thumb)
    histogram_correlation = float(
        cv2.compareHist(first.color_hist, second.color_hist, cv2.HISTCMP_CORREL)
    )
    color_similarity = float(np.clip((histogram_correlation + 1.0) / 2.0, 0.0, 1.0))
    hash_similarity = 1.0 - hash_distance / 64.0
    # Color may shift substantially after low-quality JPEG recompression, so it is
    # a sanity signal rather than the main identity signal.
    score = 0.55 * structural + 0.40 * hash_similarity + 0.05 * color_similarity

    if structural < sensitivity.min_ssim or score < sensitivity.min_score:
        return None
    detail = f"结构 {structural:.0%} · 感知哈希差 {hash_distance}"
    return ImageMatch(first, second, float(np.clip(score, 0.0, 1.0)), "近似重复", detail)


def _ensure_sift(feature: ImageFeature) -> None:
    if feature._sift_attempted:
        return
    feature._sift_attempted = True
    image, _, _ = _open_rgb(feature.path)
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    gray = _resize_long_edge(gray, 800)
    sift = cv2.SIFT_create(nfeatures=900, contrastThreshold=0.035, edgeThreshold=12)
    keypoints, descriptors = sift.detectAndCompute(gray, None)
    feature._sift_keypoints = keypoints
    feature._sift_descriptors = descriptors
    feature._sift_size = (gray.shape[1], gray.shape[0])


def _hull_coverage(points: np.ndarray, width: int, height: int) -> float:
    if len(points) < 3:
        return 0.0
    hull = cv2.convexHull(points.astype(np.float32))
    return float(cv2.contourArea(hull) / max(1, width * height))


def _crop_match(first: ImageFeature, second: ImageFeature) -> ImageMatch | None:
    _ensure_sift(first)
    _ensure_sift(second)
    if first._sift_descriptors is None or second._sift_descriptors is None:
        return None
    if len(first._sift_descriptors) < 12 or len(second._sift_descriptors) < 12:
        return None

    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(first._sift_descriptors, second._sift_descriptors, k=2)
    good = [near for near, far in pairs if near.distance < 0.72 * far.distance]
    if len(good) < 12:
        return None

    first_points = np.float32([first._sift_keypoints[item.queryIdx].pt for item in good])
    second_points = np.float32([second._sift_keypoints[item.trainIdx].pt for item in good])
    _, mask = cv2.findHomography(first_points, second_points, cv2.RANSAC, 4.5)
    if mask is None:
        return None
    inlier_mask = mask.ravel().astype(bool)
    inliers = int(inlier_mask.sum())
    inlier_ratio = inliers / len(good)
    if inliers < 10 or inlier_ratio < 0.52:
        return None

    first_sift_width, first_sift_height = first._sift_size or (first.width, first.height)
    second_sift_width, second_sift_height = second._sift_size or (second.width, second.height)
    first_coverage = _hull_coverage(
        first_points[inlier_mask], first_sift_width, first_sift_height
    )
    second_coverage = _hull_coverage(
        second_points[inlier_mask], second_sift_width, second_sift_height
    )
    if max(first_coverage, second_coverage) < 0.045:
        return None

    confidence = min(0.97, 0.58 + 0.23 * inlier_ratio + 0.16 * min(1.0, inliers / 45))
    detail = f"{inliers} 个几何内点 · 一致率 {inlier_ratio:.0%}"
    return ImageMatch(first, second, confidence, "疑似局部/构图变化", detail)


def compare_images(
    first: ImageFeature,
    second: ImageFeature,
    sensitivity: Sensitivity,
    detect_crops: bool = False,
) -> ImageMatch | None:
    standard = _standard_match(first, second, sensitivity)
    if standard is not None:
        return standard
    if detect_crops:
        return _crop_match(first, second)
    return None


def _recommend(images: Iterable[ImageFeature]) -> tuple[ImageFeature, list[str]]:
    ordered = sorted(images, key=lambda item: (item.quality_score, item.file_size), reverse=True)
    winner = ordered[0]
    runner_up = ordered[1] if len(ordered) > 1 else None
    reasons: list[str] = []
    if runner_up is not None:
        if winner.megapixels >= runner_up.megapixels * 1.08:
            reasons.append(f"分辨率更高（{winner.megapixels:.1f} MP）")
        if winner.sharpness >= runner_up.sharpness * 1.10:
            reasons.append("细节清晰度更好")
        if hasattr(winner, "bitrate_kbps") and winner.bitrate_kbps >= runner_up.bitrate_kbps * 1.10:
            reasons.append(f"码率更高（{winner.bitrate_label}）")
        if hasattr(winner, "compression_score") and winner.compression_score >= runner_up.compression_score + 0.10:
            reasons.append("压缩痕迹更少")
        if hasattr(winner, "exposure_score") and winner.exposure_score >= runner_up.exposure_score + 0.10:
            reasons.append("高光和暗部保留更完整")
        if _feature_format_score(winner) >= _feature_format_score(runner_up) + 0.12:
            reasons.append(f"{winner.display_format} 格式更利于保留")
    if winner.has_exif:
        reasons.append("保留了 EXIF 信息")
    if not reasons:
        reasons.append("综合画质评分最高")
    return winner, reasons[:3]


def _build_groups(features: list[ImageFeature], matches: list[ImageMatch]) -> list[DuplicateGroup]:
    parent = {str(feature.path): str(feature.path) for feature in features}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(first: str, second: str) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    for match in matches:
        union(str(match.first.path), str(match.second.path))

    grouped_images: dict[str, list[ImageFeature]] = {}
    for feature in features:
        root = find(str(feature.path))
        grouped_images.setdefault(root, []).append(feature)

    groups: list[DuplicateGroup] = []
    for images in grouped_images.values():
        if len(images) < 2:
            continue
        paths = {str(image.path) for image in images}
        group_matches = [
            match
            for match in matches
            if str(match.first.path) in paths and str(match.second.path) in paths
        ]
        recommended, reasons = _recommend(images)
        groups.append(
            DuplicateGroup(
                images=sorted(images, key=lambda item: item.quality_score, reverse=True),
                matches=group_matches,
                recommended=recommended,
                recommendation_reasons=reasons,
            )
        )
    return sorted(groups, key=lambda group: (len(group.images), group.confidence), reverse=True)


def _load_many(
    paths: list[Path], progress: ProgressCallback | None, cancel: Event | None
) -> tuple[list[ImageFeature], list[str]]:
    features: list[ImageFeature] = []
    errors: list[str] = []
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()
        if progress:
            progress("读取图片", index - 1, total, path.name)
        try:
            features.append(extract_features(path))
        except Exception as exc:
            errors.append(f"{path}: {exc}")
        if progress:
            progress("读取图片", index, total, path.name)
    return features, errors


def scan_folder(
    folder: Path,
    recursive: bool = True,
    sensitivity: str = "balanced",
    detect_crops: bool = False,
    progress: ProgressCallback | None = None,
    cancel: Event | None = None,
) -> ScanResult:
    paths = list_images(folder, recursive)
    features, errors = _load_many(paths, progress, cancel)
    matches: list[ImageMatch] = []
    total_pairs = len(features) * (len(features) - 1) // 2
    compared = 0
    setting = SENSITIVITIES[sensitivity]
    for first_index, first in enumerate(features):
        for second in features[first_index + 1 :]:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled()
            match = compare_images(first, second, setting, detect_crops)
            if match is not None:
                matches.append(match)
            compared += 1
            if progress and (compared % 5 == 0 or compared == total_pairs):
                progress("两两比较", compared, total_pairs, f"已发现 {len(matches)} 对")
    return ScanResult(_build_groups(features, matches), len(features), compared, errors)


def scan_folders(
    first_folder: Path,
    second_folder: Path,
    recursive: bool = True,
    sensitivity: str = "balanced",
    detect_crops: bool = False,
    progress: ProgressCallback | None = None,
    cancel: Event | None = None,
) -> ScanResult:
    first_paths = list_images(first_folder, recursive)
    second_paths = list_images(second_folder, recursive)
    unique_paths = list(dict.fromkeys([*first_paths, *second_paths]))
    features, errors = _load_many(unique_paths, progress, cancel)
    feature_by_path = {str(feature.path): feature for feature in features}
    first_features = [
        feature_by_path[str(path.resolve())]
        for path in first_paths
        if str(path.resolve()) in feature_by_path
    ]
    second_features = [
        feature_by_path[str(path.resolve())]
        for path in second_paths
        if str(path.resolve()) in feature_by_path
    ]

    matches: list[ImageMatch] = []
    setting = SENSITIVITIES[sensitivity]
    total_pairs = len(first_features) * len(second_features)
    processed = 0
    compared = 0
    first_path_set = {str(feature.path) for feature in first_features}
    second_path_set = {str(feature.path) for feature in second_features}
    seen_pairs: set[tuple[str, str]] | None = (
        set() if first_path_set.intersection(second_path_set) else None
    )

    for first in first_features:
        for second in second_features:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled()
            processed += 1
            first_key, second_key = str(first.path), str(second.path)
            if first_key == second_key:
                continue
            if seen_pairs is not None:
                pair_key = tuple(sorted((first_key, second_key)))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
            match = compare_images(first, second, setting, detect_crops)
            if match is not None:
                matches.append(match)
            compared += 1
            if progress and (processed % 5 == 0 or processed == total_pairs):
                progress(
                    "文件夹交叉比较",
                    processed,
                    total_pairs,
                    f"已发现 {len(matches)} 对",
                )
    if progress and total_pairs and processed == total_pairs:
        progress("文件夹交叉比较", total_pairs, total_pairs, f"已发现 {len(matches)} 对")
    return ScanResult(_build_groups(features, matches), len(features), compared, errors)


def scan_reference(
    reference: Path,
    folder: Path,
    recursive: bool = True,
    sensitivity: str = "balanced",
    detect_crops: bool = False,
    progress: ProgressCallback | None = None,
    cancel: Event | None = None,
) -> ScanResult:
    reference = reference.expanduser().resolve()
    folder_paths = [path for path in list_images(folder, recursive) if path.resolve() != reference]
    paths = [reference, *folder_paths]
    features, errors = _load_many(paths, progress, cancel)
    if not features or features[0].path != reference:
        return ScanResult([], len(features), 0, errors or [f"无法读取基准图片：{reference}"])

    reference_feature = features[0]
    candidates = features[1:]
    matches: list[ImageMatch] = []
    setting = SENSITIVITIES[sensitivity]
    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()
        match = compare_images(reference_feature, candidate, setting, detect_crops)
        if match is not None:
            matches.append(match)
        if progress:
            progress("逐张比较", index, total, f"已发现 {len(matches)} 张")

    involved_paths = {str(reference_feature.path)}
    for match in matches:
        involved_paths.add(str(match.second.path))
    involved = [feature for feature in features if str(feature.path) in involved_paths]
    return ScanResult(_build_groups(involved, matches), len(features), total, errors)


def _load_many_videos(
    paths: list[Path], progress: ProgressCallback | None, cancel: Event | None
) -> tuple[list[VideoFeature], list[str]]:
    features: list[VideoFeature] = []
    errors: list[str] = []
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()
        if progress:
            progress("读取视频", index - 1, total, path.name)
        try:
            features.append(extract_video_features(path))
        except Exception as exc:
            errors.append(f"{path}: {exc}")
        if progress:
            progress("读取视频", index, total, path.name)
    return features, errors


def _video_frame_similarity(first: FrameFeature, second: FrameFeature) -> tuple[float, int, float]:
    hash_distance = _hash_distance(first.phash, second.phash)
    structural = _ssim(first.gray_thumb, second.gray_thumb)
    histogram_correlation = float(
        cv2.compareHist(first.color_hist, second.color_hist, cv2.HISTCMP_CORREL)
    )
    color_similarity = float(np.clip((histogram_correlation + 1.0) / 2.0, 0.0, 1.0))
    score = 0.55 * structural + 0.40 * (1.0 - hash_distance / 64.0) + 0.05 * color_similarity
    return float(np.clip(score, 0.0, 1.0)), hash_distance, structural


def compare_videos(
    first: VideoFeature,
    second: VideoFeature,
    sensitivity: Sensitivity,
) -> ImageMatch | None:
    if first.duration > 0 and second.duration > 0:
        duration_tolerance = min(2.0, max(1.0, max(first.duration, second.duration) * 0.10))
        duration_delta = abs(first.duration - second.duration)
        if duration_delta > duration_tolerance:
            return None
    if len(first.frame_features) != 3 or len(second.frame_features) != 3:
        return None

    settings = {
        "严格": (0.80, 0.90, 14),
        "平衡": (0.68, 0.80, 22),
        "宽松": (0.58, 0.72, 30),
    }
    minimum_frame_score, minimum_average_score, maximum_hash_distance = settings.get(
        sensitivity.name, settings["平衡"]
    )
    frame_scores: list[float] = []
    frame_details: list[str] = []
    for first_frame, second_frame in zip(first.frame_features, second.frame_features):
        score, hash_distance, structural = _video_frame_similarity(first_frame, second_frame)
        if hash_distance > maximum_hash_distance or score < minimum_frame_score:
            return None
        frame_scores.append(score)
        frame_details.append(f"{structural:.0%}")
    average_score = float(np.mean(frame_scores))
    if average_score < minimum_average_score:
        return None
    duration_delta = abs(first.duration - second.duration)
    detail = f"时长差 {duration_delta:.1f} 秒 · 前/中/后 {', '.join(frame_details)}"
    return ImageMatch(first, second, average_score, "视频近似重复", detail)


def scan_video_folder(
    folder: Path,
    recursive: bool = True,
    sensitivity: str = "balanced",
    progress: ProgressCallback | None = None,
    cancel: Event | None = None,
) -> ScanResult:
    paths = list_videos(folder, recursive)
    features, errors = _load_many_videos(paths, progress, cancel)
    matches: list[ImageMatch] = []
    total_pairs = len(features) * (len(features) - 1) // 2
    compared = 0
    setting = SENSITIVITIES[sensitivity]
    for first_index, first in enumerate(features):
        for second in features[first_index + 1 :]:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled()
            match = compare_videos(first, second, setting)
            if match is not None:
                matches.append(match)
            compared += 1
            if progress and (compared % 3 == 0 or compared == total_pairs):
                progress("视频两两比较", compared, total_pairs, f"已发现 {len(matches)} 对")
    return ScanResult(_build_groups(features, matches), len(features), compared, errors)


def scan_video_folders(
    first_folder: Path,
    second_folder: Path,
    recursive: bool = True,
    sensitivity: str = "balanced",
    progress: ProgressCallback | None = None,
    cancel: Event | None = None,
) -> ScanResult:
    first_paths = list_videos(first_folder, recursive)
    second_paths = list_videos(second_folder, recursive)
    unique_paths = list(dict.fromkeys([*first_paths, *second_paths]))
    features, errors = _load_many_videos(unique_paths, progress, cancel)
    feature_by_path = {str(feature.path): feature for feature in features}
    first_features = [feature_by_path[str(path.resolve())] for path in first_paths if str(path.resolve()) in feature_by_path]
    second_features = [feature_by_path[str(path.resolve())] for path in second_paths if str(path.resolve()) in feature_by_path]
    matches: list[ImageMatch] = []
    setting = SENSITIVITIES[sensitivity]
    total_pairs = len(first_features) * len(second_features)
    processed = 0
    compared = 0
    first_path_set = {str(feature.path) for feature in first_features}
    second_path_set = {str(feature.path) for feature in second_features}
    seen_pairs: set[tuple[str, str]] | None = set() if first_path_set.intersection(second_path_set) else None
    for first in first_features:
        for second in second_features:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled()
            processed += 1
            first_key, second_key = str(first.path), str(second.path)
            if first_key == second_key:
                continue
            if seen_pairs is not None:
                pair_key = tuple(sorted((first_key, second_key)))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
            match = compare_videos(first, second, setting)
            if match is not None:
                matches.append(match)
            compared += 1
            if progress and (processed % 3 == 0 or processed == total_pairs):
                progress("视频文件夹交叉比较", processed, total_pairs, f"已发现 {len(matches)} 对")
    return ScanResult(_build_groups(features, matches), len(features), compared, errors)


def scan_video_reference(
    reference: Path,
    folder: Path,
    recursive: bool = True,
    sensitivity: str = "balanced",
    progress: ProgressCallback | None = None,
    cancel: Event | None = None,
) -> ScanResult:
    reference = reference.expanduser().resolve()
    folder_paths = [path for path in list_videos(folder, recursive) if path.resolve() != reference]
    paths = [reference, *folder_paths]
    features, errors = _load_many_videos(paths, progress, cancel)
    if not features or features[0].path != reference:
        return ScanResult([], len(features), 0, errors or [f"无法读取基准视频：{reference}"])
    reference_feature = features[0]
    setting = SENSITIVITIES[sensitivity]
    matches: list[ImageMatch] = []
    total = len(features) - 1
    for index, candidate in enumerate(features[1:], start=1):
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()
        match = compare_videos(reference_feature, candidate, setting)
        if match is not None:
            matches.append(match)
        if progress:
            progress("视频逐张比较", index, total, f"已发现 {len(matches)} 张")
    involved_paths = {str(reference_feature.path)}
    for match in matches:
        involved_paths.add(str(match.second.path))
    involved = [feature for feature in features if str(feature.path) in involved_paths]
    return ScanResult(_build_groups(involved, matches), len(features), total, errors)
