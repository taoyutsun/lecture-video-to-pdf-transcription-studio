from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ConversionOptions, TaskType
from .pipeline import run_media_job


@dataclass
class Job:
    id: str
    video_path: str
    output_dir: str
    options: ConversionOptions
    task: TaskType = "slides"
    status: str = "queued"
    progress: float = 0.0
    message: str = "Queued"
    result: dict[str, Any] | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, progress: float, message: str) -> None:
        with self.lock:
            self.progress = max(0.0, min(1.0, float(progress)))
            self.message = message

    def to_dict(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "video_path": self.video_path,
                "media_path": self.video_path,
                "output_dir": self.output_dir,
                "task": self.task,
                "status": self.status,
                "progress": self.progress,
                "message": self.message,
                "result": self.result,
                "error": self.error,
            }


class JobManager:
    def __init__(self, max_workers: int = 1):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create(self, video_path: str, output_dir: str, options: ConversionOptions, task: TaskType = "slides") -> Job:
        job = Job(str(uuid.uuid4()), video_path, output_dir, options, task)
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job) -> None:
        with job.lock:
            job.status = "running"
            job.message = "Starting"
        try:
            result = run_media_job(job.video_path, job.output_dir, job.options, task=job.task, progress=job.update)
            with job.lock:
                job.status = "completed"
                job.progress = 1.0
                job.message = "Completed"
                job.result = result
        except Exception as exc:
            with job.lock:
                job.status = "failed"
                job.error = str(exc).replace(job.options.asr.api_key, "[redacted]") if job.options.asr.api_key else str(exc)
                job.message = "Failed"
        finally:
            job.options.asr.api_key = ""


manager = JobManager(max_workers=1)
