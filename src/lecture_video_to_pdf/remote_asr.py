from __future__ import annotations

import json
import mimetypes
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import AsrOptions, TranscriptSegment
from .utils import ensure_dir, find_ffmpeg

BYTES_PER_MB = 1024 * 1024
CHUNK_OVERLAP_SECONDS = 1.0
MIN_CHUNK_SECONDS = 60.0
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
TRANSCRIPTION_TIMEOUT = httpx.Timeout(connect=20.0, read=900.0, write=900.0, pool=20.0)


@dataclass
class AudioChunk:
    index: int
    path: Path
    core_start: float
    core_end: float
    media_start: float
    media_end: float
    size_bytes: int

    def manifest_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = self.path.name
        data["status"] = "prepared"
        data["attempts"] = 0
        return data


def is_local_endpoint(base_url: str) -> bool:
    try:
        hostname = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    return hostname in {"127.0.0.1", "localhost", "::1"}


def resolve_upload_strategy(media_path: Path, options: AsrOptions) -> str:
    strategy = (options.endpoint_upload_strategy or "auto").lower()
    if strategy in {"direct", "chunked"}:
        return strategy
    if is_local_endpoint(options.endpoint_base_url):
        return "direct"
    max_bytes = max(5, min(95, int(options.endpoint_max_chunk_mb))) * BYTES_PER_MB
    return "direct" if media_path.stat().st_size <= max_bytes else "chunked"


def _parse_ffmpeg_duration(stderr: str) -> float:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not match:
        raise RuntimeError("FFmpeg could not determine the media duration.")
    hours, minutes, seconds = match.groups()
    return (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)


def probe_media_duration(media_path: Path, ffmpeg_path: Path | None = None) -> float:
    ffmpeg = ffmpeg_path or find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required for chunked cloud transcription but was not found.")
    completed = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(media_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if " Audio:" not in completed.stderr:
        raise RuntimeError("No audio track was found in the selected media.")
    return _parse_ffmpeg_duration(completed.stderr)


def _encode_audio_range(
    media_path: Path,
    output_path: Path,
    media_start: float,
    media_end: float,
    ffmpeg_path: Path,
) -> None:
    duration = max(0.01, media_end - media_start)
    command = [
        str(ffmpeg_path),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{media_start:.3f}",
        "-i",
        str(media_path),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "flac",
        "-y",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        detail = completed.stderr.strip().replace("\n", " ")[:500]
        raise RuntimeError(f"FFmpeg failed to prepare an audio chunk: {detail}")


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_audio_chunks(
    media_path: Path,
    output_dir: Path,
    options: AsrOptions,
    progress,
    ffmpeg_path: Path | None = None,
    overlap_seconds: float = CHUNK_OVERLAP_SECONDS,
) -> tuple[list[AudioChunk], Path, Path]:
    ffmpeg = ffmpeg_path or find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required for chunked cloud transcription but was not found.")

    max_chunk_mb = max(5, min(95, int(options.endpoint_max_chunk_mb)))
    max_bytes = max_chunk_mb * BYTES_PER_MB
    chunk_seconds = max(2, min(30, int(options.endpoint_chunk_minutes))) * 60.0
    duration = probe_media_duration(media_path, ffmpeg)
    chunk_dir = ensure_dir(output_dir / ".asr_chunks")
    manifest_path = output_dir / "transcription_manifest.json"
    chunks: list[AudioChunk] = []
    next_index = 1

    progress(0.80, "分析媒體音軌")

    def encode_core(core_start: float, core_end: float) -> None:
        nonlocal next_index
        media_start = max(0.0, core_start - overlap_seconds)
        media_end = min(duration, core_end + overlap_seconds)
        path = chunk_dir / f"chunk_{next_index:04d}.flac"
        _encode_audio_range(media_path, path, media_start, media_end, ffmpeg)
        size_bytes = path.stat().st_size
        if size_bytes > max_bytes and (core_end - core_start) > MIN_CHUNK_SECONDS:
            path.unlink()
            midpoint = core_start + ((core_end - core_start) / 2.0)
            encode_core(core_start, midpoint)
            encode_core(midpoint, core_end)
            return
        if size_bytes > max_bytes:
            path.unlink()
            raise RuntimeError(
                f"An encoded audio chunk is still larger than {max_chunk_mb} MB at the minimum chunk duration."
            )
        chunks.append(
            AudioChunk(
                index=next_index,
                path=path,
                core_start=round(core_start, 3),
                core_end=round(core_end, 3),
                media_start=round(media_start, 3),
                media_end=round(media_end, 3),
                size_bytes=size_bytes,
            )
        )
        next_index += 1

    core_start = 0.0
    while core_start < duration:
        core_end = min(duration, core_start + chunk_seconds)
        progress(0.81, f"準備雲端轉錄音訊 {core_start / duration:.0%}")
        encode_core(core_start, core_end)
        core_start = core_end

    manifest = {
        "version": 1,
        "strategy": "chunked",
        "model": options.model,
        "duration_seconds": round(duration, 3),
        "max_chunk_mb": max_chunk_mb,
        "chunk_minutes": int(options.endpoint_chunk_minutes),
        "overlap_seconds": overlap_seconds,
        "completed": False,
        "chunks_removed": False,
        "chunks": [chunk.manifest_dict() for chunk in chunks],
    }
    _write_manifest(manifest_path, manifest)
    return chunks, chunk_dir, manifest_path


def update_manifest_chunk(
    manifest_path: Path,
    chunk_index: int,
    status: str,
    attempts: int,
    error: str | None = None,
) -> None:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in payload.get("chunks", []):
        if int(item.get("index", -1)) == chunk_index:
            item["status"] = status
            item["attempts"] = attempts
            if error:
                item["error"] = error[:500]
            else:
                item.pop("error", None)
            break
    _write_manifest(manifest_path, payload)


def finalize_manifest(manifest_path: Path, completed: bool, chunks_removed: bool) -> None:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["completed"] = completed
    payload["chunks_removed"] = chunks_removed
    _write_manifest(manifest_path, payload)


def remove_chunk_files(chunk_dir: Path) -> None:
    if not chunk_dir.exists():
        return
    for path in chunk_dir.iterdir():
        if path.is_file():
            path.unlink()
    try:
        chunk_dir.rmdir()
    except OSError:
        pass


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        raw = response.headers.get("retry-after", "").strip()
        try:
            return max(0.0, min(60.0, float(raw)))
        except ValueError:
            pass
    return min(10.0, float(2**attempt))


def post_transcription_with_retry(
    url: str,
    headers: dict[str, str],
    data: dict[str, str],
    media_path: Path,
    progress,
    label: str,
    max_attempts: int = 3,
) -> tuple[httpx.Response, int]:
    last_exception: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        response: httpx.Response | None = None
        try:
            with media_path.open("rb") as fh:
                mime_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
                files = {"file": (media_path.name, fh, mime_type)}
                response = httpx.post(
                    url,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=TRANSCRIPTION_TIMEOUT,
                    follow_redirects=False,
                )
            if response.status_code not in RETRYABLE_STATUS_CODES or attempt == max_attempts:
                return response, attempt
        except httpx.TransportError as exc:
            last_exception = exc
            if attempt == max_attempts:
                raise RuntimeError(f"Cloud ASR connection failed after {max_attempts} attempts: {exc}") from exc
        delay = _retry_delay(response, attempt)
        progress(0.86, f"{label} 暫時失敗，{delay:g} 秒後重試（{attempt}/{max_attempts}）")
        time.sleep(delay)
    if last_exception:
        raise last_exception
    raise RuntimeError("Cloud ASR request failed without a response.")


def offset_chunk_segments(
    segments: list[TranscriptSegment],
    chunk: AudioChunk,
    fallback_text: str = "",
) -> list[TranscriptSegment]:
    if not segments and fallback_text.strip():
        return [
            TranscriptSegment(
                start=chunk.core_start,
                end=chunk.core_end,
                text=fallback_text.strip(),
            )
        ]

    result: list[TranscriptSegment] = []
    for segment in segments:
        start = chunk.media_start + float(segment.start)
        end = chunk.media_start + float(segment.end)
        midpoint = start + ((end - start) / 2.0)
        is_last_edge = abs(chunk.core_end - chunk.media_end) < 0.01
        if midpoint < chunk.core_start:
            continue
        if midpoint >= chunk.core_end and not (is_last_edge and midpoint <= chunk.core_end + 0.05):
            continue
        result.append(
            TranscriptSegment(
                start=max(0.0, start),
                end=max(start + 0.01, end),
                text=segment.text.strip(),
                speaker=segment.speaker,
            )
        )
    return result


def _normalized_segment_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.lower(), flags=re.UNICODE)


def merge_chunk_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    merged: list[TranscriptSegment] = []
    for segment in sorted(segments, key=lambda item: (item.start, item.end)):
        if not segment.text.strip():
            continue
        if merged:
            previous = merged[-1]
            left = _normalized_segment_text(previous.text)
            right = _normalized_segment_text(segment.text)
            overlap = segment.start <= previous.end + 1.5
            similar = bool(left and right) and (
                left in right
                or right in left
                or SequenceMatcher(None, left, right).ratio() >= 0.88
            )
            if overlap and similar:
                if len(segment.text) > len(previous.text):
                    previous.text = segment.text
                    previous.speaker = segment.speaker
                previous.start = min(previous.start, segment.start)
                previous.end = max(previous.end, segment.end)
                continue
        merged.append(segment)
    return merged
