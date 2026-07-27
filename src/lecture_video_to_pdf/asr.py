from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from .models import AsrOptions, SlideFrame, TranscriptSegment
from .remote_asr import (
    finalize_manifest,
    merge_chunk_segments,
    offset_chunk_segments,
    post_transcription_with_retry,
    prepare_audio_chunks,
    remove_chunk_files,
    resolve_upload_strategy,
    update_manifest_chunk,
)
from .utils import package_installed, prepare_cuda_runtime

GROQ_RESPONSE_FORMATS = {"json", "verbose_json", "text"}
SRT_MAX_LINES = 2
SRT_MAX_CHARS_PER_LINE = 42
CHINESE_LANGUAGE_VALUES = {"zh", "zh-tw", "zh-cn", "zh-hant", "zh-hant-tw", "zh-hans", "zh-hans-cn", "chinese"}
TRADITIONAL_CHINESE_VALUES = {"zh", "zh-tw", "zh-hant", "zh-hant-tw"}
SIMPLIFIED_CHINESE_VALUES = {"zh-cn", "zh-hans", "zh-hans-cn"}


def _fmt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hh = int(seconds // 3600)
    mm = int((seconds % 3600) // 60)
    ss = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def _normalized_language(value: str | None) -> str:
    return (value or "").strip().lower().replace("_", "-")


def _asr_request_language(value: str | None) -> str | None:
    normalized = _normalized_language(value)
    if not normalized or normalized == "auto":
        return None
    if normalized in CHINESE_LANGUAGE_VALUES:
        return "zh"
    return normalized


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _contains_kana(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff]", text or ""))


def _looks_like_chinese(text: str) -> bool:
    return _contains_cjk(text) and not _contains_kana(text)


def _opencc_convert(text: str, config: str) -> str:
    if not text:
        return text
    try:
        from opencc import OpenCC

        return OpenCC(config).convert(text)
    except Exception:
        return text


def _target_chinese_script(language: str | None, detected_language: str | None, text: str) -> str | None:
    selected = _normalized_language(language)
    detected = _normalized_language(detected_language)
    if selected in SIMPLIFIED_CHINESE_VALUES:
        return "simplified"
    if selected in TRADITIONAL_CHINESE_VALUES:
        return "traditional"
    if selected and selected not in {"auto"}:
        return None
    if detected in CHINESE_LANGUAGE_VALUES or _looks_like_chinese(text):
        return "traditional"
    return None


def _convert_chinese_text(text: str, language: str | None, detected_language: str | None = None) -> str:
    script = _target_chinese_script(language, detected_language, text)
    if script == "traditional":
        return _opencc_convert(text, "s2twp")
    if script == "simplified":
        return _opencc_convert(text, "t2s")
    return text


def _convert_chinese_segments(
    segments: list[TranscriptSegment],
    language: str | None,
    detected_language: str | None = None,
) -> list[TranscriptSegment]:
    if not segments:
        return segments
    joined = " ".join(seg.text for seg in segments)
    script = _target_chinese_script(language, detected_language, joined)
    if not script:
        return segments
    config = "s2twp" if script == "traditional" else "t2s"
    return [
        TranscriptSegment(
            start=seg.start,
            end=seg.end,
            text=_opencc_convert(seg.text, config),
            speaker=seg.speaker,
        )
        for seg in segments
    ]


def _split_long_token(token: str, max_chars: int) -> list[str]:
    if len(token) <= max_chars:
        return [token]
    return [token[i : i + max_chars] for i in range(0, len(token), max_chars)]


def _append_subtitle_line(lines: list[str], token: str, max_chars: int) -> bool:
    if not lines:
        lines.append(token)
        return True
    candidate = f"{lines[-1]} {token}" if lines[-1] else token
    if len(candidate) <= max_chars:
        lines[-1] = candidate
        return True
    return False


def _wrap_words_for_subtitle(text: str, max_chars: int = SRT_MAX_CHARS_PER_LINE, max_lines: int = SRT_MAX_LINES) -> list[str]:
    words: list[str] = []
    for raw in re.split(r"\s+", text.strip()):
        if raw:
            words.extend(_split_long_token(raw, max_chars))

    blocks: list[str] = []
    current_lines: list[str] = []
    for word in words:
        if _append_subtitle_line(current_lines, word, max_chars):
            continue
        if len(current_lines) < max_lines:
            current_lines.append(word)
            continue
        blocks.append("\n".join(current_lines))
        current_lines = [word]
    if current_lines:
        blocks.append("\n".join(current_lines))
    return blocks


def _wrap_cjk_for_subtitle(text: str, max_chars: int = SRT_MAX_CHARS_PER_LINE, max_lines: int = SRT_MAX_LINES) -> list[str]:
    compact = re.sub(r"\s+", "", text.strip())
    cue_chars = max_chars * max_lines
    blocks: list[str] = []
    for start in range(0, len(compact), cue_chars):
        cue = compact[start : start + cue_chars]
        lines = [cue[i : i + max_chars] for i in range(0, len(cue), max_chars)]
        blocks.append("\n".join(lines))
    return blocks


def _subtitle_blocks(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    has_spaces = bool(re.search(r"\s", text))
    return _wrap_words_for_subtitle(text) if has_spaces else _wrap_cjk_for_subtitle(text)


def _block_weight(block: str) -> int:
    return max(1, len(block.replace("\n", "").strip()))


def _segments_to_srt(segments: list[TranscriptSegment]) -> str:
    lines: list[str] = []
    idx = 1
    for seg in segments:
        speaker = f"{seg.speaker}: " if seg.speaker else ""
        blocks = _subtitle_blocks(f"{speaker}{seg.text}")
        if not blocks:
            continue
        total_weight = sum(_block_weight(block) for block in blocks)
        duration = max(0.1, float(seg.end) - float(seg.start))
        cursor = float(seg.start)
        for block_idx, block in enumerate(blocks):
            if block_idx == len(blocks) - 1:
                end = float(seg.end)
            else:
                end = cursor + (duration * (_block_weight(block) / total_weight))
            end = max(end, cursor + 0.1)
            if end > float(seg.end):
                end = float(seg.end)
            if end <= cursor:
                end = cursor + 0.1
            lines.extend(
                [
                    str(idx),
                    f"{_fmt_ts(cursor)} --> {_fmt_ts(end)}",
                    block,
                    "",
                ]
            )
            idx += 1
            cursor = end
    return "\n".join(lines).strip() + "\n"


def _segments_to_plain_srt(segments: list[TranscriptSegment]) -> str:
    lines: list[str] = []
    for idx, seg in enumerate(segments, 1):
        speaker = f"{seg.speaker}: " if seg.speaker else ""
        lines.extend(
            [
                str(idx),
                f"{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}",
                f"{speaker}{seg.text}".strip(),
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _parse_srt_timestamp(value: str) -> float:
    hh, mm, rest = value.strip().split(":")
    ss, ms = rest.split(",")
    return (int(hh) * 3600) + (int(mm) * 60) + int(ss) + (int(ms) / 1000.0)


def _parse_srt_segments(srt_text: str) -> list[TranscriptSegment]:
    parsed: list[TranscriptSegment] = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if time_index < 0:
            continue
        try:
            start_raw, end_raw = [part.strip() for part in lines[time_index].split("-->", 1)]
            text = " ".join(lines[time_index + 1 :]).strip()
            if text:
                parsed.append(
                    TranscriptSegment(
                        start=_parse_srt_timestamp(start_raw),
                        end=_parse_srt_timestamp(end_raw),
                        text=text,
                    )
                )
        except Exception:
            continue
    return parsed


def _compact_srt_text(srt_text: str) -> str:
    parsed = _parse_srt_segments(srt_text)
    return _segments_to_srt(parsed) if parsed else srt_text


def _plain_text(segments: list[TranscriptSegment], fallback: str = "") -> str:
    if segments:
        return "\n".join(seg.text.strip() for seg in segments if seg.text.strip()) + "\n"
    return (fallback.strip() + "\n") if fallback.strip() else ""


def _slide_map(slides: list[SlideFrame], segments: list[TranscriptSegment], fallback_text: str = "") -> str:
    lines = ["# Slide Transcript Map", ""]
    if not slides:
        lines.append(fallback_text.strip())
        return "\n".join(lines).strip() + "\n"
    kept = [s for s in slides if s.kept]
    for idx, slide in enumerate(kept):
        start = slide.time_seconds
        end = kept[idx + 1].time_seconds if idx + 1 < len(kept) else float("inf")
        related = [seg for seg in segments if seg.start >= start and seg.start < end]
        lines.append(f"## Slide {idx + 1} - {start:.2f}s")
        lines.append("")
        if related:
            for seg in related:
                lines.append(f"- [{seg.start:.2f}s-{seg.end:.2f}s] {seg.text.strip()}")
        else:
            lines.append("- No transcript segment mapped to this slide.")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _write_outputs(
    output_dir: Path,
    slides: list[SlideFrame],
    segments: list[TranscriptSegment],
    text: str = "",
    srt_text: str | None = None,
    write_slide_map: bool = True,
) -> dict[str, str]:
    transcript_path = output_dir / "transcript.txt"
    srt_path = output_dir / "transcript.srt"
    slide_map_path = output_dir / "slide_map.md"

    transcript_path.write_text(_plain_text(segments, text), encoding="utf-8")
    if srt_text is None:
        srt_text = _segments_to_srt(segments) if segments else ""
    else:
        srt_text = _compact_srt_text(srt_text)
    srt_path.write_text(srt_text, encoding="utf-8")
    outputs = {
        "transcript_path": str(transcript_path),
        "transcript_srt_path": str(srt_path),
    }
    if write_slide_map:
        slide_map_path.write_text(_slide_map(slides, segments, text), encoding="utf-8")
        outputs["slide_map_path"] = str(slide_map_path)
    return outputs


def _parse_openai_segments(payload: dict[str, Any]) -> list[TranscriptSegment]:
    parsed: list[TranscriptSegment] = []
    for seg in payload.get("segments", []) or []:
        try:
            parsed.append(
                TranscriptSegment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", seg.get("start", 0.0))),
                    text=str(seg.get("text", "")).strip(),
                    speaker=seg.get("speaker"),
                )
            )
        except Exception:
            continue
    return parsed


def _transcription_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1/audio/transcriptions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/audio/transcriptions"
    return f"{base}/v1/audio/transcriptions"


def _is_groq_endpoint(base_url: str) -> bool:
    return "api.groq.com" in base_url.lower()


def _request_response_format(options: AsrOptions) -> str:
    requested = options.response_format or "verbose_json"
    if _is_groq_endpoint(options.endpoint_base_url) and requested not in GROQ_RESPONSE_FORMATS:
        return "verbose_json"
    return requested


def _openai_transcription_data(options: AsrOptions) -> dict[str, str]:
    data = {
        "model": options.model or "default",
        "response_format": _request_response_format(options),
    }
    language = _asr_request_language(options.language)
    if language:
        data["language"] = language
    return data


def _response_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"]).replace("\n", " ")[:800]
            if payload.get("message"):
                return str(payload["message"]).replace("\n", " ")[:800]
    except Exception:
        pass
    return response.text[:800].replace("\n", " ")


def _filter_asr_models(model_ids: list[str]) -> list[str]:
    markers = ("whisper", "asr", "transcrib", "speech")
    filtered = [model for model in model_ids if any(marker in model.lower() for marker in markers)]
    return filtered or model_ids


def _is_cuda_runtime_error(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = ("cuda", "cublas", "cudnn", "cufft", "cudart")
    return any(marker in message for marker in markers)


def _short_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:240]


def _resolve_faster_whisper_runtime(options: AsrOptions) -> tuple[str, str]:
    device = options.device
    if device == "auto":
        diagnostics = prepare_cuda_runtime()
        device = "cuda" if diagnostics.get("gpu_available") and diagnostics.get("runtime_ready") else "cpu"
    compute_type = options.compute_type
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


def _run_faster_whisper_once(
    video_path: Path,
    output_dir: Path,
    options: AsrOptions,
    slides: list[SlideFrame],
    progress,
    device: str,
    compute_type: str,
    write_slide_map: bool = True,
) -> dict:
    from faster_whisper import WhisperModel

    progress(0.82, f"載入 faster-whisper 模型 {options.model} ({device}/{compute_type})")
    model = WhisperModel(options.model, device=device, compute_type=compute_type)
    progress(0.86, "轉錄音訊")
    raw_segments, info = model.transcribe(
        str(video_path),
        language=_asr_request_language(options.language),
        vad_filter=True,
        word_timestamps=False,
    )
    segments = [
        TranscriptSegment(start=float(seg.start), end=float(seg.end), text=str(seg.text).strip())
        for seg in raw_segments
        if str(seg.text).strip()
    ]
    detected_language = getattr(info, "language", None)
    segments = _convert_chinese_segments(segments, options.language, detected_language)
    outputs = _write_outputs(output_dir, slides, segments, write_slide_map=write_slide_map)
    return {**outputs, "warnings": []}


def run_faster_whisper(
    video_path: Path,
    output_dir: Path,
    options: AsrOptions,
    slides: list[SlideFrame],
    progress,
    write_slide_map: bool = True,
) -> dict:
    if not package_installed("faster_whisper"):
        raise RuntimeError(
            "faster-whisper is not installed. In the Web UI, choose faster-whisper and install optional ASR dependencies, or run: pip install -r requirements-asr.txt"
        )

    device, compute_type = _resolve_faster_whisper_runtime(options)
    try:
        if device == "cuda":
            diagnostics = prepare_cuda_runtime()
            if not diagnostics.get("runtime_ready"):
                missing = ", ".join(diagnostics.get("missing_runtime_dlls", []))
                progress(0.81, f"CUDA GPU 已偵測，但 runtime 尚未完整載入：{missing}")
        return _run_faster_whisper_once(
            video_path,
            output_dir,
            options,
            slides,
            progress,
            device,
            compute_type,
            write_slide_map=write_slide_map,
        )
    except Exception as exc:
        if device == "cuda" and _is_cuda_runtime_error(exc):
            progress(0.82, "CUDA 載入失敗，改用 CPU/int8 轉錄")
            outputs = _run_faster_whisper_once(
                video_path,
                output_dir,
                options,
                slides,
                progress,
                "cpu",
                "int8",
                write_slide_map=write_slide_map,
            )
            warning = f"ASR CUDA 載入失敗，已自動改用 CPU/int8。原設定：device={options.device}, compute_type={options.compute_type}。原因：{_short_error(exc)}"
            return {**outputs, "warnings": [warning]}
        raise


def _raise_for_openai_response(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = _response_error_detail(response)
        raise RuntimeError(f"{exc.response.status_code} {exc.response.reason_phrase}: {detail}") from exc


def _parse_openai_response(
    response: httpx.Response,
    options: AsrOptions,
) -> tuple[list[TranscriptSegment], str, str | None]:
    content_type = response.headers.get("content-type", "")
    text = response.text
    segments: list[TranscriptSegment] = []
    srt_text = None
    if "application/json" in content_type:
        payload = response.json()
        text = str(payload.get("text", "")).strip()
        segments = _parse_openai_segments(payload)
        detected_language = payload.get("language")
        detected = str(detected_language) if detected_language else None
        segments = _convert_chinese_segments(segments, options.language, detected)
        text = _convert_chinese_text(text, options.language, detected)
    elif options.response_format == "srt" or "-->" in text:
        srt_text = _convert_chinese_text(text, options.language)
        segments = _parse_srt_segments(srt_text)
    return segments, text, srt_text


def _run_openai_direct(
    video_path: Path,
    output_dir: Path,
    options: AsrOptions,
    slides: list[SlideFrame],
    progress,
    write_slide_map: bool,
) -> dict:
    url = _transcription_url(options.endpoint_base_url)
    headers = {"Authorization": f"Bearer {options.api_key}"} if options.api_key else {}
    data = _openai_transcription_data(options)
    progress(0.84, "呼叫 OpenAI-compatible ASR endpoint")
    response, _attempts = post_transcription_with_retry(
        url,
        headers,
        data,
        video_path,
        progress,
        "雲端轉錄",
    )
    _raise_for_openai_response(response)
    segments, text, srt_text = _parse_openai_response(response, options)
    outputs = _write_outputs(
        output_dir,
        slides,
        segments,
        text=text,
        srt_text=srt_text,
        write_slide_map=write_slide_map,
    )
    if options.response_format == "srt" and data["response_format"] != "srt" and not srt_text:
        outputs["warnings"] = ["Endpoint does not support direct SRT; generated transcript.srt locally from returned segments."]
        return outputs
    return {**outputs, "warnings": []}


def _run_openai_chunked(
    video_path: Path,
    output_dir: Path,
    options: AsrOptions,
    slides: list[SlideFrame],
    progress,
    write_slide_map: bool,
) -> dict:
    url = _transcription_url(options.endpoint_base_url)
    headers = {"Authorization": f"Bearer {options.api_key}"} if options.api_key else {}
    data = _openai_transcription_data(options)
    data["response_format"] = "verbose_json"
    chunks, chunk_dir, manifest_path = prepare_audio_chunks(video_path, output_dir, options, progress)
    collected: list[TranscriptSegment] = []
    warnings: list[str] = []

    try:
        for position, chunk in enumerate(chunks, 1):
            ratio = 0.84 + (position / max(1, len(chunks))) * 0.12
            progress(ratio, f"雲端轉錄第 {position}/{len(chunks)} 段")
            attempts = 0
            try:
                response, attempts = post_transcription_with_retry(
                    url,
                    headers,
                    data,
                    chunk.path,
                    progress,
                    f"第 {position}/{len(chunks)} 段",
                )
                _raise_for_openai_response(response)
                segments, text, srt_text = _parse_openai_response(response, options)
                if not segments and srt_text:
                    segments = _parse_srt_segments(srt_text)
                collected.extend(offset_chunk_segments(segments, chunk, fallback_text=text))
                update_manifest_chunk(manifest_path, chunk.index, "completed", attempts)
            except Exception as exc:
                update_manifest_chunk(manifest_path, chunk.index, "failed", attempts, str(exc))
                raise

        merged = merge_chunk_segments(collected)
        outputs = _write_outputs(
            output_dir,
            slides,
            merged,
            write_slide_map=write_slide_map,
        )
        outputs["transcription_manifest_path"] = str(manifest_path)
        if not merged:
            warnings.append("Cloud ASR returned no transcript segments.")
        finalize_manifest(manifest_path, completed=True, chunks_removed=False)
        remove_chunk_files(chunk_dir)
        finalize_manifest(manifest_path, completed=True, chunks_removed=not chunk_dir.exists())
        return {**outputs, "warnings": warnings}
    except Exception:
        finalize_manifest(manifest_path, completed=False, chunks_removed=False)
        raise


def run_openai_compatible(
    video_path: Path,
    output_dir: Path,
    options: AsrOptions,
    slides: list[SlideFrame],
    progress,
    write_slide_map: bool = True,
) -> dict:
    if not options.endpoint_base_url:
        raise ValueError("OpenAI-compatible ASR endpoint is empty.")
    strategy = resolve_upload_strategy(video_path, options)
    if strategy == "chunked":
        return _run_openai_chunked(video_path, output_dir, options, slides, progress, write_slide_map)
    return _run_openai_direct(video_path, output_dir, options, slides, progress, write_slide_map)


def run_asr_if_requested(
    video_path: str | Path,
    output_dir: str | Path,
    options: AsrOptions,
    slides: list[SlideFrame],
    progress,
    write_slide_map: bool = True,
) -> dict:
    video = Path(video_path)
    output = Path(output_dir)
    if options.engine == "faster-whisper":
        return run_faster_whisper(video, output, options, slides, progress, write_slide_map=write_slide_map)
    if options.engine == "openai-compatible":
        return run_openai_compatible(video, output, options, slides, progress, write_slide_map=write_slide_map)
    return {"warnings": []}


def test_openai_compatible_endpoint(base_url: str, api_key: str = "") -> dict[str, Any]:
    if not base_url:
        return {"ok": False, "message": "base_url is empty"}
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    candidates = [f"{base}/health"]
    if base.endswith("/v1"):
        candidates.append(f"{base}/models")
    else:
        candidates.append(f"{base}/v1/models")
        candidates.append(f"{base}/models")
    errors = []
    first_success: dict[str, Any] | None = None
    try:
        for url in candidates:
            response = httpx.get(url, headers=headers, timeout=8.0)
            if response.status_code < 400:
                body = response.text[:500]
                names: list[str] = []
                try:
                    payload = response.json()
                    if "data" in payload and isinstance(payload["data"], list):
                        names = [str(item.get("id", "")) for item in payload["data"][:50] if item.get("id")]
                        names = _filter_asr_models(names)
                        body = "models: " + ", ".join(names) if names else json.dumps(payload, ensure_ascii=False)
                    elif payload.get("status") == "ok" and "model_ready" in payload:
                        body = json.dumps(payload, ensure_ascii=False)
                        result = {
                            "ok": True,
                            "message": f"{url} -> {body}",
                            "models": ["default"],
                            "server_managed_model": True,
                        }
                        return result
                    else:
                        body = json.dumps(payload, ensure_ascii=False)
                except Exception:
                    pass
                result = {"ok": True, "message": f"{url} -> {body}", "models": names, "server_managed_model": False}
                if names:
                    return result
                if first_success is None:
                    first_success = result
            errors.append(f"{url} -> HTTP {response.status_code}: {response.text[:180]}")
        if first_success is not None:
            return first_success
        return {"ok": False, "message": " / ".join(errors), "models": []}
    except Exception as exc:
        return {"ok": False, "message": str(exc), "models": []}
