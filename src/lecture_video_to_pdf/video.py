from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity

from .models import ConversionOptions, ProgressCallback, VideoInfo


@dataclass
class CandidateFrame:
    time_seconds: float
    frame_number: int
    diff_score: float


@dataclass
class CropRect:
    x: int
    y: int
    width: int
    height: int


MODE_SETTINGS = {
    "fast": {
        "sample_fps": 1.0,
        "change_threshold": 0.11,
        "settle_threshold": 0.11,
        "min_gap": 1.2,
        "duplicate_ssim": 0.992,
        "duplicate_hash": 4,
    },
    "balanced": {
        "sample_fps": 2.0,
        "change_threshold": 0.065,
        "settle_threshold": 0.085,
        "min_gap": 0.8,
        "duplicate_ssim": 0.994,
        "duplicate_hash": 4,
    },
    "sensitive": {
        "sample_fps": 3.0,
        "change_threshold": 0.032,
        "settle_threshold": 0.065,
        "min_gap": 0.45,
        "duplicate_ssim": 0.996,
        "duplicate_hash": 3,
    },
}


def probe_video(video_path: str | Path) -> VideoInfo:
    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {path}")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if fps > 0 else 0.0
        return VideoInfo(
            path=str(path),
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            duration=duration,
        )
    finally:
        cap.release()


def parse_crop(crop: str | None, width: int, height: int) -> CropRect:
    if not crop or crop.strip().lower() == "auto":
        return CropRect(0, 0, width, height)
    parts = [p.strip() for p in crop.split(",")]
    if len(parts) != 4:
        raise ValueError("crop must be 'auto' or 'x,y,w,h'")
    x, y, w, h = [int(float(p)) for p in parts]
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return CropRect(x, y, w, h)


def crop_frame(frame: np.ndarray, crop: CropRect) -> np.ndarray:
    return frame[crop.y : crop.y + crop.height, crop.x : crop.x + crop.width]


def frame_to_analysis_gray(frame: np.ndarray, crop: CropRect, width: int = 360) -> np.ndarray:
    cropped = crop_frame(frame, crop)
    if cropped.size == 0:
        cropped = frame
    ratio = width / max(cropped.shape[1], 1)
    height = max(1, int(cropped.shape[0] * ratio))
    resized = cv2.resize(cropped, (width, height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)


def difference_hash(gray: np.ndarray, hash_size: int = 8) -> int:
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming_distance(a: int, b: int) -> int:
    return int((a ^ b).bit_count())


def normalized_change_score(prev_gray: np.ndarray, gray: np.ndarray, prev_hash: int, curr_hash: int) -> float:
    diff = float(np.mean(cv2.absdiff(prev_gray, gray)) / 255.0)
    mean_delta = abs(float(np.mean(prev_gray)) - float(np.mean(gray))) / 255.0
    hist_prev = cv2.calcHist([prev_gray], [0], None, [32], [0, 256])
    hist_curr = cv2.calcHist([gray], [0], None, [32], [0, 256])
    cv2.normalize(hist_prev, hist_prev)
    cv2.normalize(hist_curr, hist_curr)
    hist_delta = float(cv2.compareHist(hist_prev, hist_curr, cv2.HISTCMP_BHATTACHARYYA))
    edges_prev = cv2.Canny(prev_gray, 80, 160)
    edges_curr = cv2.Canny(gray, 80, 160)
    edge_diff = float(np.mean(cv2.absdiff(edges_prev, edges_curr)) / 255.0)
    hash_diff = hamming_distance(prev_hash, curr_hash) / 64.0
    return max(
        0.0,
        min(
            1.0,
            (hash_diff * 0.34)
            + (min(diff * 7.0, 1.0) * 0.30)
            + (edge_diff * 0.12)
            + (mean_delta * 0.10)
            + (hist_delta * 0.14),
        ),
    )


def is_near_black_frame(gray: np.ndarray) -> bool:
    return float(np.mean(gray)) < 10.0 and float(np.std(gray)) < 8.0


def iter_sampled_frames(video_path: str | Path, sample_fps: float) -> Iterable[tuple[int, float, np.ndarray]]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")
    native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(native_fps / max(sample_fps, 0.1))))
    index = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % step == 0:
                yield index, index / native_fps, frame
            index += 1
            if frame_count and index >= frame_count:
                break
    finally:
        cap.release()


def detect_candidate_frames(
    video_path: str | Path,
    info: VideoInfo,
    options: ConversionOptions,
    progress: ProgressCallback | None = None,
) -> list[CandidateFrame]:
    settings = MODE_SETTINGS[options.mode]
    sample_fps = options.sample_fps or settings["sample_fps"]
    crop = parse_crop(options.crop, info.width, info.height)

    candidates: list[CandidateFrame] = []
    previous_gray: np.ndarray | None = None
    previous_hash: int | None = None
    pending_change: CandidateFrame | None = None
    settle_count = 0
    last_added_time = 0.0
    initial_candidate_added = False

    for frame_number, timestamp, frame in iter_sampled_frames(video_path, sample_fps):
        if progress and info.duration:
            progress(min(0.42, (timestamp / info.duration) * 0.42), f"分析畫面變化 {timestamp:.1f}s")
        gray = frame_to_analysis_gray(frame, crop)
        curr_hash = difference_hash(gray)
        if previous_gray is None or previous_hash is None:
            previous_gray = gray
            previous_hash = curr_hash
            if not is_near_black_frame(gray):
                candidates.append(CandidateFrame(timestamp, frame_number, 0.0))
                last_added_time = timestamp
                initial_candidate_added = True
            continue

        if not initial_candidate_added:
            previous_gray = gray
            previous_hash = curr_hash
            if is_near_black_frame(gray):
                continue
            candidates.append(CandidateFrame(timestamp, frame_number, 0.0))
            last_added_time = timestamp
            initial_candidate_added = True
            continue

        score = normalized_change_score(previous_gray, gray, previous_hash, curr_hash)
        enough_gap = timestamp - last_added_time >= settings["min_gap"]
        if score >= settings["change_threshold"] and enough_gap:
            pending_change = CandidateFrame(timestamp, frame_number, score)
            settle_count = 0
            if score >= settings["change_threshold"] * 1.35:
                candidates.append(CandidateFrame(timestamp, frame_number, score))
                last_added_time = timestamp
                pending_change = None
        elif pending_change is not None:
            if score <= settings["settle_threshold"]:
                settle_count += 1
            else:
                settle_count = 0
            if settle_count >= 1 and timestamp - last_added_time >= settings["min_gap"]:
                candidates.append(CandidateFrame(timestamp, frame_number, pending_change.diff_score))
                last_added_time = timestamp
                pending_change = None
                settle_count = 0

        previous_gray = gray
        previous_hash = curr_hash

    if pending_change and pending_change.time_seconds - last_added_time >= settings["min_gap"]:
        candidates.append(pending_change)

    if not candidates:
        candidates.append(CandidateFrame(0.0, 0, 0.0))

    return sorted(candidates, key=lambda c: c.time_seconds)


def read_frame_at(video_path: str | Path, time_seconds: float) -> tuple[int, np.ndarray]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        frame_number = max(0, int(round(time_seconds * fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, frame = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_number - 1))
            ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"Unable to read frame at {time_seconds:.2f}s")
        return frame_number, frame
    finally:
        cap.release()


def image_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a_gray = cv2.cvtColor(cv2.resize(a, (360, 202), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    b_gray = cv2.cvtColor(cv2.resize(b, (360, 202), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    return float(structural_similarity(a_gray, b_gray, data_range=255))


def save_bgr_png(frame: np.ndarray, path: str | Path) -> None:
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError(f"Unable to encode PNG: {path}")
    Path(path).write_bytes(encoded.tobytes())


def create_thumbnail(source: str | Path, dest: str | Path, width: int = 360) -> None:
    image = Image.open(source)
    image.thumbnail((width, width), Image.Resampling.LANCZOS)
    image.save(dest, "JPEG", quality=88)
