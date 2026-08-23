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
from pathlib import Path
import re
from urllib.request import Request, urlopen

try:
    from yt_dlp import YoutubeDL
except ImportError:  # pragma: no cover - exercised as a clear CLI failure.
    YoutubeDL = None

from build_brain import transcript_markdown, slug


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
    existing_ids = {path.stem.rsplit("-", 1)[-1] for path in youtube_dir.glob("*.md")}
    catalog: list[dict] = []
    options = {"extract_flat": "in_playlist", "skip_download": True, "quiet": True, "ignoreerrors": True}
    with YoutubeDL(options) as ydl:
        for channel_url in args.channel_url:
            channel_info = ydl.extract_info(channel_url, download=False)
            if not channel_info:
                catalog.append({"channel_url": channel_url, "status": "unavailable"})
                continue
            entries = [entry for entry in (channel_info.get("entries") or []) if entry]
            channel_record = {"channel_url": channel_url, "channel_id": channel_info.get("channel_id"), "channel": channel_info.get("channel") or channel_info.get("uploader"), "videos": []}
            for entry in entries:
                video_id = str(entry.get("id") or "")
                if not video_id:
                    continue
                row = {"video_id": video_id, "title": entry.get("title"), "url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}", "status": "already_present" if video_id in existing_ids else "metadata_only"}
                if args.fetch_captions and video_id not in existing_ids:
                    info = ydl.extract_info(row["url"], download=False) or {}
                    track = caption_url(info)
                    if track:
                        try:
                            text = fetch_caption(track)
                            if text:
                                section = {"title": row["title"] or video_id, "url": row["url"], "video_id": video_id, "transcript": text, "source_file": f"official:{channel_url}", "source_line_start": 1, "source_line_end": len(text.splitlines()), "part_index": 1, "part_count": 1, "start_timestamp": None, "end_timestamp": None}
                                destination = youtube_dir / f"{slug(str(row['title'] or video_id))}-{video_id}-part-001.md"
                                destination.write_text(transcript_markdown(section), encoding="utf-8", newline="\n")
                                row["status"] = "caption_ingested"
                        except Exception as error:  # Network/caption errors remain visible in the catalog.
                            row["caption_error"] = type(error).__name__
                    else:
                        row["status"] = "caption_unavailable_pending_openai_transcription"
                channel_record["videos"].append(row)
            catalog.append(channel_record)
    report = {"verified_channel_urls": args.channel_url, "video_count": sum(len(row.get("videos", [])) for row in catalog), "channels": catalog}
    report_path = args.output / "meta" / "official-channel-catalog.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "video_count": report["video_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
