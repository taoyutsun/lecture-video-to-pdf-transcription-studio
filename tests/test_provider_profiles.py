from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from lecture_video_to_pdf.api import create_app
from lecture_video_to_pdf.asr import _filter_asr_models, run_openai_compatible, test_openai_compatible_endpoint
from lecture_video_to_pdf.credentials import CredentialError, WindowsCredentialStore
from lecture_video_to_pdf.jobs import Job, JobManager
from lecture_video_to_pdf.models import AsrOptions, ConversionOptions
from lecture_video_to_pdf.providers import ProviderProfiles, default_settings, normalize_endpoint, transcription_models
from lecture_video_to_pdf.remote_asr import AudioChunk


class FakeVault:
    available = True

    def __init__(self):
        self.values = {}

    def get(self, account):
        return self.values.get(account)

    def set(self, account, value):
        self.values[account] = value

    def delete(self, account):
        self.values.pop(account, None)


class ProfileFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = FakeVault()
        self.store = ProviderProfiles(self.root / "preferences.json", self.vault)


class ProfileTests(ProfileFixture, unittest.TestCase):
    def test_key_never_enters_preferences_or_public_payload(self):
        settings = default_settings("groq")
        settings["api_key"] = "fixture-secret"
        saved = self.store.save(settings, "fixture-secret", True)
        self.assertTrue(saved["has_key"])
        self.assertNotIn("fixture-secret", json.dumps(self.store.snapshot()))
        self.assertNotIn("fixture-secret", self.store.path.read_text(encoding="utf-8"))
        self.assertNotIn("api_key", self.store.path.read_text(encoding="utf-8"))

    def test_reopening_store_remembers_preferences_and_resolves_key(self):
        self.store.save(default_settings("groq"), "fixture-secret", True)
        reopened = ProviderProfiles(self.store.path, self.vault)
        self.assertEqual(reopened.snapshot()["active_profile"], "groq")
        profile, key = reopened.resolve("groq", "https://api.groq.com/openai/v1/", use_saved_key=True)
        self.assertEqual(profile["model"], "whisper-large-v3-turbo")
        self.assertEqual(key, "fixture-secret")

    def test_different_endpoint_and_provider_cannot_reuse_key(self):
        self.store.save(default_settings("groq"), "fixture-secret", True)
        with self.assertRaises(ValueError):
            self.store.resolve("groq", "https://other.example/v1", use_saved_key=True)
        with self.assertRaises(ValueError):
            self.store.resolve("openai", "https://api.openai.com/v1", use_saved_key=True)

    def test_custom_endpoint_change_removes_old_key(self):
        settings = {**default_settings("custom"), "base_url": "https://one.example/v1", "model": "asr-model"}
        saved = self.store.save(settings, "fixture-secret", True)
        self.assertTrue(saved["has_key"])
        saved["base_url"] = "https://two.example/v1"
        updated = self.store.save(saved)
        self.assertFalse(updated["has_key"])
        self.assertEqual(self.vault.values, {})

    def test_delete_key_keeps_nonsecret_preferences(self):
        self.store.save(default_settings("groq"), "fixture-secret", True)
        self.assertFalse(self.store.delete_key("groq")["has_key"])
        self.assertEqual(self.store.snapshot()["active_profile"], "groq")

    def test_empty_save_does_not_overwrite_key(self):
        self.store.save(default_settings("groq"), "fixture-secret", True)
        with self.assertRaises(ValueError):
            self.store.save(default_settings("groq"), "", True)
        self.assertEqual(next(iter(self.vault.values.values())), "fixture-secret")

    def test_vault_failure_never_falls_back_to_plaintext(self):
        with patch.object(self.vault, "set", side_effect=CredentialError("Vault unavailable")):
            with self.assertRaises(CredentialError):
                self.store.save(default_settings("groq"), "fixture-secret", True)
        self.assertFalse(self.store.path.exists())

    def test_unsupported_platform_keeps_temporary_usage(self):
        self.vault.available = False
        saved = self.store.save(default_settings("groq"))
        self.assertFalse(saved["has_key"])
        self.assertEqual(self.store.resolve("groq", saved["base_url"], "temporary-key")[1], "temporary-key")

    def test_corrupt_preferences_are_preserved(self):
        self.store.path.write_text("invalid-json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.save(default_settings("groq"))
        self.assertEqual(self.store.path.read_text(), "invalid-json")

    def test_endpoint_normalization_and_transport_rules(self):
        self.assertEqual(normalize_endpoint("HTTPS://API.GROQ.COM:443/openai/v1/"), "https://api.groq.com/openai/v1")
        self.assertEqual(normalize_endpoint("http://[::1]:11435/"), "http://[::1]:11435")
        for value in ["http://cloud.example/v1", "https://user:key@example.com", "https://example.com?k=secret", "https://example.com/#x", "file:///secret", "https://example.com:bad"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_endpoint(value)

    def test_builtin_urls_cannot_be_overridden(self):
        with self.assertRaises(ValueError):
            self.store.save({**default_settings("groq"), "base_url": "https://other.example"})

    def test_models_exclude_llm_and_tts_and_include_transcription(self):
        names = ["llama-3", "gpt-4o-transcribe", "speech-tts", "whisper-large-v3-turbo"]
        self.assertEqual(transcription_models("groq", names), ["whisper-large-v3-turbo"])
        self.assertEqual(transcription_models("openai", names), ["gpt-4o-transcribe"])
        self.assertEqual(_filter_asr_models(["llama-3", "speech-tts"]), [])

    def test_model_cache_is_bound_to_endpoint(self):
        self.store.save(default_settings("groq"))
        names = self.store.update_models("groq", ["llama-3", "whisper-large-v3"], "2026-10-02", "https://api.groq.com/openai/v1")
        self.assertEqual(names, ["whisper-large-v3"])
        with self.assertRaises(ValueError):
            self.store.update_models("groq", names, "2026-10-02", "https://other.example")

    def test_text_only_models_reject_srt_setting(self):
        with self.assertRaises(ValueError):
            self.store.save({**default_settings("openai"), "model": "gpt-4o-transcribe"})
        self.store.save({**default_settings("openai"), "model": "gpt-4o-transcribe", "response_format": "text"})

    def test_custom_qwen_port_keeps_server_managed_model(self):
        saved = self.store.save({**default_settings("custom"), "base_url": "http://127.0.0.1:11436", "model": "default"})
        self.store.update_models(saved["id"], ["default"], "2026-10-02", saved["base_url"], True)
        profile = next(item for item in self.store.snapshot()["profiles"] if item["id"] == saved["id"])
        self.assertTrue(profile["server_managed_model"])
        self.assertEqual(profile["models"], ["default"])

    def test_blank_custom_model_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save({**default_settings("custom"), "base_url": "https://asr.example/v1"})


class ApiSecurityTests(ProfileFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(create_app(self.store), base_url="http://127.0.0.1:8790")
        self.addCleanup(self.client.close)
        self.client.get("/")
        # The metadata route probes the GPU; other tests avoid that unrelated work.
        cookie = self.client.cookies.get("studio_session_8790")
        self.headers = {"X-Studio-Token": cookie}

    def test_api_without_browser_session_is_denied(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/asr/profiles").status_code, 401)

    def test_bad_host_and_foreign_origin_are_denied(self):
        self.assertEqual(self.client.get("/", headers={"Host": "attacker.example"}).status_code, 400)
        self.assertEqual(self.client.get("/", headers={"Origin": "https://attacker.example"}).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)

    def test_mutations_require_token_and_same_origin(self):
        body = default_settings("groq")
        self.assertEqual(self.client.post("/api/asr/profiles", json=body).status_code, 403)
        self.assertEqual(self.client.post("/api/asr/profiles", json=body, headers={**self.headers, "Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/asr/profiles", json=body, headers=self.headers).status_code, 200)

    def test_profile_api_never_returns_saved_key(self):
        saved = self.client.post("/api/asr/profiles", headers=self.headers, json={**default_settings("groq"), "save_key": True, "api_key": "fixture-secret"})
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["has_key"])
        self.assertNotIn("fixture-secret", saved.text)
        self.assertNotIn("fixture-secret", self.client.get("/api/asr/profiles").text)

    def test_validation_errors_do_not_echo_key(self):
        result = self.client.post("/api/asr/profiles", headers=self.headers, json={"api_key": "fixture-secret"})
        self.assertEqual(result.status_code, 422)
        self.assertNotIn("fixture-secret", result.text)

    def test_connection_uses_saved_key_and_redacts_remote_error(self):
        self.store.save(default_settings("groq"), "fixture-secret", True)
        with patch("lecture_video_to_pdf.api.test_openai_compatible_endpoint", return_value={"ok": False, "message": "invalid fixture-secret", "models": []}) as request:
            result = self.client.post("/api/test-openai-endpoint", headers=self.headers, json={"base_url": "https://api.groq.com/openai/v1", "profile_id": "groq", "use_saved_key": True})
        self.assertEqual(request.call_args.args[1], "fixture-secret")
        self.assertNotIn("fixture-secret", result.text)

    def test_job_resolves_key_server_side_without_exposing_it(self):
        self.store.save(default_settings("groq"), "fixture-secret", True)
        media = self.root / "sample.mp3"
        media.write_bytes(b"audio")
        with patch("lecture_video_to_pdf.api.manager.create") as create:
            create.return_value.to_dict.return_value = {"id": "test-job"}
            result = self.client.post("/api/jobs", headers=self.headers, json={"video_path": str(media), "task": "transcription", "asr": {"engine": "openai-compatible", "profile_id": "groq", "use_saved_key": True, "endpoint_base_url": "https://api.groq.com/openai/v1", "model": "whisper-large-v3-turbo"}})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(create.call_args.args[2].asr.api_key, "fixture-secret")
        self.assertNotIn("fixture-secret", result.text)

    def test_assets_are_not_cached_and_cookie_is_httponly(self):
        result = self.client.get("/")
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertIn("HttpOnly", result.headers["set-cookie"])
        self.assertIn("SameSite=strict", result.headers["set-cookie"])


class TranscriptionCapabilityTests(unittest.TestCase):
    def test_discovery_does_not_forward_credentials_on_redirect(self):
        redirect = httpx.Response(302, headers={"location": "https://other.example"})
        with patch("lecture_video_to_pdf.asr.httpx.get", return_value=redirect) as request:
            result = test_openai_compatible_endpoint("https://api.groq.com/openai/v1", "fixture-secret")
        self.assertFalse(result["ok"])
        self.assertFalse(request.call_args.kwargs["follow_redirects"])

    def test_openai_text_only_direct_has_no_fake_srt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "sample.mp3"
            media.write_bytes(b"audio")
            response = httpx.Response(200, json={"text": "Transcript text"}, request=httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions"))
            options = AsrOptions(model="gpt-4o-transcribe", endpoint_base_url="https://api.openai.com/v1", response_format="text", endpoint_upload_strategy="direct")
            with patch("lecture_video_to_pdf.asr.post_transcription_with_retry", return_value=(response, 1)) as request:
                result = run_openai_compatible(media, root, options, [], lambda *_: None)
            self.assertEqual(request.call_args.args[2]["response_format"], "json")
            self.assertEqual(Path(result["transcript_path"]).read_text().strip(), "Transcript text")
            self.assertNotIn("transcript_srt_path", result)
            self.assertFalse((root / "slide_map.md").exists())

    def test_openai_text_only_chunked_merges_text_without_srt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "sample.mp3"
            media.write_bytes(b"audio")
            chunks = [AudioChunk(1, media, 0, 10, 0, 10, 5), AudioChunk(2, media, 10, 20, 10, 20, 5)]
            manifest = root / "transcription_manifest.json"
            manifest.write_text(json.dumps({"chunks": [chunk.manifest_dict() for chunk in chunks]}))
            responses = [(httpx.Response(200, json={"text": text}, request=httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")), 1) for text in ["First", "Second"]]
            options = AsrOptions(model="gpt-4o-mini-transcribe", endpoint_base_url="https://api.openai.com/v1", response_format="json", endpoint_upload_strategy="chunked")
            with patch("lecture_video_to_pdf.asr.prepare_audio_chunks", return_value=(chunks, root / ".chunks", manifest)) as prepare, patch("lecture_video_to_pdf.asr.post_transcription_with_retry", side_effect=responses):
                result = run_openai_compatible(media, root, options, [], lambda *_: None)
            self.assertEqual(prepare.call_args.kwargs["overlap_seconds"], 0)
            self.assertEqual(Path(result["transcript_path"]).read_text().strip(), "First\nSecond")
            self.assertNotIn("transcript_srt_path", result)

    def test_failed_job_scrubs_and_releases_key(self):
        options = ConversionOptions(asr=AsrOptions(api_key="fixture-secret"))
        job = Job("test", "sample.mp3", "out", options)
        manager = JobManager()
        with patch("lecture_video_to_pdf.jobs.run_media_job", side_effect=RuntimeError("invalid fixture-secret")):
            manager._run(job)
        self.assertNotIn("fixture-secret", json.dumps(job.to_dict()))
        self.assertEqual(job.options.asr.api_key, "")
        manager._executor.shutdown()

    @unittest.skipUnless(sys.platform == "win32", "Windows credential API test")
    def test_real_windows_credential_roundtrip_and_cleanup(self):
        vault = WindowsCredentialStore(f"LectureVideo2PDF/Test/{uuid.uuid4().hex}")
        try:
            self.assertIsNone(vault.get("test-account"))
            vault.set("test-account", "fixture-secret")
            self.assertEqual(WindowsCredentialStore(vault.namespace).get("test-account"), "fixture-secret")
            vault.set("test-account", "updated-fixture")
            self.assertEqual(vault.get("test-account"), "updated-fixture")
        finally:
            vault.delete("test-account")
        self.assertIsNone(vault.get("test-account"))


if __name__ == "__main__":
    unittest.main()
