from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .credentials import CredentialError, WindowsCredentialStore

PROVIDERS = {
    "groq": {
        "name": "Groq", "base_url": "https://api.groq.com/openai/v1",
        "models": ["whisper-large-v3-turbo", "whisper-large-v3"],
        "default_model": "whisper-large-v3-turbo", "timestamps": True,
    },
    "openai": {
        "name": "OpenAI", "base_url": "https://api.openai.com/v1",
        "models": ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"],
        "default_model": "whisper-1", "timestamps": True,
    },
    "qwen": {
        "name": "本機 QwenASR", "base_url": "http://127.0.0.1:11435",
        "models": ["default"], "default_model": "default", "timestamps": True,
    },
}


def normalize_endpoint(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("端點必須是完整的 HTTP/HTTPS 網址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("端點網址不可含帳號、密碼、查詢參數或 fragment。")
    if parsed.scheme == "http" and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("雲端端點需使用 HTTPS；HTTP 僅允許本機 loopback。")
    port = parsed.port  # Validate the port before building the canonical endpoint.
    host = parsed.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    netloc = f"{host}:{port}" if port and port != {"http": 80, "https": 443}[parsed.scheme] else host
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def model_has_timestamps(provider: str, model: str, base_url: str = "") -> bool:
    is_openai = provider == "openai" or urlsplit(base_url).hostname == "api.openai.com"
    return not (is_openai and model in {"gpt-4o-transcribe", "gpt-4o-mini-transcribe"})


def transcription_models(provider: str, names: list[str]) -> list[str]:
    if provider in PROVIDERS:
        return [name for name in PROVIDERS[provider]["models"] if name in names]
    # Generic endpoints cannot enumerate capabilities reliably. Avoid returning all LLMs.
    return [name for name in names if re.search(r"whisper|asr|transcrib|sensevoice", name, re.I)]


def default_settings(provider: str) -> dict:
    preset = PROVIDERS.get(provider, {})
    return {
        "id": provider if provider in PROVIDERS else None, "provider": provider, "name": preset.get("name", "自訂端點"),
        "base_url": preset.get("base_url", ""), "model": preset.get("default_model", ""),
        "language": "", "response_format": "srt", "upload_strategy": "auto",
        "max_chunk_mb": 20, "chunk_minutes": 10,
        "models": list(preset.get("models", [])), "models_checked_at": None,
        "server_managed_model": provider == "qwen",
    }


class ProviderProfiles:
    def __init__(self, path: Path | None = None, vault=None):
        if path is None:
            base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
            path = base / "LectureVideo2PDF" / "preferences.json"
        self.path = path
        self.vault = vault if vault is not None else WindowsCredentialStore()
        self.lock = threading.RLock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "active_profile": "qwen", "profiles": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("profiles"), dict):
                raise ValueError()
            return data
        except (ValueError, OSError):
            raise ValueError("使用偏好檔案無法讀取，請先備份並檢查；原檔未被覆寫。") from None

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _profile(self, data: dict, profile_id: str) -> dict:
        if profile_id in PROVIDERS:
            return {**default_settings(profile_id), **data["profiles"].get(profile_id, {})}
        if profile_id in data["profiles"]:
            return dict(data["profiles"][profile_id])
        raise ValueError("找不到此服務設定檔。")

    def _account(self, profile: dict) -> str:
        binding = f"{profile['id']}\n{normalize_endpoint(profile['base_url'])}"
        return hashlib.sha256(binding.encode("utf-8")).hexdigest()

    def _public(self, profile: dict) -> dict:
        result = {key: profile.get(key) for key in default_settings(profile["provider"])}
        result["has_key"] = False
        result["key_status"] = "未保存金鑰"
        if self.vault.available:
            try:
                result["has_key"] = bool(self.vault.get(self._account(profile)))
                if result["has_key"]:
                    result["key_status"] = "金鑰已安全保存"
            except CredentialError:
                result["key_status"] = "憑證庫暫時無法讀取"
        result["model_capabilities"] = {
            model: {"timestamps": model_has_timestamps(profile["provider"], model, profile["base_url"])}
            for model in profile["models"]
        }
        return result

    def snapshot(self) -> dict:
        with self.lock:
            data = self._read()
            ids = list(PROVIDERS) + [key for key in data["profiles"] if key not in PROVIDERS]
            return {
                "active_profile": data["active_profile"],
                "configured": bool(data["profiles"]),
                "secure_storage_available": self.vault.available,
                "profiles": [self._public(self._profile(data, key)) for key in ids],
            }

    def save(self, settings: dict, key: str = "", save_key: bool = False) -> dict:
        with self.lock:
            data = self._read()
            provider = settings.get("provider", "custom")
            if provider not in {*PROVIDERS, "custom"}:
                raise ValueError("未知的服務類型。")
            profile_id = settings.get("id") or (provider if provider != "custom" else f"custom-{uuid.uuid4().hex}")
            if provider != "custom" and profile_id != provider:
                raise ValueError("內建服務設定檔 ID 不符。")
            if provider == "custom" and not re.fullmatch(r"custom-[a-f0-9]{32}", profile_id):
                raise ValueError("自訂設定檔 ID 格式錯誤。")
            profile = default_settings(provider)
            # Only these non-secret preferences may enter the JSON file.
            for field in ("name", "base_url", "model", "language", "response_format", "upload_strategy", "max_chunk_mb", "chunk_minutes"):
                if field in settings:
                    profile[field] = settings[field]
            profile["id"] = profile_id
            profile["name"] = str(profile["name"]).strip()[:80] or "自訂端點"
            profile["base_url"] = normalize_endpoint(profile["base_url"])
            if not isinstance(profile["model"], str) or not profile["model"].strip():
                raise ValueError("請選擇或輸入轉錄模型 ID。")
            profile["model"] = profile["model"].strip()
            if provider in PROVIDERS and profile["base_url"] != PROVIDERS[provider]["base_url"]:
                raise ValueError("內建服務網址固定；其他網址請建立自訂端點。")
            if provider in PROVIDERS and profile["model"] not in PROVIDERS[provider]["models"]:
                raise ValueError("此模型不在服務的已知轉錄模型清單。")
            if profile["response_format"] not in {"srt", "verbose_json", "json", "text"}:
                raise ValueError("不支援此輸出格式。")
            if not model_has_timestamps(provider, profile["model"], profile["base_url"]) and profile["response_format"] not in {"json", "text"}:
                raise ValueError("此模型不提供字幕時間戳，請選擇 text 或 json。")
            if profile["upload_strategy"] not in {"auto", "direct", "chunked"}:
                raise ValueError("不支援此上傳策略。")
            if not 5 <= profile["max_chunk_mb"] <= 95 or not 2 <= profile["chunk_minutes"] <= 30:
                raise ValueError("分段設定超出範圍。")
            previous = data["profiles"].get(profile_id)
            if previous and previous["base_url"] == profile["base_url"]:
                profile["models"] = previous.get("models", profile["models"])
                profile["models_checked_at"] = previous.get("models_checked_at")
                profile["server_managed_model"] = previous.get("server_managed_model", False)
            elif previous and self.vault.available:
                self.vault.delete(self._account(previous))
            if provider == "custom" and profile["model"] and profile["model"] not in profile["models"]:
                profile["models"].append(profile["model"])
            if save_key:
                if not key.strip():
                    raise ValueError("請輸入要保存的金鑰；現有金鑰不會被空值覆寫。")
                self.vault.set(self._account(profile), key.strip())
            data["profiles"][profile_id] = profile
            data["active_profile"] = profile_id
            self._write(data)
            return self._public(profile)

    def resolve(self, profile_id: str, base_url: str, key: str = "", use_saved_key: bool = False) -> tuple[dict, str]:
        with self.lock:
            data = self._read()
            profile = self._profile(data, profile_id)
            endpoint = normalize_endpoint(base_url)
            if endpoint != normalize_endpoint(profile["base_url"]):
                raise ValueError("端點網址已變更。請先保存為自訂端點並重新設定金鑰。")
            if key.strip():
                return profile, key.strip()
            if use_saved_key:
                stored = self.vault.get(self._account(profile))
                if not stored:
                    raise ValueError("此設定檔沒有已保存金鑰，請輸入金鑰。")
                return profile, stored
            return profile, ""

    def delete_key(self, profile_id: str) -> dict:
        with self.lock:
            profile = self._profile(self._read(), profile_id)
            self.vault.delete(self._account(profile))
            return self._public(profile)

    def update_models(self, profile_id: str, names: list[str], checked_at: str, base_url: str, server_managed: bool = False) -> list[str]:
        with self.lock:
            data = self._read()
            profile = self._profile(data, profile_id)
            if normalize_endpoint(base_url) != normalize_endpoint(profile["base_url"]):
                raise ValueError("端點已變更，請重新測試連線。")
            filtered = ["default"] if server_managed else transcription_models(profile["provider"], names)
            if filtered:
                profile["models"] = list(dict.fromkeys(filtered))
                profile["models_checked_at"] = checked_at
                profile["server_managed_model"] = server_managed
                if server_managed:
                    profile["model"] = "default"
                data["profiles"][profile_id] = profile
                self._write(data)
            return filtered


profiles = ProviderProfiles()
