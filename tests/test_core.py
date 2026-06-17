from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from lecture_video_to_pdf.models import ConversionOptions
from lecture_video_to_pdf.asr import (
    _asr_request_language,
    _compact_srt_text,
    _convert_chinese_text,
    _filter_asr_models,
    _is_cuda_runtime_error,
    _openai_transcription_data,
    _request_response_format,
    test_openai_compatible_endpoint,
    _segments_to_srt,
    run_faster_whisper,
)
from lecture_video_to_pdf.installer import cuda_runtime_install_command, faster_whisper_install_command
from lecture_video_to_pdf.models import AsrOptions, TranscriptSegment
from lecture_video_to_pdf.pipeline import run_conversion, run_media_job, run_transcription
from lecture_video_to_pdf.video import detect_candidate_frames, difference_hash, hamming_distance, probe_video
from lecture_video_to_pdf import cli


def make_synthetic_video(path: Path) -> None:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, 10.0, (640, 360))
    if not writer.isOpened():
        raise RuntimeError("Unable to create synthetic video")
    try:
        colors = [(245, 245, 245), (230, 240, 255), (235, 250, 235)]
        labels = ["Slide 1", "Slide 2", "Slide 3"]
        for color, label in zip(colors, labels):
            for i in range(18):
                frame = np.full((360, 640, 3), color, dtype=np.uint8)
                cv2.rectangle(frame, (45, 40), (595, 320), (60, 80, 110), 2)
                cv2.putText(frame, label, (120, 170), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (30, 40, 70), 4)
                if i > 9:
                    cv2.circle(frame, (500, 95), 18, (40, 130, 90), -1)
                writer.write(frame)
    finally:
        writer.release()


def make_black_intro_video(path: Path) -> None:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, 10.0, (640, 360))
    if not writer.isOpened():
        raise RuntimeError("Unable to create synthetic video")
    try:
        for _ in range(5):
            writer.write(np.zeros((360, 640, 3), dtype=np.uint8))
        for _ in range(25):
            frame = np.full((360, 640, 3), 245, dtype=np.uint8)
            cv2.putText(frame, "Cover Slide", (110, 170), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (20, 30, 60), 4)
            writer.write(frame)
        for _ in range(25):
            frame = np.full((360, 640, 3), 250, dtype=np.uint8)
            cv2.putText(frame, "Agenda", (170, 170), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (20, 30, 60), 4)
            writer.write(frame)
    finally:
        writer.release()


class CoreTests(unittest.TestCase):
    def test_cli_without_args_starts_web_ui(self):
        with patch("lecture_video_to_pdf.cli.run_server") as run_server:
            self.assertEqual(cli.main([]), 0)
        run_server.assert_called_once_with("127.0.0.1", 8787, True)

    def test_difference_hash_distance(self):
        a = np.zeros((32, 32), dtype=np.uint8)
        b = np.ones((32, 32), dtype=np.uint8) * 255
        self.assertEqual(hamming_distance(difference_hash(a), difference_hash(a)), 0)
        self.assertGreaterEqual(hamming_distance(difference_hash(a), difference_hash(b)), 0)

    def test_synthetic_conversion_outputs_pdf_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video = tmp_path / "測試 lecture.mp4"
            make_synthetic_video(video)
            result = run_conversion(
                video,
                tmp_path / "out",
                ConversionOptions(mode="sensitive", crop="auto"),
            )
            self.assertTrue(Path(result.pdf_path).exists())
            self.assertTrue(Path(result.metadata_path).exists())
            self.assertGreaterEqual(len([s for s in result.slides if s.kept]), 2)

    def test_transcription_only_does_not_create_pdf_or_slide_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "lecture audio.mp3"
            audio.write_bytes(b"fake audio")

            def fake_asr(_media, output, _options, _slides, _progress, write_slide_map=True):
                output = Path(output)
                transcript = output / "transcript.txt"
                srt = output / "transcript.srt"
                transcript.write_text("hello\n", encoding="utf-8")
                srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
                result = {"transcript_path": str(transcript), "transcript_srt_path": str(srt), "warnings": []}
                if write_slide_map:
                    slide_map = output / "slide_map.md"
                    slide_map.write_text("# map\n", encoding="utf-8")
                    result["slide_map_path"] = str(slide_map)
                return result

            with patch("lecture_video_to_pdf.pipeline.run_asr_if_requested", side_effect=fake_asr):
                result = run_transcription(
                    audio,
                    tmp_path / "out",
                    AsrOptions(engine="openai-compatible", endpoint_base_url="http://127.0.0.1:11435"),
                )

            self.assertEqual(result["task"], "transcription")
            self.assertIsNone(result["pdf_path"])
            self.assertEqual(result["slides"], [])
            self.assertIsNone(result["slide_map_path"])
            self.assertTrue(Path(result["transcript_path"]).exists())
            self.assertTrue(Path(result["transcript_srt_path"]).exists())
            self.assertTrue(Path(result["metadata_path"]).exists())

    def test_audio_file_is_rejected_for_pdf_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "lecture audio.mp3"
            audio.write_bytes(b"fake audio")
            with self.assertRaisesRegex(ValueError, "PDF extraction requires a video file"):
                run_media_job(audio, tmp_path / "out", ConversionOptions(), task="slides")

    def test_pdf_plus_transcription_keeps_slide_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video = tmp_path / "lecture.mp4"
            make_synthetic_video(video)

            def fake_asr(_media, output, _options, _slides, _progress, write_slide_map=True):
                output = Path(output)
                transcript = output / "transcript.txt"
                srt = output / "transcript.srt"
                slide_map = output / "slide_map.md"
                transcript.write_text("hello\n", encoding="utf-8")
                srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
                if write_slide_map:
                    slide_map.write_text("# map\n", encoding="utf-8")
                return {
                    "transcript_path": str(transcript),
                    "transcript_srt_path": str(srt),
                    "slide_map_path": str(slide_map) if write_slide_map else None,
                    "warnings": [],
                }

            options = ConversionOptions(mode="sensitive", asr=AsrOptions(engine="openai-compatible", endpoint_base_url="http://127.0.0.1:11435"))
            with patch("lecture_video_to_pdf.pipeline.run_asr_if_requested", side_effect=fake_asr):
                result = run_media_job(video, tmp_path / "out", options, task="slides_and_transcription")

            self.assertEqual(result["task"], "slides_and_transcription")
            self.assertTrue(Path(result["pdf_path"]).exists())
            self.assertTrue(Path(result["transcript_path"]).exists())
            self.assertTrue(Path(result["slide_map_path"]).exists())

    def test_black_intro_does_not_replace_cover_slide(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video = tmp_path / "black intro.mp4"
            make_black_intro_video(video)
            info = probe_video(video)
            candidates = detect_candidate_frames(video, info, ConversionOptions(mode="balanced", crop="auto"))
            self.assertGreater(candidates[0].time_seconds, 0.0)
            self.assertLess(candidates[0].time_seconds, 1.2)

    def test_groq_srt_request_is_sent_as_verbose_json(self):
        options = AsrOptions(
            engine="openai-compatible",
            endpoint_base_url="https://api.groq.com/openai/v1",
            response_format="srt",
        )
        self.assertEqual(_request_response_format(options), "verbose_json")

    def test_openai_transcription_omits_empty_language_for_auto_detect(self):
        options = AsrOptions(
            engine="openai-compatible",
            endpoint_base_url="https://api.groq.com/openai/v1",
            model="whisper-large-v3-turbo",
            language=None,
            response_format="srt",
        )
        data = _openai_transcription_data(options)
        self.assertNotIn("language", data)
        self.assertEqual(data["response_format"], "verbose_json")

    def test_openai_transcription_sends_selected_language(self):
        options = AsrOptions(
            engine="openai-compatible",
            endpoint_base_url="https://api.groq.com/openai/v1",
            model="whisper-large-v3-turbo",
            language="zh-TW",
            response_format="srt",
        )
        data = _openai_transcription_data(options)
        self.assertEqual(data["language"], "zh")

    def test_chinese_language_variants_are_sent_as_zh(self):
        self.assertEqual(_asr_request_language("zh-TW"), "zh")
        self.assertEqual(_asr_request_language("zh-CN"), "zh")
        self.assertIsNone(_asr_request_language(""))

    def test_auto_detected_chinese_prefers_traditional_conversion(self):
        with patch("lecture_video_to_pdf.asr._opencc_convert", side_effect=lambda text, config: f"{config}:{text}"):
            self.assertEqual(_convert_chinese_text("软件发票", "", "zh"), "s2twp:软件发票")

    def test_selected_simplified_chinese_uses_simplified_conversion(self):
        with patch("lecture_video_to_pdf.asr._opencc_convert", side_effect=lambda text, config: f"{config}:{text}"):
            self.assertEqual(_convert_chinese_text("軟體發票", "zh-CN", "zh"), "t2s:軟體發票")

    def test_srt_output_splits_long_segments_for_readability(self):
        text = (
            "Detecting breast cancer at an early phase will result in better treatment "
            "and machine learning techniques can help reduce the cost of medication."
        )
        srt = _segments_to_srt([TranscriptSegment(start=0.0, end=12.0, text=text)])
        cues = [cue for cue in srt.strip().split("\n\n") if cue.strip()]
        self.assertGreater(len(cues), 1)
        for cue in cues:
            payload_lines = cue.splitlines()[2:]
            self.assertLessEqual(len(payload_lines), 2)
            self.assertTrue(all(len(line) <= 42 for line in payload_lines))

    def test_direct_srt_is_compacted_for_readability(self):
        source = (
            "1\n"
            "00:00:00,000 --> 00:00:12,000\n"
            "Detecting breast cancer at an early phase will result in better treatment and machine learning techniques can help reduce the cost of medication.\n"
        )
        compacted = _compact_srt_text(source)
        cues = [cue for cue in compacted.strip().split("\n\n") if cue.strip()]
        self.assertGreater(len(cues), 1)
        for cue in cues:
            self.assertLessEqual(len(cue.splitlines()[2:]), 2)

    def test_model_filter_prefers_asr_models(self):
        models = ["llama-3.3-70b-versatile", "whisper-large-v3-turbo", "qwen/qwen3-32b"]
        self.assertEqual(_filter_asr_models(models), ["whisper-large-v3-turbo"])

    def test_qwen_health_marks_server_managed_model(self):
        class FakeResponse:
            status_code = 200
            text = '{"status":"ok","model_ready":true}'

            def json(self):
                return {"status": "ok", "model_ready": True}

        with patch("lecture_video_to_pdf.asr.httpx.get", return_value=FakeResponse()):
            result = test_openai_compatible_endpoint("http://127.0.0.1:11435")

        self.assertTrue(result["ok"])
        self.assertTrue(result["server_managed_model"])
        self.assertEqual(result["models"], ["default"])

    def test_faster_whisper_install_command_uses_requirements_file(self):
        command, requirements = faster_whisper_install_command()
        self.assertIn("-m", command)
        self.assertIn("pip", command)
        self.assertIsNotNone(requirements)
        self.assertTrue(requirements.exists())
        self.assertIn(str(requirements), command)

    def test_cuda_runtime_install_command_uses_requirements_file(self):
        command, requirements = cuda_runtime_install_command()
        self.assertIn("-m", command)
        self.assertIn("pip", command)
        self.assertIsNotNone(requirements)
        self.assertTrue(requirements.exists())
        self.assertIn(str(requirements), command)

    def test_cuda_runtime_error_detection(self):
        self.assertTrue(_is_cuda_runtime_error(RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")))
        self.assertFalse(_is_cuda_runtime_error(RuntimeError("model file not found")))

    def test_faster_whisper_auto_falls_back_to_cpu_on_cuda_runtime_error(self):
        calls = []

        def fake_run_once(_video, _output, _options, _slides, _progress, device, compute_type, **_kwargs):
            calls.append((device, compute_type))
            if device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return {"transcript_path": "transcript.txt", "transcript_srt_path": "transcript.srt", "slide_map_path": "slide_map.md", "warnings": []}

        with patch("lecture_video_to_pdf.asr.package_installed", return_value=True), patch(
            "lecture_video_to_pdf.asr.detect_cuda_available", return_value=True
        ), patch("lecture_video_to_pdf.asr._run_faster_whisper_once", side_effect=fake_run_once):
            result = run_faster_whisper(
                Path("lecture.mp4"),
                Path("."),
                AsrOptions(engine="faster-whisper", device="auto", compute_type="auto"),
                [],
                lambda _ratio, _message: None,
            )

        self.assertEqual(calls, [("cuda", "float16"), ("cpu", "int8")])
        self.assertIn("CPU/int8", result["warnings"][0])

    def test_faster_whisper_explicit_cuda_falls_back_to_cpu_on_cuda_runtime_error(self):
        calls = []

        def fake_run_once(_video, _output, _options, _slides, _progress, device, compute_type, **_kwargs):
            calls.append((device, compute_type))
            if device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return {"transcript_path": "transcript.txt", "transcript_srt_path": "transcript.srt", "slide_map_path": "slide_map.md", "warnings": []}

        with patch("lecture_video_to_pdf.asr.package_installed", return_value=True), patch(
            "lecture_video_to_pdf.asr._run_faster_whisper_once", side_effect=fake_run_once
        ):
            result = run_faster_whisper(
                Path("lecture.mp4"),
                Path("."),
                AsrOptions(engine="faster-whisper", device="cuda", compute_type="auto"),
                [],
                lambda _ratio, _message: None,
            )

        self.assertEqual(calls, [("cuda", "float16"), ("cpu", "int8")])
        self.assertIn("device=cuda", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
