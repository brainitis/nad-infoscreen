#!/usr/bin/env python3
"""
NAD Info Screen - build a WildStream playlist from a folder of media.

Reads every file in the media folder (alphabetical order), converts formats
WildStream cannot play directly, copies everything into an output folder and
writes playlist.csv in WildStream's format:

    Title,URL,Description,Type,DisplayDuration

Order:
    Files are shown in RANDOM order. The order is reshuffled only when files are
    added or removed (same set of files -> same order on every rebuild).
    Files whose name starts with "00" (e.g. 00_Velkommen.jpg) are pinned:
    they always come first, in alphabetical order.

Filename conventions (optional):
    Velkommen__15s.jpg        -> show this photo for 15 seconds (default: 10)
    Logo-animation__8s.gif    -> GIF is converted to an 8-second looping MP4
"""
import argparse
import csv
import hashlib
import random
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

PHOTO_COPY = {".jpg", ".jpeg", ".png"}
PHOTO_CONVERT = {".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp"}
VIDEO_COPY = {".mp4", ".m4v"}
VIDEO_CONVERT = {".mov", ".avi", ".mkv", ".webm"}
ANIMATION = {".gif"}
DEFAULT_SECONDS = 10
DURATION_RE = re.compile(r"__(\d{1,4})s$", re.IGNORECASE)


def slug(text: str) -> str:
    """ASCII-safe file name (æ->ae, ø->oe, å->aa, spaces -> '-')."""
    for a, b in (("æ", "ae"), ("ø", "oe"), ("å", "aa"), ("Æ", "Ae"), ("Ø", "Oe"), ("Å", "Aa")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return text or "file"


def run(cmd: list) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--media", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--base-url", required=True, help="Public URL of the output folder")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    files_dir = args.out / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    sources = sorted(
        (p for p in args.media.iterdir()
         if p.is_file() and not p.name.startswith(".") and p.name != "Icon\r"),
        key=lambda p: p.name.casefold(),
    )
    # Pinned items ("00..." names) first, in alphabetical order; the rest shuffled.
    # The shuffle seed is the set of file names, so the order only changes
    # when a file is added, removed or renamed.
    pinned = [p for p in sources if p.name.startswith("00")]
    rest = [p for p in sources if not p.name.startswith("00")]
    seed = hashlib.sha256("\n".join(p.name for p in sources).encode()).hexdigest()
    random.Random(seed).shuffle(rest)
    sources = pinned + rest

    rows, skipped = [], []
    for n, src in enumerate(sources, start=1):
        ext = src.suffix.lower()
        stem = src.stem
        m = DURATION_RE.search(stem)
        seconds = int(m.group(1)) if m else DEFAULT_SECONDS
        clean_stem = DURATION_RE.sub("", stem)
        title = re.sub(r"^\d+[a-z]?[ _.-]+", "", clean_stem).replace("_", " ").replace(",", " -").strip() or clean_stem
        # Stable output name (independent of position), so the Apple TV can keep
        # its cached copy when the order is reshuffled.
        out_stem = f"{slug(clean_stem)}-{hashlib.sha1(src.name.encode()).hexdigest()[:6]}"

        try:
            if ext in PHOTO_COPY:
                dst = files_dir / f"{out_stem}{'.jpg' if ext == '.jpeg' else ext}"
                shutil.copy2(src, dst)
                kind = "Photo"
            elif ext in PHOTO_CONVERT:
                dst = files_dir / f"{out_stem}.jpg"
                if ext in {".heic", ".heif"} and shutil.which("heif-convert"):
                    run(["heif-convert", "-q", "92", str(src), str(dst)])
                else:
                    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(dst)])
                kind = "Photo"
            elif ext in VIDEO_COPY:
                dst = files_dir / f"{out_stem}{ext}"
                shutil.copy2(src, dst)
                kind = "Video"
            elif ext in VIDEO_CONVERT:
                dst = files_dir / f"{out_stem}.mp4"
                run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)])
                kind = "Video"
            elif ext in ANIMATION:
                # Loop the GIF for the chosen number of seconds and turn it into an MP4.
                dst = files_dir / f"{out_stem}.mp4"
                run(["ffmpeg", "-y", "-loglevel", "error", "-ignore_loop", "0", "-i", str(src),
                     "-t", str(seconds), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                     "-an", "-movflags", "+faststart", str(dst)])
                kind = "Video"
            else:
                skipped.append(f"{src.name} (unsupported file type)")
                continue
        except subprocess.CalledProcessError as e:
            skipped.append(f"{src.name} (conversion failed: {e.stderr.decode(errors='ignore').strip()[:200]})")
            continue

        rows.append({
            "Title": title,
            "URL": f"{base}/files/{quote(dst.name)}",
            "Description": "",
            "Type": kind,
            "DisplayDuration": str(seconds) if kind == "Photo" else "",
        })

    with open(args.out / "playlist.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Title", "URL", "Description", "Type", "DisplayDuration"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    # Simple overview page, handy for checking what is on the screen.
    items = "\n".join(
        f"<li>{r['Type']}: <a href=\"{r['URL']}\">{r['Title']}</a>"
        f"{' (' + r['DisplayDuration'] + ' s)' if r['DisplayDuration'] else ''}</li>" for r in rows)
    skipped_html ="".join(f"<li>{s}</li>" for s in skipped)
    (args.out / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>NAD Info Screen</title>"
        "<body style='font-family:Lato,sans-serif;max-width:720px;margin:40px auto'>"
        f"<h1>NAD Info Screen</h1><p>Playlist: <a href='{base}/playlist.csv'>{base}/playlist.csv</a></p>"
        f"<ol>{items}</ol>" + (f"<h2>Skipped</h2><ul>{skipped_html}</ul>" if skipped else ""),
        encoding="utf-8")

    print(f"Playlist: {len(rows)} items")
    for s in skipped:
        print(f"::warning::Skipped {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
