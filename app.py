"""
YT Automator — FastAPI server.
Serves the web UI and exposes API endpoints for:
  - Fetching channel video lists
  - Downloading audio + thumbnails (organized by channel)
  - Image library management
  - Video render queue (background, sequential)
  - Thumbnail generation (synchronous)
  - Browsing downloaded channel content (Studio)
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
import uvicorn
import os
import subprocess
import uuid
import shutil
import glob
import json
import threading
import urllib.parse
import time

from modules.downloader import (
    fetch_channel_videos,
    fetch_video_metadata,
    find_video_url_by_title,
    download_video_assets,
    sanitize_filename,
)
from modules.video_creator import create_video
from modules.thumbnail import generate_thumbnail, get_available_fonts
from modules.render_queue import render_queue
from modules.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path
from modules.image_gallery import (
    record_usage,
    get_usage_history,
    get_grouped_library,
    browse_folder,
    get_browse_roots,
    get_channel_images,
)

app = FastAPI(title="YT Automator")

@app.on_event("startup")
def startup_check():
    """Verify that ffmpeg and ffprobe are available in the PATH."""
    ffmpeg_path = get_ffmpeg_path()
    ffprobe_path = get_ffprobe_path()
    ffmpeg_found = shutil.which(ffmpeg_path) is not None or os.path.exists(ffmpeg_path)
    ffprobe_found = shutil.which(ffprobe_path) is not None or os.path.exists(ffprobe_path)

    print("\n" + "=" * 60)
    print("YT AUTOMATOR STARTUP VERIFICATION")
    print(f"  - ffmpeg:  {'FOUND' if ffmpeg_found else 'NOT FOUND (WARNING)'} ({ffmpeg_path})")
    print(f"  - ffprobe: {'FOUND' if ffprobe_found else 'NOT FOUND (WARNING)'} ({ffprobe_path})")
    if not (ffmpeg_found and ffprobe_found):
        print("\n  [WARNING] ffmpeg or ffprobe were not detected in your system's PATH.")
        print("  Please make sure they are installed to run video encoding processes.")
    else:
        print("  System environment verified successfully!")
    print("=" * 60 + "\n")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LIBRARY_DIR = os.path.join(OUTPUT_DIR, "library")
HISTORY_FILE = os.path.join(OUTPUT_DIR, "download_history.json")
STATUS_FILE = os.path.join(OUTPUT_DIR, "item_status.json")
HISTORY_LOCK = threading.Lock()
STATUS_LOCK = threading.Lock()

os.makedirs(LIBRARY_DIR, exist_ok=True)


def _load_download_history():
    """Return the server-side set of downloaded YouTube video IDs."""
    with HISTORY_LOCK:
        if not os.path.exists(HISTORY_FILE):
            return set()
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data if isinstance(data, list) else data.get("video_ids", []))
        except Exception:
            return set()


def _save_download_history(video_ids):
    with HISTORY_LOCK:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        tmp_path = HISTORY_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(sorted(video_ids), f, indent=2)
        os.replace(tmp_path, HISTORY_FILE)


def _mark_downloaded(video_id):
    if not video_id:
        return
    video_ids = _load_download_history()
    video_ids.add(video_id)
    _save_download_history(video_ids)


def _empty_item_status():
    return {"channels": {}}


def _read_item_status_unlocked():
    if not os.path.exists(STATUS_FILE):
        return _empty_item_status()
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_item_status()
        data.setdefault("channels", {})
        return data
    except Exception:
        return _empty_item_status()


def _write_item_status_unlocked(data):
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    tmp_path = STATUS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, STATUS_FILE)


def _load_item_status():
    with STATUS_LOCK:
        return _read_item_status_unlocked()


def _file_base(filename):
    return os.path.splitext(os.path.basename(filename or ""))[0]


def _resolve_item_local_paths(channel_name, files):
    safe_channel = sanitize_filename(channel_name)
    channel_dir = os.path.join(OUTPUT_DIR, safe_channel)
    candidates = {}

    for key in ("audio", "video", "thumbnail", "custom_thumb"):
        filename = os.path.basename(files.get(key, ""))
        if filename:
            candidates[key] = os.path.normpath(os.path.join(channel_dir, filename))

    primary_path = (
        candidates.get("video")
        or candidates.get("audio")
        or candidates.get("custom_thumb")
        or candidates.get("thumbnail")
    )
    return candidates, primary_path


def _upsert_item_status(channel_name, base_name, **fields):
    safe_channel = sanitize_filename(channel_name)
    base_name = base_name or fields.get("title") or "Unknown"
    now = time.time()

    with STATUS_LOCK:
        data = _read_item_status_unlocked()
        channel = data["channels"].setdefault(safe_channel, {"items": {}})
        items = channel.setdefault("items", {})
        item = items.setdefault(base_name, {
            "base_name": base_name,
            "title": base_name,
            "downloaded": False,
            "rendered": False,
            "uploaded": False,
            "created_at": now,
        })

        for key, value in fields.items():
            if value is not None:
                item[key] = value
        item["base_name"] = base_name
        item["updated_at"] = now
        _write_item_status_unlocked(data)
        return dict(item)


def _mark_item_downloaded(result):
    audio_filename = result.get("audio_filename")
    base_name = _file_base(audio_filename) or result.get("title") or result.get("video_id")
    if not base_name:
        return
    _upsert_item_status(
        result.get("channel_name", ""),
        base_name,
        title=result.get("title") or base_name,
        description=result.get("description") or "",
        tags=result.get("tags") or [],
        video_id=result.get("video_id"),
        source_url=result.get("source_url") or "",
        downloaded=True,
        audio_filename=audio_filename,
        thumb_filename=result.get("thumb_filename"),
    )


def _mark_item_rendered(channel_name, base_name, video_filename):
    _upsert_item_status(
        channel_name,
        base_name,
        rendered=True,
        video_filename=video_filename,
    )


def _set_item_uploaded(channel_name, base_name, uploaded):
    return _upsert_item_status(
        channel_name,
        base_name,
        uploaded=bool(uploaded),
    )


def _sync_item_metadata(channel_name, item_key):
    safe_channel = sanitize_filename(channel_name)
    status_data = _load_item_status()
    manifest = (
        status_data.get("channels", {})
        .get(safe_channel, {})
        .get("items", {})
        .get(item_key, {})
    )
    if not manifest:
        raise HTTPException(status_code=404, detail="Item not found")

    video_url = manifest.get("source_url") or ""
    if not video_url:
        video_id = manifest.get("video_id") or ""
        if video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
    if not video_url:
        video_url = find_video_url_by_title(
            safe_channel,
            manifest.get("title") or item_key,
        )
    if not video_url:
        raise HTTPException(status_code=400, detail="Could not determine the source video for this item")

    meta = fetch_video_metadata(video_url)
    return _upsert_item_status(
        safe_channel,
        item_key,
        title=meta.get("title") or manifest.get("title") or item_key,
        description=meta.get("description") or "",
        tags=meta.get("tags") or [],
        video_id=meta.get("video_id") or manifest.get("video_id") or "",
        source_url=meta.get("source_url") or video_url,
    )


def _scan_channel_files(safe_name):
    channel_dir = os.path.join(OUTPUT_DIR, safe_name)
    items_map: dict[str, dict] = {}

    if not os.path.exists(channel_dir):
        return items_map

    for f in os.listdir(channel_dir):
        name, ext = os.path.splitext(f)
        ext_lower = ext.lower()

        if name.endswith("_thumb") and ext_lower in (".png", ".jpg", ".jpeg"):
            base = name[:-6]
            items_map.setdefault(base, {"title": base, "files": {}})
            items_map[base]["files"]["custom_thumb"] = f

        elif ext_lower == ".mp3":
            items_map.setdefault(name, {"title": name, "files": {}})
            items_map[name]["files"]["audio"] = f

        elif ext_lower in (".jpg", ".jpeg") and not name.endswith("_thumb"):
            items_map.setdefault(name, {"title": name, "files": {}})
            items_map[name]["files"]["thumbnail"] = f

        elif ext_lower == ".mp4":
            items_map.setdefault(name, {"title": name, "files": {}})
            items_map[name]["files"]["video"] = f

    return items_map


def _sync_disk_items_to_status(safe_name, disk_items):
    for base_name, item in disk_items.items():
        files = item.get("files", {})
        fields = {}
        if files.get("audio"):
            fields["downloaded"] = True
            fields["audio_filename"] = files["audio"]
        if files.get("thumbnail"):
            fields["thumb_filename"] = files["thumbnail"]
        if files.get("custom_thumb"):
            fields["custom_thumb_filename"] = files["custom_thumb"]
        if files.get("video"):
            fields["rendered"] = True
            fields["video_filename"] = files["video"]
        if fields:
            _upsert_item_status(safe_name, base_name, **fields)


def _build_channel_items(channel_name):
    safe_name = sanitize_filename(channel_name)
    disk_items = _scan_channel_files(safe_name)
    _sync_disk_items_to_status(safe_name, disk_items)

    status_data = _load_item_status()
    manifest_items = (
        status_data.get("channels", {})
        .get(safe_name, {})
        .get("items", {})
    )

    result = []
    for base_name in sorted(set(manifest_items) | set(disk_items)):
        manifest = manifest_items.get(base_name, {})
        disk = disk_items.get(base_name, {"title": base_name, "files": {}})
        files = disk.get("files", {})
        local_paths, primary_path = _resolve_item_local_paths(safe_name, files)

        downloaded = bool(manifest.get("downloaded") or files.get("audio"))
        rendered = bool(manifest.get("rendered") or files.get("video"))
        uploaded = bool(manifest.get("uploaded"))
        local_audio = bool(files.get("audio"))
        local_video = bool(files.get("video"))
        local_available = bool(local_video if rendered else local_audio)

        if not (downloaded or rendered or uploaded or files):
            continue

        result.append({
            "key": base_name,
            "title": manifest.get("title") or disk.get("title") or base_name,
            "description": manifest.get("description") or "",
            "tags": manifest.get("tags") or [],
            "files": files,
            "paths": {
                "primary": primary_path,
                "audio": local_paths.get("audio"),
                "video": local_paths.get("video"),
                "thumbnail": local_paths.get("thumbnail"),
                "custom_thumb": local_paths.get("custom_thumb"),
            },
            "status": {
                "downloaded": downloaded,
                "rendered": rendered,
                "local_available": local_available,
                "uploaded": uploaded,
            },
        })

    return result


# ─── Serve UI ────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """Serve the main web UI."""
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


# ─── Channel Fetch & Download ────────────────────────────────────────────────

@app.post("/api/fetch-channel")
def api_fetch_channel(
    channel_url: str = Form(...),
    sort_by: str = Form("latest"),
    limit: int = Form(10),
):
    """Fetch video list from a YouTube channel."""
    try:
        result = fetch_channel_videos(channel_url, sort_by, limit)
        return {
            "status": "ok",
            "channel_name": result["channel_name"],
            "videos": result["videos"],
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )


@app.post("/api/download")
def api_download(
    video_url: str = Form(...),
    channel_name: str = Form(...),
):
    """Download audio + thumbnail for a single video into the channel folder."""
    try:
        result = download_video_assets(video_url, channel_name, OUTPUT_DIR)
        _mark_downloaded(result.get("video_id"))
        _mark_item_downloaded(result)
        return {"status": "ok", "result": result}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )


@app.get("/api/download-history")
def api_download_history():
    """Return server-side downloaded video IDs for cross-browser persistence."""
    return {"status": "ok", "video_ids": sorted(_load_download_history())}


# ─── Image Library ───────────────────────────────────────────────────────────

@app.get("/api/library")
def api_library():
    """List all images in the reusable image library."""
    files = []
    for ext in ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif"]:
        for f in glob.glob(os.path.join(LIBRARY_DIR, ext)):
            name = os.path.basename(f)
            files.append({
                "name": name,
                "url": "/output/library/" + urllib.parse.quote(name),
            })
    return {"status": "ok", "files": files}


@app.post("/api/library/upload")
def api_upload_library(file: UploadFile = File(...)):
    """Upload an image to the library for reuse."""
    try:
        safe_filename = os.path.basename(file.filename.replace("\\", "/"))
        safe_name = f"{uuid.uuid4().hex[:8]}_{safe_filename}"
        path = os.path.join(LIBRARY_DIR, safe_name)
        with open(path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        return {
            "status": "ok",
            "result": {
                "name": safe_name,
                "url": f"/output/library/{urllib.parse.quote(safe_name)}",
            },
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )


@app.delete("/api/library/{filename}")
def api_delete_library(filename: str):
    """Remove an image from the library."""
    path = os.path.join(LIBRARY_DIR, os.path.basename(filename))
    if os.path.exists(path):
        os.remove(path)
        return {"status": "ok"}
    return JSONResponse(
        status_code=404,
        content={"status": "error", "message": "File not found"},
    )


# ─── Image Gallery ───────────────────────────────────────────────────────────

@app.get("/api/gallery/grouped")
def api_gallery_grouped(channel: str = ""):
    """Return library images grouped by usage category."""
    return {
        "status": "ok",
        **get_grouped_library(OUTPUT_DIR, channel or None),
    }


@app.get("/api/gallery/usage")
def api_gallery_usage(channel: str = ""):
    """Return image-usage history, optionally filtered by channel."""
    return {
        "status": "ok",
        "records": get_usage_history(OUTPUT_DIR, channel or None),
    }


@app.post("/api/gallery/browse")
def api_gallery_browse(path: str = Form(...)):
    """Browse a folder on disk and return its image files."""
    result = browse_folder(path)
    if "error" in result:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": result["error"]},
        )
    return {"status": "ok", **result}


@app.get("/api/gallery/browse/roots")
def api_gallery_roots():
    """Return quick-access folder roots for the gallery browser."""
    return {"status": "ok", "roots": get_browse_roots(OUTPUT_DIR)}


@app.get("/api/gallery/channel-images/{channel_name}")
def api_gallery_channel_images(channel_name: str):
    """List images inside a channel folder."""
    safe = sanitize_filename(channel_name)
    return {"status": "ok", "images": get_channel_images(OUTPUT_DIR, safe)}


# ─── Channels / Studio ──────────────────────────────────────────────────────

@app.get("/api/channels")
def api_channels():
    _SKIP = {"library", "audio", "videos", "thumbnails", "uploads"}
    names = set()
    if os.path.exists(OUTPUT_DIR):
        for name in sorted(os.listdir(OUTPUT_DIR)):
            dir_path = os.path.join(OUTPUT_DIR, name)
            if os.path.isdir(dir_path) and name not in _SKIP:
                names.add(name)

    status_data = _load_item_status()
    names.update(status_data.get("channels", {}).keys())

    channels = []
    for name in sorted(names):
        if name in _SKIP:
            continue
        items = _build_channel_items(name)
        if items:
            channels.append({"name": name, "count": len(items)})
    return {"status": "ok", "channels": channels}

    """List all channel folders that contain downloaded audio."""
    # Legacy folders from the old flat layout — skip them
    _SKIP = {"library", "audio", "videos", "thumbnails", "uploads"}
    channels = []
    if os.path.exists(OUTPUT_DIR):
        for name in sorted(os.listdir(OUTPUT_DIR)):
            dir_path = os.path.join(OUTPUT_DIR, name)
            if os.path.isdir(dir_path) and name not in _SKIP:
                mp3_count = len(glob.glob(os.path.join(dir_path, "*.mp3")))
                if mp3_count > 0:
                    channels.append({"name": name, "count": mp3_count})
    return {"status": "ok", "channels": channels}


@app.get("/api/channels/{channel_name}")
def api_channel_files(channel_name: str):
    safe_name = sanitize_filename(channel_name)
    return {"status": "ok", "channel_name": safe_name, "items": _build_channel_items(safe_name)}

    """
    List all items in a channel folder, grouped by base filename.
    An "item" is any set of files sharing a base name:
    audio (.mp3), thumbnail (.jpg), video (.mp4), custom thumb (_thumb.png).
    """
    safe_name = sanitize_filename(channel_name)
    channel_dir = os.path.join(OUTPUT_DIR, safe_name)

    if not os.path.exists(channel_dir):
        return {"status": "ok", "channel_name": safe_name, "items": []}

    files = os.listdir(channel_dir)
    items_map: dict[str, dict] = {}

    for f in files:
        name, ext = os.path.splitext(f)
        ext_lower = ext.lower()

        # Custom thumbnail: <title>_thumb.png
        if name.endswith("_thumb") and ext_lower in (".png", ".jpg", ".jpeg"):
            base = name[:-6]
            items_map.setdefault(base, {"title": base, "files": {}})
            items_map[base]["files"]["custom_thumb"] = f

        elif ext_lower == ".mp3":
            items_map.setdefault(name, {"title": name, "files": {}})
            items_map[name]["files"]["audio"] = f

        elif ext_lower in (".jpg", ".jpeg") and not name.endswith("_thumb"):
            items_map.setdefault(name, {"title": name, "files": {}})
            items_map[name]["files"]["thumbnail"] = f

        elif ext_lower == ".mp4":
            items_map.setdefault(name, {"title": name, "files": {}})
            items_map[name]["files"]["video"] = f

    # Only include items that have at least an audio file
    result = [
        items_map[k]
        for k in sorted(items_map)
        if "audio" in items_map[k].get("files", {})
    ]

    return {"status": "ok", "channel_name": safe_name, "items": result}


# ─── Render Queue ────────────────────────────────────────────────────────────

@app.post("/api/item/upload-status")
def api_item_upload_status(
    channel_name: str = Form(...),
    item_key: str = Form(...),
    uploaded: bool = Form(...),
):
    """Manually mark whether an item has been uploaded to YouTube."""
    safe_channel = sanitize_filename(channel_name)
    item = _set_item_uploaded(safe_channel, item_key, uploaded)
    return {
        "status": "ok",
        "item": {
            "key": item.get("base_name", item_key),
            "uploaded": bool(item.get("uploaded")),
        },
    }


@app.post("/api/item/open-folder")
def api_item_open_folder(
    channel_name: str = Form(...),
    item_key: str = Form(...),
):
    """Reveal the local item in Windows Explorer."""
    safe_channel = sanitize_filename(channel_name)
    items = _build_channel_items(safe_channel)
    item = next((entry for entry in items if entry.get("key") == item_key), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    primary_path = (item.get("paths") or {}).get("primary")
    if not primary_path or not os.path.exists(primary_path):
        raise HTTPException(status_code=404, detail="Local file not found")

    subprocess.Popen(["explorer.exe", f"/select,{os.path.normpath(primary_path)}"])
    return {"status": "ok", "path": primary_path}


@app.post("/api/item/sync-metadata")
def api_item_sync_metadata(
    channel_name: str = Form(...),
    item_key: str = Form(...),
):
    """Refresh saved title/description/tags for an existing item."""
    item = _sync_item_metadata(channel_name, item_key)
    return {
        "status": "ok",
        "item": {
            "key": item.get("base_name", item_key),
            "title": item.get("title") or item_key,
            "description": item.get("description") or "",
            "tags": item.get("tags") or [],
        },
    }


@app.post("/api/item/save-metadata")
def api_item_save_metadata(
    channel_name: str = Form(...),
    item_key: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
):
    """Manually save the updated title and description for an item."""
    safe_channel = sanitize_filename(channel_name)
    item = _upsert_item_status(
        safe_channel,
        item_key,
        title=title,
        description=description,
    )
    return {
        "status": "ok",
        "item": {
            "key": item.get("base_name", item_key),
            "title": item.get("title"),
            "description": item.get("description"),
        },
    }


@app.post("/api/item/rewrite-metadata")
def api_item_rewrite_metadata(
    text: str = Form(...),
    field_type: str = Form(...),
):
    """Rewrite title or description using Gemini 2.5 Flash."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY environment variable is not configured",
        )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    if field_type == "title":
        prompt = (
            "Rewrite the following YouTube video title to make it more engaging, clickable, "
            "and optimized for viewers, while keeping it concise. Return ONLY the rewritten title, "
            f"with absolutely no surrounding quotes, markdown, explanations, or extra text:\n\n{text}"
        )
    else:
        prompt = (
            "Rewrite the following YouTube video description to make it engaging, professional, "
            "and optimized for SEO. Return ONLY the rewritten description, with absolutely no surrounding quotes, "
            f"markdown, explanations, or extra text:\n\n{text}"
        )

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }

    import urllib.request
    import json
    
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            rewritten_text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Remove any wrapping quotes or code block formatting if returned by LLM
            if rewritten_text.startswith("`") and rewritten_text.endswith("`"):
                rewritten_text = rewritten_text.strip("`").strip()
            if rewritten_text.startswith('"') and rewritten_text.endswith('"'):
                rewritten_text = rewritten_text.strip('"').strip()
            if rewritten_text.startswith("'") and rewritten_text.endswith("'"):
                rewritten_text = rewritten_text.strip("'").strip()
            return {"status": "ok", "rewritten": rewritten_text}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Gemini API error: {str(e)}"},
        )



@app.post("/api/queue/video")
def api_queue_video(
    channel_name: str = Form(...),
    audio_file: str = Form(...),
    library_images: str = Form(""),
    library_image: str = Form(""),
):
    """
    Queue a video render job.
    Combines the given audio file (from the channel folder) with the
    selected library image to produce an MP4 in the same channel folder.
    """
    safe_channel = sanitize_filename(channel_name)
    safe_audio = os.path.basename(audio_file)

    audio_path = os.path.join(OUTPUT_DIR, safe_channel, safe_audio)
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=400, detail="Audio file not found")

    raw_images = (library_images or library_image).strip()
    try:
        parsed_images = json.loads(raw_images) if raw_images.startswith("[") else raw_images.split(",")
    except Exception:
        parsed_images = raw_images.split(",")

    import shutil
    image_names_for_usage = []
    image_paths = []
    for loc in (str(x).strip() for x in parsed_images if str(x).strip()):
        if os.path.isabs(loc) and os.path.exists(loc):
            basename = os.path.basename(loc)
            dest = os.path.join(LIBRARY_DIR, basename)
            if not os.path.exists(dest):
                shutil.copy2(loc, dest)
            image_paths.append(dest)
            image_names_for_usage.append(basename)
        else:
            basename = os.path.basename(loc)
            p = os.path.join(LIBRARY_DIR, basename)
            if not os.path.exists(p):
                raise HTTPException(status_code=400, detail=f"Library image not found: {basename}")
            image_paths.append(p)
            image_names_for_usage.append(basename)

    if not image_paths:
        raise HTTPException(status_code=400, detail="Select at least one library image")

    base_name = os.path.splitext(safe_audio)[0]
    out_name = base_name + ".mp4"
    out_path = os.path.join(OUTPUT_DIR, safe_channel, out_name)
    title = base_name

    def do_render(job):
        def on_start(proc):
            with render_queue._lock:
                job.process = proc

        def on_progress(percent):
            with render_queue._lock:
                job.progress = max(job.progress, percent)

        create_video(
            audio_path,
            image_paths,
            out_path,
            on_process_start=on_start,
            on_progress=on_progress,
        )
        _mark_item_rendered(safe_channel, base_name, out_name)
        return {
            "filename": out_name,
            "url": (
                f"/output/{urllib.parse.quote(safe_channel)}"
                f"/{urllib.parse.quote(out_name)}"
            ),
        }

    # Record image usage for gallery tracking
    for img_name in image_names_for_usage:
        record_usage(OUTPUT_DIR, img_name, "render", safe_channel, title)

    job = render_queue.add_job("video", title, safe_channel, do_render)
    return {"status": "ok", "job": job}


@app.get("/api/queue")
def api_queue_status():
    """Return the current state of the render queue."""
    return {"status": "ok", "jobs": render_queue.get_all_jobs()}


@app.post("/api/queue/clear")
def api_queue_clear():
    """Remove completed / failed jobs from the queue list."""
    render_queue.clear_completed()
    return {"status": "ok"}


@app.post("/api/queue/{job_id}/cancel")
def api_queue_cancel(job_id: str):
    """Cancel a pending or rendering job."""
    success = render_queue.cancel_job(job_id)
    if success:
        return {"status": "ok", "message": "Job cancellation initiated"}
    raise HTTPException(status_code=400, detail="Job not found or already finished")


# ─── Thumbnail Generation (synchronous — fast Pillow operation) ──────────────

@app.post("/api/queue/{job_id}/retry")
def api_queue_retry(job_id: str):
    """Retry a failed render job using its original inputs."""
    job = render_queue.retry_job(job_id)
    if job:
        return {"status": "ok", "job": job}
    raise HTTPException(status_code=400, detail="Job not found or not retryable")


@app.get("/api/fonts")
def api_fonts():
    """Return available thumbnail font names detected on this system."""
    return {"status": "ok", "fonts": get_available_fonts()}


@app.get("/api/fonts/{font_name}/file")
def api_font_file(font_name: str):
    """Serve the raw .ttf file for a font by its display name, so the browser
    can load it via the FontFace API for the canvas live preview."""
    from modules.thumbnail import FONT_CATALOGUE
    path = FONT_CATALOGUE.get(font_name)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Font not found")
    return FileResponse(path, media_type="font/ttf", headers={
        "Cache-Control": "public, max-age=86400",
    })


@app.post("/api/create-thumbnail")
def api_create_thumbnail(
    channel_name: str = Form(...),
    audio_file: str = Form(...),
    library_image: str = Form(...),
    text: str = Form(...),
    highlighted_words: str = Form(""),
    font_size: int = Form(72),
    pos_x: float = Form(50),
    pos_y: float = Form(72),
    font_name: str = Form("Impact"),
    text_color: str = Form("#ffffff"),
    highlight_color: str = Form("#ffd636"),
    box_color: str = Form("#000000"),
    box_opacity: float = Form(0.70),
    box_width: int = Form(52),
    fit_box: bool = Form(True),
    line_scales: str = Form("[]"),
    shadow_mode: str = Form("both"),
    text_align: str = Form("center"),
):
    """
    Generate a styled thumbnail PNG and save it alongside the audio.
    Uses ``<title>_thumb.png`` to avoid clashing with the original
    YouTube thumbnail already stored as ``<title>.jpg``.
    """
    safe_channel = sanitize_filename(channel_name)

    import shutil
    loc = library_image.strip()
    if os.path.isabs(loc) and os.path.exists(loc):
        basename = os.path.basename(loc)
        bg_path = os.path.join(LIBRARY_DIR, basename)
        if not os.path.exists(bg_path):
            shutil.copy2(loc, bg_path)
        image_name_for_usage = basename
    else:
        basename = os.path.basename(loc)
        bg_path = os.path.join(LIBRARY_DIR, basename)
        if not os.path.exists(bg_path):
            raise HTTPException(status_code=400, detail="Background image not found")
        image_name_for_usage = basename

    highlights = [w.strip() for w in highlighted_words.split(",") if w.strip()]
    try:
        import json
        parsed_line_scales = json.loads(line_scales or "[]")
        if not isinstance(parsed_line_scales, list):
            parsed_line_scales = []
    except Exception:
        parsed_line_scales = []

    base_name = os.path.splitext(os.path.basename(audio_file))[0]
    out_name = base_name + "_thumb.png"
    out_path = os.path.join(OUTPUT_DIR, safe_channel, out_name)

    try:
        generate_thumbnail(
            bg_path, text, highlights, out_path,
            font_size=font_size,
            pos_x=pos_x,
            pos_y=pos_y,
            font_name=font_name,
            text_color=text_color,
            highlight_color=highlight_color,
            box_color=box_color,
            box_opacity=box_opacity,
            box_width=box_width,
            fit_box=fit_box,
            line_scales=parsed_line_scales,
            shadow_mode=shadow_mode,
            text_align=text_align,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )

    # Record image usage for gallery tracking
    record_usage(OUTPUT_DIR, image_name_for_usage, "thumbnail", safe_channel, base_name)

    return {
        "status": "ok",
        "result": {
            "filename": out_name,
            "url": (
                f"/output/{urllib.parse.quote(safe_channel)}"
                f"/{urllib.parse.quote(out_name)}"
            ),
        },
    }


# ─── Serve Browsed Images (outside library) ─────────────────────────────────

@app.get("/api/gallery/serve-file")
def api_gallery_serve_file(path: str):
    """Serve an image file from an arbitrary path (read-only, images only)."""
    clean = os.path.normpath(os.path.abspath(path))
    if not os.path.isfile(clean):
        raise HTTPException(status_code=404, detail="File not found")
    ext = os.path.splitext(clean)[1].lower()
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Not an image file")
    return FileResponse(clean)


# ─── Static File Serving (must be after API routes) ──────────────────────────

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "static")),
    name="static",
)


if __name__ == "__main__":
    print("\n  YT Automator running at http://127.0.0.1:8000\n")
    uvicorn.run("app:app", host="127.0.0.1", port=8000)
