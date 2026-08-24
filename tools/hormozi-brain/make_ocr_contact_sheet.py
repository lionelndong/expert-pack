#!/usr/bin/env python3
"""Create a visual-QA contact sheet with one representative page per PDF."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("private-input/ocr-results"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cards = []
    for report_path in sorted((args.root / "reports").glob("*.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        items = report.get("results", [])
        if not items:
            continue
        item = items[0]
        image_path = Path(str(item.get("qa_image", "")))
        if image_path.is_file():
            cards.append((str(report.get("source_id")), str(report.get("relative_path", "")), str(item.get("classification", "pending")), image_path))
    width, card_width, card_height, label_height, columns = 1600, 380, 520, 100, 4
    sheet = Image.new("RGB", (width, ((len(cards) + columns - 1) // columns) * card_height), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (source_id, relative_path, classification, image_path) in enumerate(cards):
        with Image.open(image_path).convert("RGB") as image:
            image.thumbnail((card_width - 20, card_height - label_height - 10))
            x = (index % columns) * card_width + (card_width - image.width) // 2
            y = (index // columns) * card_height + 5
            sheet.paste(image, (x, y))
        tx = (index % columns) * card_width + 8
        ty = (index // columns) * card_height + card_height - label_height
        label = f"{source_id}\n{classification}\n{relative_path[:48]}"
        draw.multiline_text((tx, ty), label, fill="black", spacing=2)
    output = args.output or args.root / "representative-pages-contact-sheet.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
