#!/usr/bin/env python3
"""
NAD Info Screen - fetch media from a Google Drive folder shared as
"Anyone with the link" (read with an API key, no login).

Environment:
    GDRIVE_API_KEY    Google Cloud API key with the Drive API enabled
    GDRIVE_FOLDER_ID  ID of the shared folder (the part after /folders/ in its link)
    SITE              Public URL of the published site (to compare with the last version)
    FORCE             "true" = always download and rebuild

Writes to $GITHUB_OUTPUT:
    changed=true|false
    media_dir=<folder with the downloaded files>
Also writes drive_state.txt (fingerprint of the folder content) in the current folder.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = os.environ.get("GDRIVE_API_BASE", "https://www.googleapis.com/drive/v3/files")


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def list_folder(folder_id: str, key: str) -> list:
    files, token = [], None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, size, md5Checksum, modifiedTime)",
            "pageSize": "1000",
            "key": key,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if token:
            params["pageToken"] = token
        data = get_json(f"{API}?{urllib.parse.urlencode(params)}")
        files += data.get("files", [])
        token = data.get("nextPageToken")
        if not token:
            return files


def output(name: str, value: str) -> None:
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write(f"{name}={value}\n")
    print(f"{name}={value}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="drive_media", type=Path)
    args = ap.parse_args()

    key = os.environ.get("GDRIVE_API_KEY", "").strip()
    folder = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
    site = os.environ.get("SITE", "").rstrip("/")
    force = os.environ.get("FORCE", "false").lower() == "true"
    if not key or not folder:
        print("::error::GDRIVE_API_KEY (secret) or GDRIVE_FOLDER_ID (variable) is missing")
        return 1

    try:
        items = list_folder(folder, key)
    except urllib.error.HTTPError as e:
        print(f"::error::Google Drive answered {e.code}: {e.read().decode(errors='ignore')[:300]}")
        print("::error::Check that the folder is shared as 'Anyone with the link' and that the API key allows the Drive API.")
        return 1

    media, skipped = [], []
    for it in items:
        if it["mimeType"].startswith("application/vnd.google-apps"):
            skipped.append("a Google Docs/Slides file or subfolder (export it as JPG/PNG/MP4 first)")
        else:
            media.append(it)

    fingerprint = hashlib.sha256(json.dumps(
        sorted((m["id"], m["name"], m.get("md5Checksum") or m.get("modifiedTime"), m.get("size")) for m in media)
    ).encode()).hexdigest()
    Path("drive_state.txt").write_text(fingerprint + "\n")

    previous = ""
    if site and not force:
        try:
            with urllib.request.urlopen(f"{site}/drive_state.txt", timeout=30) as r:
                previous = r.read().decode().strip()
        except Exception:
            previous = ""

    for s in skipped:
        print(f"::warning::Skipped {s}")

    if previous == fingerprint:
        print(f"No changes in the Drive folder ({len(media)} files).")
        output("changed", "false")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    used = set()
    total = 0
    for m in media:
        name = re.sub(r'[\\/:*?"<>|\r\n]', "_", m["name"]).strip() or m["id"]
        if name.casefold() in used:  # same name twice in Drive
            p = Path(name)
            name = f"{p.stem} ({m['id'][:6]}){p.suffix}"
        used.add(name.casefold())
        url = f"{API}/{m['id']}?alt=media&acknowledgeAbuse=true&key={urllib.parse.quote(key)}"
        dst = args.out / name
        with urllib.request.urlopen(url, timeout=600) as r, open(dst, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        total += dst.stat().st_size

    if total > 900e6:
        print(f"::warning::The folder holds {total / 1e9:.2f} GB. GitHub Pages sites may be no larger than 1 GB.")

    print(f"Downloaded {len(media)} files ({total / 1e6:.1f} MB).")
    output("changed", "true")
    output("media_dir", str(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
