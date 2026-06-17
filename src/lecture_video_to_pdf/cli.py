from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .api import create_app
from .models import AsrOptions, ConversionOptions
from .pipeline import run_conversion, run_transcription


def _progress(ratio: float, message: str) -> None:
    print(f"[{ratio * 100:5.1f}%] {message}", flush=True)


def run_server(host: str, port: int, open_browser: bool) -> None:
    import uvicorn

    if open_browser:
        webbrowser.open(f"http://{host}:{port}/")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def convert(args: argparse.Namespace) -> int:
    asr = AsrOptions(
        engine=args.asr,
        model=args.asr_model,
        language=args.language,
        endpoint_base_url=args.endpoint_base_url,
        api_key=args.api_key,
        response_format=args.response_format,
        device=args.device,
        compute_type=args.compute_type,
    )
    options = ConversionOptions(mode=args.mode, crop=args.crop, asr=asr)
    result = run_conversion(args.video, args.out, options, progress=_progress)
    print(f"PDF: {result.pdf_path}")
    print(f"Output: {result.output_dir}")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


def transcribe(args: argparse.Namespace) -> int:
    asr = AsrOptions(
        engine=args.asr,
        model=args.asr_model,
        language=args.language,
        endpoint_base_url=args.endpoint_base_url,
        api_key=args.api_key,
        response_format=args.response_format,
        device=args.device,
        compute_type=args.compute_type,
    )
    result = run_transcription(args.media, args.out, asr, progress=_progress)
    print(f"Transcript TXT: {result.get('transcript_path')}")
    print(f"Transcript SRT: {result.get('transcript_srt_path')}")
    print(f"Output: {result.get('output_dir')}")
    if result.get("warnings"):
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lecture-video-to-pdf")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Start the local Web UI")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8787)
    run.add_argument("--no-browser", action="store_true")

    conv = sub.add_parser("convert", help="Convert a lecture video to a clean slide PDF")
    conv.add_argument("video")
    conv.add_argument("--out", default="output")
    conv.add_argument("--crop", default="auto", help="auto or x,y,w,h")
    conv.add_argument("--mode", choices=["fast", "balanced", "sensitive"], default="balanced")
    conv.add_argument("--asr", choices=["none", "faster-whisper", "openai-compatible"], default="none")
    conv.add_argument("--asr-model", default="base")
    conv.add_argument("--language", default="", help="auto, zh-TW, zh-CN, en, ja, ...")
    conv.add_argument("--response-format", choices=["srt", "verbose_json", "json", "text"], default="srt")
    conv.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    conv.add_argument("--compute-type", choices=["auto", "int8", "float16", "int8_float16"], default="auto")
    conv.add_argument("--endpoint-base-url", default="")
    conv.add_argument("--api-key", default="")

    trans = sub.add_parser("transcribe", help="Transcribe a video or audio file")
    trans.add_argument("media")
    trans.add_argument("--out", default="output")
    trans.add_argument("--asr", choices=["faster-whisper", "openai-compatible"], default="faster-whisper")
    trans.add_argument("--asr-model", default="base")
    trans.add_argument("--language", default="", help="auto, zh-TW, zh-CN, en, ja, ...")
    trans.add_argument("--response-format", choices=["srt", "verbose_json", "json", "text"], default="srt")
    trans.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    trans.add_argument("--compute-type", choices=["auto", "int8", "float16", "int8_float16"], default="auto")
    trans.add_argument("--endpoint-base-url", default="")
    trans.add_argument("--api-key", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        run_server(args.host, args.port, not args.no_browser)
        return 0
    if args.command == "convert":
        return convert(args)
    if args.command == "transcribe":
        return transcribe(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
