from __future__ import annotations

import json
import secrets
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .asr import test_openai_compatible_endpoint
from .config import load_config
from .credentials import CredentialError
from .installer import cuda_runtime_installer, faster_whisper_installer
from .jobs import manager
from .metadata import (
    APP_NAME,
    APP_NAME_ZH,
    APP_VERSION,
    ASR_MODELS,
    AUTHOR_BLOG_URL,
    AUTHOR_DESCRIPTION,
    AUTHOR_FACEBOOK_URL,
    AUTHOR_NAME,
    LANGUAGE_OPTIONS,
    LICENSE_NAME,
    OPENAI_ASR_MODELS,
    SOURCE_REPO_URL,
)
from .models import AsrOptions, ConversionOptions
from .providers import ProviderProfiles, model_has_timestamps, normalize_endpoint, profiles
from .security import LocalSessionMiddleware
from .pipeline import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, is_supported_media_file, is_video_file, rebuild_pdf_from_metadata
from .utils import detect_cuda_available, ensure_dir, package_installed, prepare_cuda_runtime, qwen_asr_install_hint, safe_slug

WEB_DIR = Path(__file__).parent / "web"


class AsrRequest(BaseModel):
    engine: Literal["none", "faster-whisper", "openai-compatible"] = "none"
    model: str = "base"
    language: str | None = ""
    device: str = "auto"
    compute_type: str = "auto"
    endpoint_base_url: str = ""
    api_key: str = ""
    profile_id: str | None = None
    use_saved_key: bool = False
    response_format: str = "verbose_json"
    endpoint_upload_strategy: Literal["auto", "direct", "chunked"] = "auto"
    endpoint_max_chunk_mb: int = Field(default=20, ge=5, le=95)
    endpoint_chunk_minutes: int = Field(default=10, ge=2, le=30)


class JobRequest(BaseModel):
    video_path: str
    output_dir: str | None = None
    task: Literal["slides", "transcription", "slides_and_transcription"] = "slides"
    mode: Literal["fast", "balanced", "sensitive"] = "balanced"
    crop: str = "auto"
    asr: AsrRequest = AsrRequest()


class EndpointTestRequest(BaseModel):
    base_url: str
    api_key: str = ""
    profile_id: str | None = None
    use_saved_key: bool = False


class ProfileRequest(BaseModel):
    id: str | None = None
    provider: Literal["groq", "openai", "qwen", "custom"]
    name: str = Field(default="", max_length=80)
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=256)
    language: str | None = ""
    response_format: str = "srt"
    upload_strategy: str = "auto"
    max_chunk_mb: int = Field(default=20, ge=5, le=95)
    chunk_minutes: int = Field(default=10, ge=2, le=30)
    api_key: str = Field(default="", max_length=2560)
    save_key: bool = False


class InstallRequest(BaseModel):
    confirm: bool = False


class SlideReviewRequest(BaseModel):
    order: list[int] | None = None
    kept: dict[str, bool] = {}


def create_app(profile_store: ProviderProfiles | None = None) -> FastAPI:
    app = FastAPI(title=APP_NAME, version=APP_VERSION)
    store = profile_store if profile_store is not None else profiles
    token = secrets.token_urlsafe(32)
    app.add_middleware(LocalSessionMiddleware, token=token)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        # Pydantic's default errors can echo the whole request, including the key.
        errors = [{"loc": error["loc"], "type": error["type"], "msg": error["msg"]} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    def credentials(profile_id, base_url, key, use_saved):
        try:
            endpoint = normalize_endpoint(base_url)
            if profile_id:
                profile, resolved = store.resolve(profile_id, endpoint, key, use_saved)
                return endpoint, resolved, profile
            if use_saved:
                raise ValueError("使用已保存金鑰需指定服務設定檔。")
            return endpoint, key.strip(), None
        except (ValueError, CredentialError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/app.css")
    def css():
        return FileResponse(WEB_DIR / "app.css")

    @app.get("/app.js")
    def js():
        return FileResponse(WEB_DIR / "app.js")

    @app.get("/api/meta")
    def meta():
        return {
            "csrf_token": token,
            "app": {"name": APP_NAME, "name_zh": APP_NAME_ZH, "version": APP_VERSION},
            "author": {
                "name": AUTHOR_NAME,
                "description": AUTHOR_DESCRIPTION,
                "blog": AUTHOR_BLOG_URL,
                "facebook": AUTHOR_FACEBOOK_URL,
                "source_repo": SOURCE_REPO_URL,
                "license": LICENSE_NAME,
            },
            "asr": {
                "models": ASR_MODELS,
                "openai_models": OPENAI_ASR_MODELS,
                "languages": LANGUAGE_OPTIONS,
                "faster_whisper_installed": package_installed("faster_whisper"),
                "faster_whisper_install": faster_whisper_installer.status(),
                "cuda_available": detect_cuda_available(),
                "cuda_diagnostics": prepare_cuda_runtime(),
                "cuda_runtime_install": cuda_runtime_installer.status(),
                "qwen_hint": qwen_asr_install_hint(),
            },
            "media": {
                "video_extensions": sorted(VIDEO_EXTENSIONS),
                "audio_extensions": sorted(AUDIO_EXTENSIONS),
            },
        }

    @app.post("/api/uploads")
    async def upload_videos(files: list[UploadFile] = File(...)):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        upload_root = ensure_dir(Path("uploads") / f"{stamp}_{uuid.uuid4().hex[:8]}")
        saved = []
        for upload in files:
            original_name = upload.filename or "media"
            dest = upload_root / safe_slug(Path(original_name).stem, "media")
            suffix = Path(original_name).suffix
            final_path = dest.with_suffix(suffix)
            counter = 1
            while final_path.exists():
                final_path = dest.with_name(f"{dest.name}_{counter}").with_suffix(suffix)
                counter += 1
            with final_path.open("wb") as fh:
                shutil.copyfileobj(upload.file, fh)
            saved.append({"name": original_name, "path": str(final_path)})
        return {"files": saved}

    @app.post("/api/jobs")
    def create_job(req: JobRequest):
        config = load_config()
        output_dir = req.output_dir or config.output_dir
        source_path = Path(req.video_path)
        if not source_path.exists():
            raise HTTPException(status_code=400, detail=f"Media not found: {req.video_path}")
        if req.task in {"slides", "slides_and_transcription"} and not is_video_file(source_path):
            raise HTTPException(status_code=400, detail="PDF extraction requires a video file.")
        if req.task == "transcription" and not is_supported_media_file(source_path):
            raise HTTPException(status_code=400, detail="Transcription supports common video/audio files.")
        asr_payload = req.asr.model_dump() if hasattr(req.asr, "model_dump") else req.asr.dict()
        profile_id = asr_payload.pop("profile_id")
        use_saved = asr_payload.pop("use_saved_key")
        if req.asr.engine == "openai-compatible":
            endpoint, key, profile = credentials(profile_id, req.asr.endpoint_base_url, req.asr.api_key, use_saved)
            asr_payload.update(endpoint_base_url=endpoint, api_key=key)
            if not model_has_timestamps(profile["provider"] if profile else "custom", req.asr.model, endpoint) and req.asr.response_format not in {"json", "text"}:
                raise HTTPException(status_code=400, detail="此模型不提供字幕時間戳，請選擇 text 或 json。")
        asr_options = AsrOptions(**asr_payload)
        if req.task in {"transcription", "slides_and_transcription"} and asr_options.engine == "none":
            raise HTTPException(status_code=400, detail="Transcription requires an ASR engine.")
        options = ConversionOptions(mode=req.mode, crop=req.crop, asr=asr_options)
        job = manager.create(req.video_path, output_dir, options, task=req.task)
        return job.to_dict()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job.to_dict()

    @app.get("/api/jobs/{job_id}/artifacts")
    def get_artifacts(job_id: str):
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        data = job.to_dict()
        result = data.get("result")
        if not result:
            raise HTTPException(status_code=409, detail="Job is not completed")
        return {
            "pdf": result.get("pdf_path"),
            "metadata": result.get("metadata_path"),
            "slides_dir": result.get("slides_dir"),
            "transcript": result.get("transcript_path"),
            "transcript_srt": result.get("transcript_srt_path"),
            "transcription_manifest": result.get("transcription_manifest_path"),
            "slide_map": result.get("slide_map_path"),
            "slides": result.get("slides", []),
        }

    @app.get("/api/jobs/{job_id}/thumbs/{filename}")
    def get_thumb(job_id: str, filename: str):
        job = manager.get(job_id)
        if job is None or not job.result:
            raise HTTPException(status_code=404, detail="Job not found")
        if not job.result.get("thumbs_dir"):
            raise HTTPException(status_code=404, detail="Thumbnail not available for this job")
        thumbs = Path(job.result["thumbs_dir"]).resolve()
        path = (thumbs / filename).resolve()
        if not path.exists() or path.parent != thumbs:
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        return FileResponse(path)

    @app.post("/api/jobs/{job_id}/slides/review")
    def review_slides(job_id: str, req: SlideReviewRequest):
        job = manager.get(job_id)
        if job is None or not job.result:
            raise HTTPException(status_code=404, detail="Job not found")
        result = job.result
        slides = list(result.get("slides", []))
        if req.kept:
            for slide in slides:
                key = str(slide.get("index"))
                if key in req.kept:
                    slide["kept"] = bool(req.kept[key])
        if req.order:
            by_index = {int(slide.get("index")): slide for slide in slides}
            ordered = [by_index[i] for i in req.order if i in by_index]
            ordered.extend(slide for slide in slides if int(slide.get("index")) not in set(req.order))
            slides = ordered
        result["slides"] = slides
        metadata_path = Path(result["metadata_path"])
        metadata_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        pdf = rebuild_pdf_from_metadata(metadata_path)
        result["pdf_path"] = str(pdf)
        return {"ok": True, "pdf_path": str(pdf), "slides": slides}

    @app.post("/api/test-openai-endpoint")
    def test_endpoint(req: EndpointTestRequest):
        endpoint, key, profile = credentials(req.profile_id, req.base_url, req.api_key, req.use_saved_key)
        result = test_openai_compatible_endpoint(endpoint, key)
        if key:
            result["message"] = str(result.get("message", "")).replace(key, "[redacted]")
        if result.get("ok") and profile:
            try:
                result["models"] = store.update_models(profile["id"], result.get("models", []), datetime.now().astimezone().isoformat(), endpoint, bool(result.get("server_managed_model")))
            except (ValueError, CredentialError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        return result

    @app.get("/api/asr/profiles")
    def get_profiles():
        try:
            return store.snapshot()
        except (ValueError, CredentialError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/asr/profiles")
    def save_profile(req: ProfileRequest):
        data = req.model_dump()
        key, save_key = data.pop("api_key"), data.pop("save_key")
        try:
            return store.save(data, key, save_key)
        except (ValueError, CredentialError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/asr/profiles/{profile_id}/delete-key")
    def delete_profile_key(profile_id: str):
        try:
            return store.delete_key(profile_id)
        except (ValueError, CredentialError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/asr/faster-whisper/install")
    def faster_whisper_install_status():
        return faster_whisper_installer.status()

    @app.post("/api/asr/faster-whisper/install")
    def install_faster_whisper(req: InstallRequest):
        if not req.confirm:
            raise HTTPException(status_code=400, detail="Explicit confirmation is required before installing dependencies.")
        return faster_whisper_installer.start()

    @app.get("/api/asr/cuda-runtime/install")
    def cuda_runtime_install_status():
        return cuda_runtime_installer.status()

    @app.post("/api/asr/cuda-runtime/install")
    def install_cuda_runtime(req: InstallRequest):
        if not req.confirm:
            raise HTTPException(status_code=400, detail="Explicit confirmation is required before installing CUDA runtime dependencies.")
        return cuda_runtime_installer.start()

    return app


app = create_app()
