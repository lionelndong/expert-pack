#!/usr/bin/env python3
"""Transcribe an authorized audio source through the existing OpenAI API."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-4o-mini-transcribe")
    args = parser.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required; no audio was opened or uploaded")
    try:
        from openai import OpenAI
    except ImportError as error:
        raise SystemExit("Install the OpenAI Python SDK in the approved environment") from error
    client = OpenAI()
    with args.audio.open("rb") as handle:
        response = client.audio.transcriptions.create(model=args.model, file=handle, response_format="verbose_json", timestamp_granularities=["segment"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(response.model_dump_json(indent=2) if hasattr(response, "model_dump_json") else str(response), encoding="utf-8")
    print(f"Wrote timestamped transcription to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
