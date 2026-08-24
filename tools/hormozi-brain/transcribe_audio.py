#!/usr/bin/env python3
"""Transcribe an authorized audio source through the existing OpenAI API.

Large files are converted to temporary mono MP3 chunks so the original audio
is never rewritten or retained as an upload artifact. Without an API key the
command supports ``--estimate-only`` and otherwise fails before opening audio.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def duration_seconds(audio: Path) -> float:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(completed.stdout.strip())


def plan_chunks(duration: float, chunk_seconds: int) -> list[tuple[int, int]]:
    if duration <= 0 or chunk_seconds <= 0:
        return []
    result = []
    start = 0
    while start < duration:
        end = min(int(duration + 0.999), start + chunk_seconds)
        result.append((start, end))
        start = end
    return result


def response_dict(response) -> dict:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "model_dump_json"):
        return json.loads(response.model_dump_json())
    if hasattr(response, "__dict__"):
        return dict(response.__dict__)
    return {"text": str(response)}


def transcribe_chunk(client, chunk: Path, model: str, retries: int) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with chunk.open("rb") as handle:
                response = client.audio.transcriptions.create(
                    model=model,
                    file=handle,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
            return response_dict(response)
        except Exception as error:  # SDK/network errors are retried without logging source text.
            last_error = error
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"audio transcription failed after {retries} attempts: {type(last_error).__name__}") from last_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-4o-mini-transcribe")
    parser.add_argument("--chunk-seconds", type=int, default=600)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--price-per-minute-usd", type=float, help="Optional approved transcription price used only for a projected estimate")
    parser.add_argument("--estimate-only", action="store_true")
    args = parser.parse_args()
    if not args.audio.is_file():
        raise SystemExit(f"Audio not found: {args.audio}")
    try:
        duration = duration_seconds(args.audio)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        raise SystemExit(f"ffprobe could not inspect audio: {type(error).__name__}") from error
    chunks = plan_chunks(duration, args.chunk_seconds)
    plan = {
        "audio": str(args.audio),
        "duration_seconds": duration,
        "estimated_audio_minutes": round(duration / 60, 3),
        "chunk_seconds": args.chunk_seconds,
        "estimated_chunks": len(chunks),
        "chunks": [{"start_seconds": start, "end_seconds": end} for start, end in chunks],
        "model": args.model,
        "timestamped": True,
        "usage_estimate": "audio duration and request count; token usage varies by speech content",
        "price_per_minute_usd": args.price_per_minute_usd,
        "projected_transcription_cost_usd": round(duration / 60 * args.price_per_minute_usd, 6) if args.price_per_minute_usd is not None else None,
    }
    if args.estimate_only:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"status": "estimate_only", **plan}, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "estimate_only", "duration_seconds": duration, "chunks": len(chunks), "output": str(args.output)}, indent=2))
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required; no audio was opened or uploaded")
    try:
        from openai import OpenAI
    except ImportError as error:
        raise SystemExit("Install the OpenAI Python SDK in the approved environment") from error
    if not shutil_which("ffmpeg"):
        raise SystemExit("ffmpeg is required to create temporary upload chunks")
    client = OpenAI()
    merged_segments: list[dict] = []
    merged_text: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hormozi-audio-") as temp:
        temp_dir = Path(temp)
        for index, (start, end) in enumerate(chunks):
            chunk = temp_dir / f"chunk-{index:04d}.mp3"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-to", str(end), "-i", str(args.audio), "-ac", "1", "-ar", "16000", "-codec:a", "libmp3lame", "-b:a", "64k", str(chunk)], check=True)
            response = transcribe_chunk(client, chunk, args.model, max(1, args.retries))
            merged_text.append(str(response.get("text", "")))
            for segment in response.get("segments", []) or []:
                segment = dict(segment)
                segment["start"] = float(segment.get("start", 0)) + start
                segment["end"] = float(segment.get("end", 0)) + start
                segment["chunk_index"] = index
                merged_segments.append(segment)
    output = {"status": "transcribed", **plan, "text": "\n".join(text for text in merged_text if text), "segments": merged_segments}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote timestamped transcription to {args.output}")
    return 0


def shutil_which(command: str) -> str | None:
    # Local import keeps estimate-only usable in minimal environments.
    import shutil
    return shutil.which(command)


if __name__ == "__main__":
    raise SystemExit(main())
