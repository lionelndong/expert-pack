#!/usr/bin/env python3
"""Transcribe captionless verified official videos through the existing OpenAI API.

Only an ephemeral best-audio stream is downloaded to a temporary directory. The
video itself is never retained, and the command fails before any media request
when ``OPENAI_API_KEY`` is absent. The resulting transcript is timestamped and
the official-channel catalog is updated with its provenance.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from yt_dlp import YoutubeDL
except ImportError:  # pragma: no cover - clear CLI failure path
    YoutubeDL = None

from build_brain import slug, transcript_markdown
from transcribe_audio import (
    duration_seconds,
    plan_chunks,
    transcribe_chunk,
)


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"


def segment_lines(segments: list[dict], fallback_text: str) -> str:
    lines: list[str] = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if text:
            lines.append(f"{format_timestamp(float(segment.get('start', 0)))} {text}")
    return "\n".join(lines) if lines else fallback_text.strip()


def find_audio_file(directory: Path, video_id: str) -> Path:
    candidates = sorted(path for path in directory.iterdir() if path.is_file() and video_id in path.stem)
    if not candidates:
        candidates = sorted(path for path in directory.iterdir() if path.is_file())
    if not candidates:
        raise RuntimeError("yt-dlp produced no temporary audio file")
    return candidates[0]


def transcribe_video(entry: dict, output_pack: Path, model: str, chunk_seconds: int, retries: int) -> dict:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required; no media was opened or downloaded")
    if YoutubeDL is None:
        raise RuntimeError("yt-dlp is required in the approved environment")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required for temporary audio chunks")
    try:
        from openai import OpenAI
    except ImportError as error:  # pragma: no cover - environment-specific
        raise RuntimeError("Install the OpenAI Python SDK in the approved environment") from error

    url = str(entry["url"])
    video_id = str(entry["video_id"])
    title = str(entry.get("title") or video_id)
    metadata_options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "js_runtimes": {"node": {}},
    }
    with YoutubeDL(metadata_options) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    title = str(info.get("title") or title)
    channel_url = str(entry.get("channel_url") or info.get("channel_url") or "verified-official-channel")

    client = OpenAI()
    merged_segments: list[dict] = []
    merged_text: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hormozi-official-audio-") as temp:
        temp_dir = Path(temp)
        download_options = {
            "format": "bestaudio/best",
            "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "js_runtimes": {"node": {}},
        }
        with YoutubeDL(download_options) as ydl:
            ydl.download([url])
        audio_path = find_audio_file(temp_dir, video_id)
        duration = duration_seconds(audio_path)
        chunks = plan_chunks(duration, chunk_seconds)
        for index, (start, end) in enumerate(chunks):
            chunk = temp_dir / f"chunk-{index:04d}.mp3"
            import subprocess

            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(start), "-to", str(end), "-i", str(audio_path),
                    "-ac", "1", "-ar", "16000", "-codec:a", "libmp3lame", "-b:a", "64k", str(chunk),
                ],
                check=True,
            )
            response = transcribe_chunk(client, chunk, model, max(1, retries))
            merged_text.append(str(response.get("text", "")))
            for raw_segment in response.get("segments", []) or []:
                segment = dict(raw_segment)
                segment["start"] = float(segment.get("start", 0)) + start
                segment["end"] = float(segment.get("end", 0)) + start
                segment["chunk_index"] = index
                merged_segments.append(segment)

    transcript = segment_lines(merged_segments, "\n".join(text for text in merged_text if text))
    section = {
        "title": title,
        "url": url,
        "video_id": video_id,
        "transcript": transcript,
        "source_file": f"official:{channel_url} (OpenAI {model}; audio-only temporary extraction)",
        "source_line_start": 1,
        "source_line_end": len(transcript.splitlines()),
        "part_index": 1,
        "part_count": 1,
        "start_timestamp": 0,
        "end_timestamp": max((float(item.get("end", 0)) for item in merged_segments), default=None),
    }
    destination = output_pack / "youtube" / f"{slug(title, 'video')}-{video_id}-part-001.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(transcript_markdown(section), encoding="utf-8", newline="\n")
    result = {
        "status": "openai_transcribed",
        "transcript_path": str(destination),
        "transcription_model": model,
        "transcribed_at": datetime.now(timezone.utc).isoformat(),
        "audio_retained": False,
        "timestamped_segments": len(merged_segments),
    }
    return result


def update_catalog(catalog_path: Path, video_id: str, result: dict) -> None:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    found = False
    for channel in catalog.get("channels", []):
        for video in channel.get("videos", []):
            if str(video.get("video_id")) == video_id:
                video.update(result)
                found = True
    if not found:
        raise RuntimeError(f"Video ID {video_id} was not found in the verified official catalog")
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def estimate_video(entry: dict, model: str, chunk_seconds: int, price_per_minute: float | None) -> dict:
    """Read metadata only and estimate media requests before any download/API call."""

    if YoutubeDL is None:
        raise RuntimeError("yt-dlp is required in the approved environment")
    options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "js_runtimes": {"node": {}}}
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(str(entry["url"]), download=False) or {}
    duration = float(info.get("duration") or 0)
    chunks = plan_chunks(duration, chunk_seconds)
    result = {
        "video_id": str(entry["video_id"]),
        "title": str(info.get("title") or entry.get("title") or entry["video_id"]),
        "url": str(entry["url"]),
        "duration_seconds": duration,
        "estimated_audio_minutes": round(duration / 60, 3),
        "estimated_chunks": len(chunks),
        "chunk_seconds": chunk_seconds,
        "model": model,
        "price_per_minute_usd": price_per_minute,
        "projected_transcription_cost_usd": round(duration / 60 * price_per_minute, 6) if price_per_minute is not None else None,
        "media_downloaded": False,
        "api_called": False,
        "usage_estimate": "audio duration and request count; token usage varies by speech content",
        "metadata_status": "available",
        "metadata_source": "yt-dlp",
    }
    return result


def estimate_from_metadata(entry: dict, metadata: dict, model: str, chunk_seconds: int, price_per_minute: float | None) -> dict:
    """Build the same estimate from a read-only metadata cache, never media."""

    duration = float(metadata.get("duration_seconds") or 0)
    if duration <= 0:
        raise ValueError("metadata cache duration_seconds must be positive")
    chunks = plan_chunks(duration, chunk_seconds)
    result = {
        "video_id": str(entry["video_id"]),
        "title": str(metadata.get("title") or entry.get("title") or entry["video_id"]),
        "url": str(metadata.get("url") or entry["url"]),
        "duration_seconds": duration,
        "duration_iso": metadata.get("duration_iso"),
        "estimated_audio_minutes": round(duration / 60, 3),
        "estimated_chunks": len(chunks),
        "chunk_seconds": chunk_seconds,
        "model": model,
        "price_per_minute_usd": price_per_minute,
        "projected_transcription_cost_usd": round(duration / 60 * price_per_minute, 6) if price_per_minute is not None else None,
        "media_downloaded": False,
        "api_called": False,
        "usage_estimate": "audio duration and request count; token usage varies by speech content",
        "metadata_status": "available",
        "metadata_source": str(metadata.get("metadata_source") or "approved metadata cache"),
    }
    for key in ("transcript_available", "transcript_check_method"):
        if key in metadata:
            result[key] = metadata[key]
    return result


def unavailable_estimate(entry: dict, model: str, chunk_seconds: int, price_per_minute: float | None, error: Exception) -> dict:
    """Record a metadata-only failure without downloading media or calling an API."""

    return {
        "video_id": str(entry["video_id"]),
        "title": str(entry.get("title") or entry["video_id"]),
        "url": str(entry["url"]),
        "duration_seconds": None,
        "estimated_audio_minutes": None,
        "estimated_chunks": None,
        "chunk_seconds": chunk_seconds,
        "model": model,
        "price_per_minute_usd": price_per_minute,
        "projected_transcription_cost_usd": None,
        "media_downloaded": False,
        "api_called": False,
        "metadata_status": "unavailable_pending_transcription",
        "error_type": type(error).__name__,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-id", action="append", required=True, help="Catalog video ID; repeat for multiple videos")
    parser.add_argument("--catalog", type=Path, default=Path("private-input/packs/alex-hormozi-brain-v1/meta/official-channel-catalog.json"))
    parser.add_argument("--output", type=Path, default=Path("private-input/packs/alex-hormozi-brain-v1"))
    parser.add_argument("--model", default="gpt-4o-mini-transcribe")
    parser.add_argument("--chunk-seconds", type=int, default=600)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--price-per-minute-usd", type=float, help="Optional approved transcription price used only for a projected estimate")
    parser.add_argument("--metadata-cache", type=Path, help="Optional read-only JSON cache of browser-verified video durations")
    parser.add_argument("--estimate-only", action="store_true", help="Use metadata only; do not download media or call OpenAI")
    args = parser.parse_args()
    if not args.catalog.is_file():
        raise SystemExit(f"Official-channel catalog not found: {args.catalog}")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    videos = {str(video.get("video_id")): video for channel in catalog.get("channels", []) for video in channel.get("videos", [])}
    metadata_cache: dict[str, dict] = {}
    if args.metadata_cache:
        try:
            cache = json.loads(args.metadata_cache.read_text(encoding="utf-8"))
            metadata_cache = {
                str(item["video_id"]): item
                for item in cache.get("videos", [])
                if isinstance(item, dict) and item.get("video_id")
            }
        except (OSError, json.JSONDecodeError, AttributeError, TypeError, KeyError) as error:
            raise SystemExit(f"Invalid official metadata cache: {type(error).__name__}") from error
    estimates: list[dict] = []
    for video_id in args.video_id:
        if video_id not in videos:
            raise SystemExit(f"Video ID {video_id} is not present in the verified official catalog")
        if args.estimate_only:
            try:
                if video_id in metadata_cache:
                    estimates.append(estimate_from_metadata(videos[video_id], metadata_cache[video_id], args.model, args.chunk_seconds, args.price_per_minute_usd))
                else:
                    estimates.append(estimate_video(videos[video_id], args.model, args.chunk_seconds, args.price_per_minute_usd))
            except Exception as error:  # noqa: BLE001 - preserve provider failures in the estimate report
                estimates.append(unavailable_estimate(videos[video_id], args.model, args.chunk_seconds, args.price_per_minute_usd, error))
            continue
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is required; no media was opened or downloaded")
        result = transcribe_video(videos[video_id], args.output, args.model, args.chunk_seconds, args.retries)
        update_catalog(args.catalog, video_id, result)
        print(json.dumps({"video_id": video_id, **result}, ensure_ascii=False))
    if args.estimate_only:
        report_path = args.output / "meta" / "official-transcription-estimate.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_available = all(item.get("duration_seconds") is not None and item.get("estimated_chunks") is not None for item in estimates)
        report = {
            "report_version": "1.0",
            "model": args.model,
            "videos": estimates,
            "metadata_available": metadata_available,
            "total_duration_seconds": sum(item["duration_seconds"] for item in estimates) if metadata_available else None,
            "total_estimated_chunks": sum(item["estimated_chunks"] for item in estimates) if metadata_available else None,
            "api_called": False,
            "media_downloaded": False,
            "output": str(report_path),
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": "estimate_only", "videos": len(estimates), "output": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
