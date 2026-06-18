"""
Image Gallery — Usage tracking, folder browsing, and smart grouping.

Provides a persistent record of which library images have been used
(for video renders or thumbnail backgrounds), grouped views of the
library, and safe read-only browsing of arbitrary directories.
"""

import json
import os
import threading
import time
import urllib.parse
from pathlib import Path

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}

# ─── Usage Persistence ───────────────────────────────────────────────────────

_usage_lock = threading.Lock()


def _usage_file(output_dir: str) -> str:
    return os.path.join(output_dir, "image_usage.json")


def _load_usage(output_dir: str) -> list[dict]:
    path = _usage_file(output_dir)
    with _usage_lock:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []


def _save_usage(output_dir: str, records: list[dict]):
    path = _usage_file(output_dir)
    with _usage_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


def record_usage(
    output_dir: str,
    image_name: str,
    usage_type: str,
    channel_name: str,
    item_title: str = "",
):
    """Append an image-usage event.

    Args:
        output_dir:   Base output directory (contains ``library/``).
        image_name:   Filename of the library image.
        usage_type:   ``"render"`` or ``"thumbnail"``.
        channel_name: Channel the image was used for.
        item_title:   Optional item/video title.
    """
    records = _load_usage(output_dir)
    records.append({
        "image_name": image_name,
        "usage_type": usage_type,
        "channel_name": channel_name,
        "item_title": item_title,
        "timestamp": time.time(),
    })
    _save_usage(output_dir, records)


def get_usage_history(output_dir: str, channel_name: str | None = None) -> list[dict]:
    """Return usage records, optionally filtered by channel."""
    records = _load_usage(output_dir)
    if channel_name:
        records = [r for r in records if r.get("channel_name") == channel_name]
    return records


# ─── Grouped Library ─────────────────────────────────────────────────────────

def get_grouped_library(output_dir: str, channel_name: str | None = None) -> dict:
    """Return library images grouped by usage category.

    Each image entry includes:
      - name, url, used_in (list of {usage_type, channel_name, item_title, timestamp})
      - used_by_channel: bool  (True when *channel_name* has used this image)

    Returns dict with keys: render_images, thumbnail_images, uncategorized.
    """
    library_dir = os.path.join(output_dir, "library")
    if not os.path.isdir(library_dir):
        return {"render_images": [], "thumbnail_images": [], "uncategorized": []}

    # Gather all library files
    all_images: dict[str, dict] = {}
    for fname in sorted(os.listdir(library_dir)):
        if os.path.splitext(fname)[1].lower() in _IMAGE_EXTENSIONS:
            all_images[fname] = {
                "name": fname,
                "url": "/output/library/" + urllib.parse.quote(fname),
                "used_in": [],
                "used_by_channel": False,
            }

    # Overlay usage records
    records = _load_usage(output_dir)
    for rec in records:
        img = all_images.get(rec.get("image_name", ""))
        if img is None:
            continue
        img["used_in"].append({
            "usage_type": rec["usage_type"],
            "channel_name": rec.get("channel_name", ""),
            "item_title": rec.get("item_title", ""),
            "timestamp": rec.get("timestamp", 0),
        })
        if channel_name and rec.get("channel_name") == channel_name:
            img["used_by_channel"] = True

    # Categorise
    render, thumb, uncat = [], [], []
    for img in all_images.values():
        types = {u["usage_type"] for u in img["used_in"]}
        if "render" in types:
            render.append(img)
        if "thumbnail" in types:
            thumb.append(img)
        if not types:
            uncat.append(img)

    return {
        "render_images": render,
        "thumbnail_images": thumb,
        "uncategorized": uncat,
    }


# ─── Folder Browsing ─────────────────────────────────────────────────────────

def browse_folder(folder_path: str) -> dict:
    """List image files in a directory (read-only, no recursion).

    Returns:
        {path, parent, folders, images}
    """
    folder_path = os.path.normpath(os.path.abspath(folder_path))

    if not os.path.isdir(folder_path):
        return {"path": folder_path, "parent": None, "folders": [], "images": [], "error": "Not a directory"}

    parent = os.path.dirname(folder_path)
    if parent == folder_path:
        parent = None  # root of a drive

    folders = []
    images = []
    try:
        for entry in sorted(os.scandir(folder_path), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                folders.append({"name": entry.name, "path": entry.path.replace("\\", "/")})
            elif entry.is_file() and os.path.splitext(entry.name)[1].lower() in _IMAGE_EXTENSIONS:
                images.append({
                    "name": entry.name,
                    "path": entry.path.replace("\\", "/"),
                })
    except PermissionError:
        return {"path": folder_path, "parent": parent, "folders": [], "images": [], "error": "Permission denied"}

    return {
        "path": folder_path.replace("\\", "/"),
        "parent": parent.replace("\\", "/") if parent else None,
        "folders": folders,
        "images": images,
    }


def get_browse_roots(output_dir: str) -> list[dict]:
    """Return quick-access folder roots for the gallery browser."""
    roots = []

    # Library
    lib = os.path.join(output_dir, "library")
    if os.path.isdir(lib):
        roots.append({"name": "📁 Library", "path": lib.replace("\\", "/"), "type": "library"})

    # Channel folders
    skip = {"library", "audio", "videos", "thumbnails", "uploads"}
    if os.path.isdir(output_dir):
        for name in sorted(os.listdir(output_dir)):
            full = os.path.join(output_dir, name)
            if os.path.isdir(full) and name.lower() not in skip:
                roots.append({"name": f"📺 {name}", "path": full.replace("\\", "/"), "type": "channel"})

    return roots


def get_channel_images(output_dir: str, channel_name: str) -> list[dict]:
    """List original YouTube thumbnail images inside a channel folder."""
    from modules.downloader import sanitize_filename
    safe = sanitize_filename(channel_name)
    channel_dir = os.path.join(output_dir, safe)
    images = []
    if not os.path.isdir(channel_dir):
        return images
    for fname in sorted(os.listdir(channel_dir)):
        ext = os.path.splitext(fname)[1].lower()
        if ext in _IMAGE_EXTENSIONS:
            images.append({
                "name": fname,
                "url": f"/output/{urllib.parse.quote(safe)}/{urllib.parse.quote(fname)}",
                "path": os.path.join(channel_dir, fname).replace("\\", "/"),
            })
    return images
