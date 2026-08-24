#!/usr/bin/env python3
"""Refresh verified public Hormozi YouTube coverage.

This adapter requires the user to pass the official channel URL(s) explicitly
and requires ``yt-dlp`` to be installed.  It uses metadata and subtitle
requests only; it does not download video files.  The generated catalog stays
under ``private-input`` and can be reviewed before merging new transcript
atoms into the local brain.
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from yt_dlp import YoutubeDL
except ImportError:  # pragma: no cover - exercised as a clear CLI failure.
    YoutubeDL = None

from build_brain import slug, transcript_markdown


def parse_vtt(raw: str) -> str:
    lines: list[str] = []
    for line in raw.splitlines():
        value = line.strip()
        if not value or value == "WEBVTT" or "-->" in value or value.isdigit():
            continue
        value = re.sub(r"<[^>]+>", "", value)
        if value and (not lines or lines[-1] != value):
            lines.append(value)
    return "\n".join(lines)


def caption_url(info: dict) -> str | None:
    tracks = info.get("subtitles") or info.get("automatic_captions") or {}
    for language in ("en", "en-US", "en-GB"):
        for item in tracks.get(language, []):
            if item.get("url") and item.get("ext") in {"vtt", "srv3", "json3"}:
                return str(item["url"])
    for items in tracks.values():
        if items and items[0].get("url"):
            return str(items[0]["url"])
    return None


def fetch_caption(url: str) -> str:
    request = Request(url, headers={"User-Agent": "alex-hormozi-brain/1.0"})
    with urlopen(request, timeout=30) as response:
        return parse_vtt(response.read().decode("utf-8", errors="replace"))


def fetch_missing_caption(entry: dict, channel_url: str, youtube_dir: Path, options: dict) -> tuple[str, dict]:
    """Fetch one missing video's captions in an isolated yt-dlp worker."""
    video_id = str(entry["video_id"])
    row = dict(entry)
    try:
        video_options = dict(options)
        video_options["extract_flat"] = False
        with YoutubeDL(video_options) as ydl:
            info = ydl.extract_info(str(entry["url"]), download=False) or {}
        track = caption_url(info)
        if not track:
            row["status"] = "caption_unavailable_pending_openai_transcription"
            row["caption_track_status"] = "no_manual_or_automatic_caption_track"
            row["caption_checked_at"] = datetime.now(timezone.utc).isoformat()
            return video_id, row
        text = fetch_caption(track)
        if not text:
            row["status"] = "caption_empty"
            row["caption_track_status"] = "track_returned_empty"
            row["caption_checked_at"] = datetime.now(timezone.utc).isoformat()
            return video_id, row
        section = {
            "title": entry.get("title") or video_id,
            "url": entry["url"],
            "video_id": video_id,
            "channel": info.get("channel") or info.get("uploader"),
            "channel_id": info.get("channel_id"),
            "channel_url": channel_url,
            "published_at": info.get("release_timestamp") or info.get("upload_date"),
            "transcript": text,
            "source_file": f"official:{channel_url}",
            "source_line_start": 1,
            "source_line_end": len(text.splitlines()),
            "part_index": 1,
            "part_count": 1,
            "start_timestamp": None,
            "end_timestamp": None,
        }
        destination = youtube_dir / f"{slug(str(entry.get('title') or video_id))}-{video_id}-part-001.md"
        destination.write_text(transcript_markdown(section), encoding="utf-8", newline="\n")
        row["status"] = "caption_ingested"
        row["caption_track_status"] = "ingested"
        row["caption_checked_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as error:  # Network/caption failures remain visible in the catalog.  # noqa: BLE001
        row["status"] = "caption_error"
        row["caption_error"] = type(error).__name__
    return video_id, row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-url", action="append", required=True, help="Explicitly verified official channel /videos URL")
    parser.add_argument("--output", type=Path, default=Path("private-input/packs/alex-hormozi-brain-v1"))
    parser.add_argument("--fetch-captions", action="store_true")
    args = parser.parse_args()
    if YoutubeDL is None:
        raise SystemExit("yt-dlp is required; install it in the approved environment before running this refresh")
    youtube_dir = args.output / "youtube"
    youtube_dir.mkdir(parents=True, exist_ok=True)
    existing_ids = set()
    for path in youtube_dir.glob("*.md"):
        match = re.search(r"-([A-Za-z0-9_-]{11})-part-\d{3}$", path.stem)
        if match:
            existing_ids.add(match.group(1))
    catalog: list[dict] = []
    options = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        # Node is already present in the supported workspace runtime. This
        # avoids yt-dlp's deprecated no-JS fallback for subtitle metadata.
        "js_runtimes": {"node": {}},
    }
    with YoutubeDL(options) as ydl:
        for channel_url in args.channel_url:
            channel_info = ydl.extract_info(channel_url, download=False)
            if not channel_info:
                catalog.append({"channel_url": channel_url, "status": "unavailable"})
                continue
            entries = [entry for entry in (channel_info.get("entries") or []) if entry]
            channel_record = {"channel_url": channel_url, "channel_id": channel_info.get("channel_id"), "channel": channel_info.get("channel") or channel_info.get("uploader"), "videos": []}
            pending: list[dict] = []
            for entry in entries:
                video_id = str(entry.get("id") or "")
                if not video_id:
                    continue
                row = {
                    "video_id": video_id,
                    "title": entry.get("title"),
                    "url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                    "channel": channel_record.get("channel"),
                    "channel_id": channel_record.get("channel_id"),
                    "channel_url": channel_url,
                    "status": "already_present" if video_id in existing_ids else "metadata_only",
                }
                if args.fetch_captions and video_id not in existing_ids:
                    pending.append(row)
                channel_record["videos"].append(row)
            if pending:
                row_by_id = {str(row["video_id"]): row for row in pending}
                with ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [pool.submit(fetch_missing_caption, row, channel_url, youtube_dir, options) for row in pending]
                    for future in as_completed(futures):
                        video_id, updated = future.result()
                        row_by_id[video_id].update(updated)
            catalog.append(channel_record)
    report = {"verified_channel_urls": args.channel_url, "video_count": sum(len(row.get("videos", [])) for row in catalog), "channels": catalog}
    report_path = args.output / "meta" / "official-channel-catalog.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "video_count": report["video_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
