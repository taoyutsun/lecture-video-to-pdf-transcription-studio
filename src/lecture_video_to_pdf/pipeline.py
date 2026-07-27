from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import cv2

from .asr import run_asr_if_requested
from .models import AsrOptions, ConversionOptions, ConversionResult, SlideFrame, TaskType
from .pdf import images_to_pdf
from .utils import ensure_dir, safe_slug
from .video import (
    MODE_SETTINGS,
    create_thumbnail,
    crop_frame,
    detect_candidate_frames,
    difference_hash,
    frame_to_analysis_gray,
    hamming_distance,
    image_similarity,
    parse_crop,
    probe_video,
    read_frame_at,
    save_bgr_png,
)

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".webm",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".ts",
}

AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
}


def is_video_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def is_audio_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def is_supported_media_file(path: str | Path) -> bool:
    return is_video_file(path) or is_audio_file(path)


def _metadata_payload(result: ConversionResult) -> dict:
    return result.to_dict()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _job_root(source_path: Path, output_dir: str | Path) -> Path:
    started = time.strftime("%Y%m%d_%H%M%S")
    job_name = f"{safe_slug(source_path.stem)}_{started}"
    return ensure_dir(Path(output_dir) / job_name)


def run_transcription(
    media_path: str | Path,
    output_dir: str | Path = "output",
    asr_options: AsrOptions | None = None,
    progress=None,
) -> dict:
    media_path = Path(media_path)
    if not media_path.exists():
        raise FileNotFoundError(f"Media not found: {media_path}")
    if not is_supported_media_file(media_path):
        raise ValueError(f"Transcription supports common video/audio files, not: {media_path.suffix}")

    asr_options = asr_options or AsrOptions(engine="faster-whisper")
    if asr_options.engine == "none":
        raise ValueError("Transcription requires an ASR engine.")

    progress = progress or (lambda _ratio, _message: None)
    root = _job_root(media_path, output_dir)
    progress(0.05, "準備語音轉文字")
    asr_result = run_asr_if_requested(media_path, root, asr_options, [], progress, write_slide_map=False)
    warnings = list(asr_result.get("warnings", []))
    result = {
        "task": "transcription",
        "media_path": str(media_path),
        "video_path": str(media_path),
        "output_dir": str(root),
        "metadata_path": str(root / "metadata.json"),
        "pdf_path": None,
        "slides_dir": None,
        "thumbs_dir": None,
        "slides": [],
        "transcript_path": asr_result.get("transcript_path"),
        "transcript_srt_path": asr_result.get("transcript_srt_path"),
        "transcription_manifest_path": asr_result.get("transcription_manifest_path"),
        "slide_map_path": None,
        "warnings": warnings,
    }
    _write_json(root / "metadata.json", result)
    progress(1.0, "轉錄完成")
    return result


def run_conversion(
    video_path: str | Path,
    output_dir: str | Path = "output",
    options: ConversionOptions | None = None,
    progress=None,
) -> ConversionResult:
    options = options or ConversionOptions()
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not is_video_file(video_path):
        raise ValueError(f"PDF extraction requires a video file, not: {video_path.suffix}")
    progress = progress or (lambda _ratio, _message: None)

    root = _job_root(video_path, output_dir)
    slides_dir = ensure_dir(root / "slides")
    thumbs_dir = ensure_dir(root / "thumbs")

    progress(0.02, "讀取影片資訊")
    info = probe_video(video_path)
    crop = parse_crop(options.crop, info.width, info.height)
    settings = MODE_SETTINGS[options.mode]
    duplicate_ssim = options.duplicate_ssim_threshold or settings["duplicate_ssim"]
    duplicate_hash = options.duplicate_hash_threshold or settings["duplicate_hash"]

    progress(0.04, "偵測候選投影片時間點")
    candidates = detect_candidate_frames(video_path, info, options, progress)
    warnings: list[str] = []
    if len(candidates) <= 1:
        warnings.append("Only one candidate frame was detected. Try sensitive mode if slides are missing.")

    slides: list[SlideFrame] = []
    kept_frames: list[tuple[int, object, int]] = []

    for i, candidate in enumerate(candidates):
        ratio = 0.45 + ((i + 1) / max(len(candidates), 1)) * 0.28
        progress(ratio, f"抽取候選畫面 {i + 1}/{len(candidates)}")
        frame_number, frame = read_frame_at(video_path, candidate.time_seconds)
        cropped = crop_frame(frame, crop)
        analysis_gray = frame_to_analysis_gray(frame, crop)
        hash_value = difference_hash(analysis_gray)

        duplicate_of = None
        for kept_index, kept_frame, kept_hash in kept_frames[-8:]:
            hash_dist = hamming_distance(hash_value, kept_hash)
            try:
                ssim = image_similarity(cropped, kept_frame)
                if ssim >= duplicate_ssim or (hash_dist <= duplicate_hash and ssim >= duplicate_ssim):
                    duplicate_of = kept_index
                    break
            except Exception:
                if hash_dist <= 1:
                    duplicate_of = kept_index
                    break

        slide_path = slides_dir / f"slide_{len(slides) + 1:04d}_{candidate.time_seconds:08.2f}s.png"
        thumb_path = thumbs_dir / f"slide_{len(slides) + 1:04d}.jpg"
        save_bgr_png(cropped, slide_path)
        create_thumbnail(slide_path, thumb_path)

        kept = duplicate_of is None
        slide = SlideFrame(
            index=len(slides) + 1,
            time_seconds=round(candidate.time_seconds, 3),
            frame_number=frame_number,
            path=str(slide_path),
            thumb_path=str(thumb_path),
            diff_score=round(candidate.diff_score, 5),
            hash_value=f"{hash_value:016x}",
            duplicate_of=duplicate_of,
            kept=kept,
        )
        slides.append(slide)
        if kept:
            kept_frames.append((slide.index, cropped, hash_value))

    kept_slide_paths = [slide.path for slide in slides if slide.kept]
    progress(0.76, "產生 PDF")
    pdf_path = images_to_pdf(kept_slide_paths, root / "result.pdf")

    transcript_path = None
    transcript_srt_path = None
    transcription_manifest_path = None
    slide_map_path = None
    if options.asr.engine != "none":
        progress(0.80, "執行選配語音轉文字")
        try:
            asr_result = run_asr_if_requested(video_path, root, options.asr, slides, progress)
            transcript_path = asr_result.get("transcript_path")
            transcript_srt_path = asr_result.get("transcript_srt_path")
            transcription_manifest_path = asr_result.get("transcription_manifest_path")
            slide_map_path = asr_result.get("slide_map_path")
            warnings.extend(asr_result.get("warnings", []))
        except Exception as exc:
            warnings.append(f"ASR failed: {exc}")

    metadata_path = root / "metadata.json"
    result = ConversionResult(
        video=info,
        output_dir=str(root),
        pdf_path=str(pdf_path),
        slides_dir=str(slides_dir),
        thumbs_dir=str(thumbs_dir),
        metadata_path=str(metadata_path),
        slides=slides,
        transcript_path=transcript_path,
        transcript_srt_path=transcript_srt_path,
        transcription_manifest_path=transcription_manifest_path,
        slide_map_path=slide_map_path,
        warnings=warnings,
    )
    _write_json(metadata_path, _metadata_payload(result))
    progress(1.0, "處理完成")
    return result


def rebuild_pdf_from_metadata(metadata_path: str | Path) -> Path:
    data = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    slides = data.get("slides", [])
    kept_paths = [slide["path"] for slide in slides if slide.get("kept", True)]
    return images_to_pdf(kept_paths, Path(data["output_dir"]) / "result.pdf")


def run_media_job(
    media_path: str | Path,
    output_dir: str | Path = "output",
    options: ConversionOptions | None = None,
    task: TaskType = "slides",
    progress=None,
) -> dict:
    options = options or ConversionOptions()
    media_path = Path(media_path)
    if task == "transcription":
        return run_transcription(media_path, output_dir, options.asr, progress)
    if task == "slides":
        slides_only = replace(options, asr=AsrOptions(engine="none"))
        return run_conversion(media_path, output_dir, slides_only, progress=progress).to_dict()
    if task == "slides_and_transcription":
        if options.asr.engine == "none":
            raise ValueError("PDF + transcription requires an ASR engine.")
        return run_conversion(media_path, output_dir, options, progress=progress).to_dict()
    raise ValueError(f"Unsupported task: {task}")
