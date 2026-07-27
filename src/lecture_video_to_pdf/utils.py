from __future__ import annotations

import importlib.util
import ctypes
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


CUDA12_RUNTIME_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll", "cudnn64_9.dll")
_DLL_DIRECTORY_HANDLES: list[object] = []
_ADDED_DLL_DIRECTORIES: set[str] = set()


def package_installed(module_name: str) -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec(module_name) is not None


def _path_has_file(filename: str) -> bool:
    for raw_dir in os.environ.get("PATH", "").split(os.pathsep):
        if raw_dir and (Path(raw_dir) / filename).exists():
            return True
    return False


def _unique_existing_dirs(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            resolved = path.resolve()
        except Exception:
            continue
        key = str(resolved).lower()
        if key not in seen and resolved.is_dir():
            seen.add(key)
            result.append(resolved)
    return result


def cuda_dll_candidate_dirs(include_external_tools: bool = True) -> list[Path]:
    candidates: list[Path] = []
    env_dirs = os.environ.get("LECTURE_VIDEO_TO_PDF_CUDA_DLL_DIRS", "")
    candidates.extend(Path(p) for p in env_dirs.split(os.pathsep) if p)
    candidates.extend(Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p)

    package_roots = [Path(sys.prefix) / "Lib" / "site-packages"]
    frozen_root = getattr(sys, "_MEIPASS", "")
    if frozen_root:
        package_roots.append(Path(frozen_root))
    if getattr(sys, "frozen", False):
        package_roots.append(Path(sys.executable).resolve().parent / "_internal")

    for site_root in package_roots:
        candidates.extend(
            [
                site_root / "ctranslate2",
                site_root / "torch" / "lib",
                site_root / "nvidia" / "cublas" / "bin",
                site_root / "nvidia" / "cuda_runtime" / "bin",
                site_root / "nvidia" / "cudnn" / "bin",
            ]
        )

    if include_external_tools:
        external_dirs = os.environ.get("LECTURE_VIDEO_TO_PDF_EXTERNAL_CUDA_DIRS", "")
        candidates.extend(Path(p) for p in external_dirs.split(os.pathsep) if p)

    existing = _unique_existing_dirs(candidates)
    return [path for path in existing if any((path / dll).exists() for dll in CUDA12_RUNTIME_DLLS)]


def _add_dll_directory(path: Path) -> None:
    key = str(path.resolve()).lower()
    if key in _ADDED_DLL_DIRECTORIES:
        return
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(path)))
    current_path = os.environ.get("PATH", "")
    if str(path) not in current_path.split(os.pathsep):
        os.environ["PATH"] = f"{path}{os.pathsep}{current_path}" if current_path else str(path)
    _ADDED_DLL_DIRECTORIES.add(key)


def _dll_loadable(filename: str) -> bool:
    if sys.platform != "win32":
        return True
    try:
        ctypes.WinDLL(filename)
        return True
    except Exception:
        return False


def _cuda_gpu_available() -> bool:
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return True
    except Exception:
        pass
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def cuda_runtime_diagnostics(prepare: bool = False) -> dict[str, Any]:
    candidates = cuda_dll_candidate_dirs()
    if prepare:
        for path in candidates:
            _add_dll_directory(path)

    found: dict[str, list[str]] = {}
    for dll in CUDA12_RUNTIME_DLLS:
        found[dll] = [str(path) for path in candidates if (path / dll).exists()]
    loadable = {dll: _dll_loadable(dll) for dll in CUDA12_RUNTIME_DLLS}
    missing = [dll for dll, ok in loadable.items() if not ok]

    torch_cuda = None
    torch_cuda_available = False
    torch_device = ""
    try:
        import torch

        torch_cuda = getattr(torch.version, "cuda", None)
        torch_cuda_available = bool(torch.cuda.is_available())
        if torch_cuda_available:
            torch_device = torch.cuda.get_device_name(0)
    except Exception:
        pass

    ctranslate2_version = ""
    ctranslate2_cuda_devices = 0
    try:
        import ctranslate2

        ctranslate2_version = getattr(ctranslate2, "__version__", "")
        ctranslate2_cuda_devices = int(ctranslate2.get_cuda_device_count())
    except Exception:
        pass

    gpu_available = _cuda_gpu_available()
    runtime_ready = gpu_available and not missing
    if not gpu_available:
        message = "No CUDA GPU was detected by PyTorch or CTranslate2."
    elif runtime_ready:
        message = "CUDA GPU and CUDA 12 runtime DLLs are available."
    else:
        message = "CUDA GPU is detected, but CUDA 12 runtime DLLs are not fully loadable."

    return {
        "gpu_available": gpu_available,
        "runtime_ready": runtime_ready,
        "missing_runtime_dlls": missing,
        "loadable_runtime_dlls": loadable,
        "found_runtime_dll_dirs": found,
        "candidate_dirs": [str(path) for path in candidates],
        "added_dll_dirs": sorted(_ADDED_DLL_DIRECTORIES),
        "torch_cuda_available": torch_cuda_available,
        "torch_cuda_version": torch_cuda,
        "torch_device": torch_device,
        "ctranslate2_version": ctranslate2_version,
        "ctranslate2_cuda_devices": ctranslate2_cuda_devices,
        "message": message,
    }


def prepare_cuda_runtime() -> dict[str, Any]:
    return cuda_runtime_diagnostics(prepare=True)


def safe_slug(value: str, default: str = "video") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or default


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def find_ffmpeg(explicit: str | None = None) -> Path | None:
    if explicit:
        candidate = Path(explicit)
        if candidate.exists():
            return candidate
    which = shutil.which("ffmpeg")
    if which:
        return Path(which)
    try:
        import imageio_ffmpeg

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if bundled.exists():
            return bundled
    except Exception:
        pass
    for candidate in [
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files (x86)/ffmpeg/bin/ffmpeg.exe"),
    ]:
        if candidate.exists():
            return candidate
    return None


def find_ffprobe() -> Path | None:
    which = shutil.which("ffprobe")
    if which:
        return Path(which)
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        probe = ffmpeg.with_name("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if probe.exists():
            return probe
    return None


def detect_cuda_available() -> bool:
    return _cuda_gpu_available()


def qwen_asr_install_hint() -> dict[str, object]:
    root_raw = os.environ.get("LECTURE_VIDEO_TO_PDF_QWEN_ASR_ROOT") or os.environ.get("QWEN_ASR_HOME") or ""
    root = Path(root_raw) if root_raw else None
    start_gpu = root / "start-gpu.bat" if root else None
    cudagpu_model = root / "cudagpu" / "GPUModel" / "Qwen3-ASR-1.7B" / "config.json" if root else None
    aligner = root / "cudagpu" / "GPUModel" / "Qwen3-ForcedAligner-0.6B" / "config.json" if root else None
    vulkan_model = root / "GPUModel" / "qwen3-asr-1.7b.bin" if root else None
    return {
        "installed": bool(root and root.exists()),
        "root": str(root) if root else "",
        "start_gpu": str(start_gpu) if start_gpu else "",
        "start_gpu_exists": bool(start_gpu and start_gpu.exists()),
        "cuda_model_ready": bool(cudagpu_model and cudagpu_model.exists()),
        "aligner_ready": bool(aligner and aligner.exists()),
        "vulkan_model_ready": bool(vulkan_model and vulkan_model.exists()),
        "default_endpoint": "http://127.0.0.1:11435",
    }


def open_path(path: str | Path) -> None:
    p = Path(path)
    if sys.platform == "win32":
        os.startfile(str(p))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])
