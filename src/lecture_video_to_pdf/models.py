from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

Mode = Literal["fast", "balanced", "sensitive"]
AsrEngine = Literal["none", "faster-whisper", "openai-compatible"]
EndpointUploadStrategy = Literal["auto", "direct", "chunked"]
TaskType = Literal["slides", "transcription", "slides_and_transcription"]


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float


@dataclass
class SlideFrame:
    index: int
    time_seconds: float
    frame_number: int
    path: str
    thumb_path: str
    diff_score: float
    hash_value: str
    duplicate_of: int | None = None
    kept: bool = True


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker: str | None = None


@dataclass
class AsrOptions:
    engine: AsrEngine = "none"
    model: str = "base"
    language: str | None = ""
    device: str = "auto"
    compute_type: str = "auto"
    endpoint_base_url: str = ""
    api_key: str = ""
    response_format: str = "verbose_json"
    endpoint_upload_strategy: EndpointUploadStrategy = "auto"
    endpoint_max_chunk_mb: int = 20
    endpoint_chunk_minutes: int = 10


@dataclass
class ConversionOptions:
    mode: Mode = "balanced"
    crop: str = "auto"
    asr: AsrOptions = field(default_factory=AsrOptions)
    sample_fps: float | None = None
    duplicate_ssim_threshold: float | None = None
    duplicate_hash_threshold: int | None = None


@dataclass
class ConversionResult:
    video: VideoInfo
    output_dir: str
    pdf_path: str
    slides_dir: str
    thumbs_dir: str
    metadata_path: str
    slides: list[SlideFrame]
    transcript_path: str | None = None
    transcript_srt_path: str | None = None
    transcription_manifest_path: str | None = None
    slide_map_path: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["task"] = "slides_and_transcription" if self.transcript_path else "slides"
        return data


ProgressCallback = Callable[[float, str], None]


def path_dict(path: Path) -> dict[str, str]:
    return {"path": str(path), "name": path.name}
