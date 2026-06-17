from __future__ import annotations

import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import package_installed, prepare_cuda_runtime


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def faster_whisper_install_command() -> tuple[list[str], Path | None]:
    requirements = _project_root() / "requirements-asr.txt"
    if requirements.exists():
        return [sys.executable, "-m", "pip", "install", "-r", str(requirements)], requirements
    return [sys.executable, "-m", "pip", "install", "faster-whisper>=1.0"], None


def cuda_runtime_install_command() -> tuple[list[str], Path | None]:
    requirements = _project_root() / "requirements-asr-cuda.txt"
    if requirements.exists():
        return [sys.executable, "-m", "pip", "install", "-r", str(requirements)], requirements
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "nvidia-cublas-cu12>=12.0",
        "nvidia-cuda-runtime-cu12>=12.0",
        "nvidia-cudnn-cu12>=9.0",
    ], None


@dataclass
class InstallState:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "idle"
    message: str = "faster-whisper is not installed."
    installed: bool = False
    running: bool = False
    return_code: int | None = None
    command: list[str] = field(default_factory=list)
    requirements_path: str | None = None
    python_executable: str = sys.executable
    log: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "installed": self.installed,
            "running": self.running,
            "return_code": self.return_code,
            "command": self.command,
            "requirements_path": self.requirements_path,
            "python_executable": self.python_executable,
            "log": self.log[-80:],
            "details": self.details,
        }


class FasterWhisperInstaller:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = InstallState()

    def status(self) -> dict[str, Any]:
        installed = package_installed("faster_whisper")
        with self._lock:
            self._state.installed = installed
            if installed and not self._state.running:
                self._state.status = "installed"
                self._state.message = "faster-whisper is installed."
            return self._state.to_dict()

    def start(self) -> dict[str, Any]:
        if package_installed("faster_whisper"):
            with self._lock:
                self._state.installed = True
                self._state.running = False
                self._state.status = "installed"
                self._state.message = "faster-whisper is already installed."
                return self._state.to_dict()

        with self._lock:
            if self._state.running:
                return self._state.to_dict()
            command, requirements = faster_whisper_install_command()
            self._state = InstallState(
                status="running",
                message="Installing faster-whisper optional ASR dependencies...",
                running=True,
                installed=False,
                command=command,
                requirements_path=str(requirements) if requirements else None,
                log=[],
            )
            snapshot = self._state.to_dict()

        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        return snapshot

    def _append_log(self, line: str) -> None:
        clean = line.rstrip()
        if not clean:
            return
        with self._lock:
            self._state.log.append(clean)
            self._state.log = self._state.log[-120:]

    def _run(self) -> None:
        with self._lock:
            command = list(self._state.command)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(_project_root()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                self._append_log(line)
            return_code = process.wait()
            installed = package_installed("faster_whisper")
            with self._lock:
                self._state.return_code = return_code
                self._state.running = False
                self._state.installed = installed
                if return_code == 0 and installed:
                    self._state.status = "installed"
                    self._state.message = "faster-whisper installation completed."
                elif return_code == 0:
                    self._state.status = "failed"
                    self._state.message = "pip completed, but faster-whisper still cannot be imported."
                else:
                    self._state.status = "failed"
                    self._state.message = f"pip install failed with exit code {return_code}."
        except Exception as exc:
            with self._lock:
                self._state.running = False
                self._state.installed = package_installed("faster_whisper")
                self._state.status = "failed"
                self._state.message = str(exc)


faster_whisper_installer = FasterWhisperInstaller()


class CudaRuntimeInstaller:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = InstallState(message="CUDA 12 runtime is not ready.")

    def status(self) -> dict[str, Any]:
        diagnostics = prepare_cuda_runtime()
        with self._lock:
            self._state.installed = bool(diagnostics.get("runtime_ready"))
            self._state.details = diagnostics
            if self._state.running:
                return self._state.to_dict()
            if not diagnostics.get("gpu_available"):
                self._state.status = "unavailable"
                self._state.message = "No supported CUDA GPU was detected."
            elif diagnostics.get("runtime_ready"):
                self._state.status = "installed"
                self._state.message = "CUDA 12 runtime is ready."
            else:
                missing = ", ".join(diagnostics.get("missing_runtime_dlls", [])) or "unknown DLLs"
                self._state.status = "missing"
                self._state.message = f"CUDA GPU detected, but CUDA 12 runtime is incomplete: {missing}."
            return self._state.to_dict()

    def start(self) -> dict[str, Any]:
        diagnostics = prepare_cuda_runtime()
        if not diagnostics.get("gpu_available"):
            with self._lock:
                self._state.installed = False
                self._state.running = False
                self._state.status = "unavailable"
                self._state.message = "No supported CUDA GPU was detected."
                self._state.details = diagnostics
                return self._state.to_dict()
        if diagnostics.get("runtime_ready"):
            with self._lock:
                self._state.installed = True
                self._state.running = False
                self._state.status = "installed"
                self._state.message = "CUDA 12 runtime is already ready."
                self._state.details = diagnostics
                return self._state.to_dict()

        with self._lock:
            if self._state.running:
                return self._state.to_dict()
            command, requirements = cuda_runtime_install_command()
            self._state = InstallState(
                status="running",
                message="Installing optional CUDA 12 runtime dependencies...",
                running=True,
                installed=False,
                command=command,
                requirements_path=str(requirements) if requirements else None,
                log=[],
                details=diagnostics,
            )
            snapshot = self._state.to_dict()

        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        return snapshot

    def _append_log(self, line: str) -> None:
        clean = line.rstrip()
        if not clean:
            return
        with self._lock:
            self._state.log.append(clean)
            self._state.log = self._state.log[-120:]

    def _run(self) -> None:
        with self._lock:
            command = list(self._state.command)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(_project_root()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                self._append_log(line)
            return_code = process.wait()
            diagnostics = prepare_cuda_runtime()
            installed = bool(diagnostics.get("runtime_ready"))
            with self._lock:
                self._state.return_code = return_code
                self._state.running = False
                self._state.installed = installed
                self._state.details = diagnostics
                if return_code == 0 and installed:
                    self._state.status = "installed"
                    self._state.message = "CUDA 12 runtime installation completed."
                elif return_code == 0:
                    missing = ", ".join(diagnostics.get("missing_runtime_dlls", [])) or "unknown DLLs"
                    self._state.status = "failed"
                    self._state.message = f"pip completed, but CUDA runtime is still incomplete: {missing}."
                else:
                    self._state.status = "failed"
                    self._state.message = f"pip install failed with exit code {return_code}."
        except Exception as exc:
            diagnostics = prepare_cuda_runtime()
            with self._lock:
                self._state.running = False
                self._state.installed = bool(diagnostics.get("runtime_ready"))
                self._state.details = diagnostics
                self._state.status = "failed"
                self._state.message = str(exc)


cuda_runtime_installer = CudaRuntimeInstaller()
