from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .asr import test_openai_compatible_endpoint
from .config import load_config
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
    response_format: str = "verbose_json"


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


class InstallRequest(BaseModel):
    confirm: bool = False


class SlideReviewRequest(BaseModel):
    order: list[int] | None = None
    kept: dict[str, bool] = {}


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=APP_VERSION)

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/app.css")
    def css():
        return FileResponse(WEB_DIR / "app.css")

    @app.get("/app.js")
    def js():
        return FileResponse(WEB_DIR / "app.js")

    @app.get("/api/meta")
    def meta():
        return {
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
        return test_openai_compatible_endpoint(req.base_url, req.api_key)

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
