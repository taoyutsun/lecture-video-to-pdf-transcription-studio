from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from lecture_video_to_pdf.asr import run_openai_compatible
from lecture_video_to_pdf.models import AsrOptions, TranscriptSegment
from lecture_video_to_pdf.pipeline import run_transcription
from lecture_video_to_pdf.remote_asr import (
    AudioChunk,
    merge_chunk_segments,
    post_transcription_with_retry,
    prepare_audio_chunks,
    resolve_upload_strategy,
)
from lecture_video_to_pdf.utils import find_ffmpeg


class FakeJsonResponse:
    def __init__(self, payload: dict, status_code: int = 200, headers: dict[str, str] | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.text = json.dumps(payload, ensure_ascii=False)
        self.reason_phrase = "OK" if status_code < 400 else "Error"
        self.request = httpx.Request("POST", "https://api.example.test/v1/audio/transcriptions")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            response = httpx.Response(
                self.status_code,
                request=self.request,
                json=self._payload,
            )
            raise httpx.HTTPStatusError("request failed", request=self.request, response=response)


class RemoteAsrTests(unittest.TestCase):
    def test_auto_strategy_chunks_large_cloud_media_but_not_local_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "lecture.mp4"
            with media.open("wb") as fh:
                fh.truncate(21 * 1024 * 1024)

            cloud = AsrOptions(
                engine="openai-compatible",
                endpoint_base_url="https://api.groq.com/openai/v1",
                endpoint_upload_strategy="auto",
                endpoint_max_chunk_mb=20,
            )
            local = AsrOptions(
                engine="openai-compatible",
                endpoint_base_url="http://127.0.0.1:11435",
                endpoint_upload_strategy="auto",
                endpoint_max_chunk_mb=20,
            )

            self.assertEqual(resolve_upload_strategy(media, cloud), "chunked")
            self.assertEqual(resolve_upload_strategy(media, local), "direct")

    def test_explicit_strategy_overrides_auto_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "lecture.mp3"
            media.write_bytes(b"audio")
            options = AsrOptions(
                engine="openai-compatible",
                endpoint_base_url="http://127.0.0.1:11435",
                endpoint_upload_strategy="chunked",
            )
            self.assertEqual(resolve_upload_strategy(media, options), "chunked")

    def test_prepare_audio_chunks_uses_real_ffmpeg_and_preserves_overlap(self):
        ffmpeg = find_ffmpeg()
        if ffmpeg is None:
            self.skipTest("FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "sample.wav"
            generated = subprocess.run(
                [
                    str(ffmpeg),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=16000",
                    "-t",
                    "125",
                    "-ac",
                    "1",
                    "-y",
                    str(media),
                ],
                capture_output=True,
                check=False,
            )
            if generated.returncode != 0:
                self.skipTest("The available FFmpeg build does not provide the lavfi test source")

            options = AsrOptions(
                engine="openai-compatible",
                endpoint_upload_strategy="chunked",
                endpoint_max_chunk_mb=5,
                endpoint_chunk_minutes=2,
            )
            chunks, chunk_dir, manifest = prepare_audio_chunks(
                media,
                root / "out",
                options,
                lambda _ratio, _message: None,
                ffmpeg_path=ffmpeg,
            )

            self.assertEqual(len(chunks), 2)
            self.assertEqual(chunks[0].core_start, 0.0)
            self.assertEqual(chunks[0].core_end, 120.0)
            self.assertEqual(chunks[1].media_start, 119.0)
            self.assertTrue(all(chunk.size_bytes <= 5 * 1024 * 1024 for chunk in chunks))
            self.assertTrue(chunk_dir.exists())
            self.assertTrue(manifest.exists())

    def test_chunked_transcription_offsets_timestamps_and_keeps_api_key_out_of_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "lecture.mp4"
            media.write_bytes(b"video")
            output = root / "out"
            output.mkdir()
            chunk_dir = output / ".asr_chunks"
            chunk_dir.mkdir()
            chunk1 = chunk_dir / "chunk_0001.flac"
            chunk2 = chunk_dir / "chunk_0002.flac"
            chunk1.write_bytes(b"one")
            chunk2.write_bytes(b"two")
            chunks = [
                AudioChunk(1, chunk1, 0.0, 10.0, 0.0, 11.0, 3),
                AudioChunk(2, chunk2, 10.0, 20.0, 9.0, 20.0, 3),
            ]
            manifest = output / "transcription_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "completed": False,
                        "chunks_removed": False,
                        "chunks": [chunk.manifest_dict() for chunk in chunks],
                    }
                ),
                encoding="utf-8",
            )
            responses = [
                FakeJsonResponse(
                    {
                        "text": "第一段",
                        "language": "zh",
                        "segments": [{"start": 8.0, "end": 9.5, "text": "第一段"}],
                    }
                ),
                FakeJsonResponse(
                    {
                        "text": "第二段",
                        "language": "zh",
                        "segments": [{"start": 2.0, "end": 4.0, "text": "第二段"}],
                    }
                ),
            ]
            options = AsrOptions(
                engine="openai-compatible",
                endpoint_base_url="https://api.groq.com/openai/v1",
                api_key="test-secret",
                model="whisper-large-v3-turbo",
                language="zh-TW",
                response_format="srt",
                endpoint_upload_strategy="chunked",
            )

            with patch(
                "lecture_video_to_pdf.asr.prepare_audio_chunks",
                return_value=(chunks, chunk_dir, manifest),
            ), patch(
                "lecture_video_to_pdf.asr.post_transcription_with_retry",
                side_effect=[(responses[0], 1), (responses[1], 1)],
            ), patch(
                "lecture_video_to_pdf.asr._opencc_convert",
                side_effect=lambda text, _config: text,
            ):
                result = run_openai_compatible(
                    media,
                    output,
                    options,
                    [],
                    lambda _ratio, _message: None,
                    write_slide_map=False,
                )

            srt = Path(result["transcript_srt_path"]).read_text(encoding="utf-8")
            self.assertIn("00:00:08,000", srt)
            self.assertIn("00:00:11,000", srt)
            self.assertIn("第一段", srt)
            self.assertIn("第二段", srt)
            manifest_text = manifest.read_text(encoding="utf-8")
            self.assertNotIn(options.api_key, manifest_text)
            self.assertTrue(json.loads(manifest_text)["completed"])
            self.assertFalse(chunk_dir.exists())

    def test_transport_failure_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "chunk.flac"
            media.write_bytes(b"audio")
            success = FakeJsonResponse({"text": "ok", "segments": []})
            with patch(
                "lecture_video_to_pdf.remote_asr.httpx.post",
                side_effect=[httpx.RemoteProtocolError("disconnected"), success],
            ) as request, patch("lecture_video_to_pdf.remote_asr.time.sleep") as sleep:
                response, attempts = post_transcription_with_retry(
                    "https://api.example.test/v1/audio/transcriptions",
                    {},
                    {"model": "whisper"},
                    media,
                    lambda _ratio, _message: None,
                    "test chunk",
                )

            self.assertIs(response, success)
            self.assertEqual(attempts, 2)
            self.assertEqual(request.call_count, 2)
            sleep.assert_called_once()

    def test_merge_chunk_segments_removes_overlapping_duplicate(self):
        merged = merge_chunk_segments(
            [
                TranscriptSegment(8.0, 10.5, "這是一段跨越切點的內容"),
                TranscriptSegment(9.5, 12.0, "這是一段跨越切點的內容"),
                TranscriptSegment(12.0, 14.0, "接下來的新內容"),
            ]
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].start, 8.0)
        self.assertEqual(merged[0].end, 12.0)

    def test_transcription_only_propagates_asr_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "lecture.mp3"
            media.write_bytes(b"audio")
            options = AsrOptions(
                engine="openai-compatible",
                endpoint_base_url="https://api.groq.com/openai/v1",
            )
            with patch(
                "lecture_video_to_pdf.pipeline.run_asr_if_requested",
                side_effect=RuntimeError("remote service unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "remote service unavailable"):
                    run_transcription(media, root / "out", options)


if __name__ == "__main__":
    unittest.main()
