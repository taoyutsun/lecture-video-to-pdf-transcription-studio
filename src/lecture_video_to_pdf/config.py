from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class FasterWhisperConfig:
    model: str = "base"
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = ""


@dataclass
class OpenAICompatibleConfig:
    base_url: str = "http://127.0.0.1:11435"
    api_key: str = ""
    model: str = ""
    language: str | None = ""
    response_format: str = "verbose_json"


@dataclass
class AsrConfig:
    engine: str = "none"
    faster_whisper: FasterWhisperConfig = field(default_factory=FasterWhisperConfig)
    openai_compatible: OpenAICompatibleConfig = field(default_factory=OpenAICompatibleConfig)


@dataclass
class AppConfig:
    output_dir: str = "output"
    ffmpeg_path: str | None = None
    default_mode: str = "balanced"
    default_crop: str = "auto"
    sample_fps: dict[str, float] = field(
        default_factory=lambda: {"fast": 1.0, "balanced": 2.0, "sensitive": 3.0}
    )
    asr: AsrConfig = field(default_factory=AsrConfig)


def _merge_config(default: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(default)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def _to_plain(config: AppConfig) -> dict[str, Any]:
    return {
        "output_dir": config.output_dir,
        "ffmpeg_path": config.ffmpeg_path,
        "default_mode": config.default_mode,
        "default_crop": config.default_crop,
        "sample_fps": config.sample_fps,
        "asr": {
            "engine": config.asr.engine,
            "faster_whisper": vars(config.asr.faster_whisper),
            "openai_compatible": vars(config.asr.openai_compatible),
        },
    }


def _from_plain(data: dict[str, Any]) -> AppConfig:
    asr_data = data.get("asr", {}) or {}
    fw_data = asr_data.get("faster_whisper", {}) or {}
    oa_data = asr_data.get("openai_compatible", {}) or {}
    return AppConfig(
        output_dir=data.get("output_dir", "output"),
        ffmpeg_path=data.get("ffmpeg_path"),
        default_mode=data.get("default_mode", "balanced"),
        default_crop=data.get("default_crop", "auto"),
        sample_fps=data.get("sample_fps") or {"fast": 1.0, "balanced": 2.0, "sensitive": 3.0},
        asr=AsrConfig(
            engine=asr_data.get("engine", "none"),
            faster_whisper=FasterWhisperConfig(**fw_data),
            openai_compatible=OpenAICompatibleConfig(**oa_data),
        ),
    )


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path or "config.yaml")
    default = _to_plain(AppConfig())
    if not config_path.exists():
        return AppConfig()
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return _from_plain(_merge_config(default, loaded))


def save_config(config: AppConfig, path: str | Path = "config.yaml") -> None:
    config_path = Path(path)
    config_path.write_text(
        yaml.safe_dump(_to_plain(config), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
