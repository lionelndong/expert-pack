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
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree

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


def _clean_caption_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    return re.sub(r"\s+", " ", value).strip()


def _caption_seconds(value: object) -> float | None:
    """Parse common YouTube caption time formats into seconds."""

    if value is None:
        return None
    raw = str(value).strip().replace(",", ".")
    if not raw:
        return None
    try:
        if re.fullmatch(r"\d+(?:\.\d+)?", raw):
            return float(raw)
        parts = raw.split(":")
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return None


def _format_caption_timestamp(seconds: float | None) -> str:
    """Render a caption start as the transcript parser's ``(M:SS)`` marker."""

    if seconds is None:
        return ""
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"({hours}:{minutes:02d}:{second:02d})"
    return f"({minutes}:{second:02d})"


def _dedupe_caption_segments(segments: list[tuple[float | None, str]]) -> list[tuple[float | None, str]]:
    result: list[tuple[float | None, str]] = []
    for start, text in segments:
        if text and (not result or result[-1][1] != text):
            result.append((start, text))
    return result


def parse_vtt_segments(raw: str) -> list[tuple[float | None, str]]:
    """Parse WebVTT/SRT cues while retaining cue start times."""

    segments: list[tuple[float | None, str]] = []
    start: float | None = None
    text_lines: list[str] = []

    def flush() -> None:
        nonlocal start, text_lines
        text = _clean_caption_text(" ".join(text_lines))
        if text:
            segments.append((start, text))
        start = None
        text_lines = []

    for line in raw.splitlines() + [""]:
        value = line.strip()
        if "-->" in value:
            flush()
            start = _caption_seconds(value.split("-->", 1)[0].strip().split()[0])
            continue
        if not value:
            flush()
            continue
        if value == "WEBVTT" or value.isdigit() or value.startswith(("NOTE", "STYLE", "REGION")):
            continue
        text_lines.append(value)
    return _dedupe_caption_segments(segments)


def parse_srv3(raw: str) -> str:
    """Extract text nodes from YouTube's XML ``srv3`` caption format."""

    root = ElementTree.fromstring(raw)
    lines: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "text":
            continue
        value = _clean_caption_text("".join(element.itertext()))
        if value and (not lines or lines[-1] != value):
            lines.append(value)
    return "\n".join(lines)


def parse_xml_caption_segments(raw: str) -> list[tuple[float | None, str]]:
    """Parse SRV/TTML caption XML and retain ``start``/``begin`` locators."""

    root = ElementTree.fromstring(raw)
    segments: list[tuple[float | None, str]] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in {"text", "p"}:
            continue
        value = _clean_caption_text("".join(element.itertext()))
        if not value:
            continue
        start = element.attrib.get("start") or element.attrib.get("begin")
        segments.append((_caption_seconds(start), value))
    return _dedupe_caption_segments(segments)


def parse_json3(raw: str) -> str:
    """Extract segment text from YouTube's ``json3`` caption format."""

    payload = json.loads(raw)
    lines: list[str] = []
    for event in payload.get("events", []) if isinstance(payload, dict) else []:
        segments = event.get("segs", []) if isinstance(event, dict) else []
        value = _clean_caption_text("".join(str(segment.get("utf8", "")) for segment in segments if isinstance(segment, dict)))
        if value and (not lines or lines[-1] != value):
            lines.append(value)
    return "\n".join(lines)


def parse_json3_segments(raw: str) -> list[tuple[float | None, str]]:
    """Parse JSON3 caption events while retaining millisecond start times."""

    payload = json.loads(raw)
    segments: list[tuple[float | None, str]] = []
    for event in payload.get("events", []) if isinstance(payload, dict) else []:
        if not isinstance(event, dict):
            continue
        text = _clean_caption_text(
            "".join(
                str(segment.get("utf8", ""))
                for segment in event.get("segs", [])
                if isinstance(segment, dict)
            )
        )
        if not text:
            continue
        raw_start = event.get("tStartMs", event.get("t"))
        start = float(raw_start) / 1000 if raw_start is not None else None
        segments.append((start, text))
    return _dedupe_caption_segments(segments)


def parse_caption_segments(raw: str) -> list[tuple[float | None, str]]:
    """Parse supported caption formats into ``(start_seconds, text)`` pairs."""

    stripped = raw.lstrip()
    if stripped.startswith(("{", "[")):
        return parse_json3_segments(raw)
    if stripped.startswith("<"):
        return parse_xml_caption_segments(raw)
    return parse_vtt_segments(raw)


def parse_caption(raw: str) -> str:
    """Parse VTT, SRV3 XML, or JSON3 captions without trusting file suffixes."""

    return "\n".join(text for _start, text in parse_caption_segments(raw))


def format_caption_segments(segments: list[tuple[float | None, str]]) -> str:
    """Render parsed captions as timestamped transcript lines."""

    lines: list[str] = []
    for start, text in segments:
        marker = _format_caption_timestamp(start)
        lines.append(f"{marker} {text}".strip() if marker else text)
    return "\n".join(lines)


def caption_track(info: dict) -> tuple[str, str, str] | None:
    """Choose an English manual/automatic caption track, then any supported track."""

    supported = {"vtt", "srt", "srv1", "srv2", "srv3", "json3", "ttml"}
    groups = (
        ("manual", info.get("subtitles") or {}),
        ("automatic", info.get("automatic_captions") or {}),
    )
    for origin, tracks in groups:
        for language in ("en", "en-US", "en-GB"):
            for item in tracks.get(language, []):
                if item.get("url") and item.get("ext") in supported:
                    return str(item["url"]), str(item["ext"]), origin
    for origin, tracks in groups:
        for items in tracks.values():
            for item in items:
                if item.get("url") and item.get("ext") in supported:
                    return str(item["url"]), str(item["ext"]), origin
    return None


def caption_url(info: dict) -> str | None:
    track = caption_track(info)
    return track[0] if track else None


def fetch_caption(url: str) -> str:
    request = Request(url, headers={"User-Agent": "alex-hormozi-brain/1.0"})
    with urlopen(request, timeout=30) as response:
        return format_caption_segments(parse_caption_segments(response.read().decode("utf-8", errors="replace")))


def fetch_missing_caption(entry: dict, channel_url: str, youtube_dir: Path, options: dict) -> tuple[str, dict]:
    """Fetch one missing video's captions in an isolated yt-dlp worker."""
    video_id = str(entry["video_id"])
    row = dict(entry)
    try:
        video_options = dict(options)
        video_options["extract_flat"] = False
        with YoutubeDL(video_options) as ydl:
            info = ydl.extract_info(str(entry["url"]), download=False) or {}
        track = caption_track(info)
        if not track:
            row["status"] = "caption_unavailable_pending_openai_transcription"
            row["caption_track_status"] = "no_manual_or_automatic_caption_track"
            row["caption_checked_at"] = datetime.now(timezone.utc).isoformat()
            return video_id, row
        text = fetch_caption(track[0])
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
        row["caption_track_status"] = f"{track[2]}_{track[1]}_ingested"
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
                # A metadata-only refresh must never make an untranscribed
                # video look complete.  The catalog is an acceptance ledger,
                # so every video without a local caption/transcript remains an
                # explicit pending transcription record until captions or an
                # approved OpenAI transcript are actually ingested.
                row = {
                    "video_id": video_id,
                    "title": entry.get("title"),
                    "url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                    "channel": channel_record.get("channel"),
                    "channel_id": channel_record.get("channel_id"),
                    "channel_url": channel_url,
                    "status": "already_present" if video_id in existing_ids else "caption_unavailable_pending_openai_transcription",
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
    report = {
        "report_version": "1.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verification_method": "yt-dlp metadata-only enumeration from explicitly supplied verified channel URL(s)",
        "media_downloaded": False,
        "captions_requested": bool(args.fetch_captions),
        "verified_channel_urls": args.channel_url,
        "video_count": sum(len(row.get("videos", [])) for row in catalog),
        "channels": catalog,
    }
    report_path = args.output / "meta" / "official-channel-catalog.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "video_count": report["video_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
