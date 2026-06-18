"""
YouTube audio + thumbnail downloader using yt-dlp.
Downloads MP3 audio and thumbnail images from YouTube videos,
organized into per-channel subfolders.
"""

import yt_dlp
import os
import re
import io
import glob
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from PIL import Image

from modules.ffmpeg_utils import get_ffmpeg_dir


def sanitize_filename(name):
    """Remove characters that cause filesystem issues."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = name.strip('. ')
    return name[:200]


def _download_thumbnail(url, save_path):
    """Download thumbnail from *url* and save as a JPEG at *save_path*."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data))
        img.convert('RGB').save(save_path, 'JPEG', quality=90)
        return True
    except Exception:
        return False


def _channel_videos_url(channel_url, sort_by):
    """Build a channel videos listing URL with YouTube's own sort order."""
    parts = urlsplit(channel_url)
    path = parts.path.rstrip('/')

    if '/videos' not in path:
        path = path + '/videos'

    query = 'view=0&sort=p&flow=grid' if sort_by == 'popular' else 'view=0&sort=dd&flow=grid'
    return urlunsplit((parts.scheme, parts.netloc, path, query, ''))


def _apply_cookie_options(ydl_opts, allow_auto=False):
    """
    Apply optional yt-dlp cookie configuration.

    Environment:
      YT_DLP_COOKIES_FILE=/path/to/cookies.txt
      YT_DLP_COOKIES_FROM_BROWSER=firefox or firefox:profile-name
    """
    cookies_file = os.environ.get("YT_DLP_COOKIES_FILE")
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
        return

    browser_spec = os.environ.get("YT_DLP_COOKIES_FROM_BROWSER")
    if browser_spec:
        parts = [p.strip() for p in browser_spec.split(":") if p.strip()]
        if len(parts) == 1:
            ydl_opts["cookiesfrombrowser"] = (parts[0],)
        elif len(parts) >= 2:
            ydl_opts["cookiesfrombrowser"] = (parts[0], parts[1])
        return

    if allow_auto and os.name == "nt":
        firefox_profiles = os.path.join(
            os.environ.get("APPDATA", ""),
            "Mozilla",
            "Firefox",
            "Profiles",
        )
        if os.path.isdir(firefox_profiles):
            # Only enable if at least one profile has a cookies.sqlite database.
            # Firefox may be installed but never opened, leaving no cookie DB.
            cookie_dbs = glob.glob(
                os.path.join(firefox_profiles, "*", "cookies.sqlite")
            )
            if cookie_dbs:
                ydl_opts["cookiesfrombrowser"] = ("firefox",)


def fetch_channel_videos(channel_url, sort_by='latest', limit=10):
    """
    Fetch video list from a YouTube channel.

    Args:
        channel_url: YouTube channel URL (e.g. https://www.youtube.com/@name)
        sort_by: 'latest' (by upload date) or 'popular' (by view count)
        limit: Maximum number of videos to return

    Returns:
        Dict with 'channel_name' (str) and 'videos' (list of dicts).
        Each video dict: id, title, url, duration, view_count, upload_date.
    """
    if not channel_url.startswith('http'):
        channel_url = 'https://www.youtube.com/' + channel_url

    channel_url = _channel_videos_url(channel_url, sort_by)
    fetch_limit = limit if sort_by == 'latest' else max(limit, 50)

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'playlistend': fetch_limit,
        'ignoreerrors': True,
    }
    _apply_cookie_options(ydl_opts, allow_auto=False)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    if not info:
        raise ValueError("Could not fetch channel info. Check the URL.")

    channel_name = sanitize_filename(
        info.get('channel')
        or info.get('uploader')
        or info.get('title')
        or 'Unknown Channel'
    )

    entries = info.get('entries', [])
    videos = []

    for entry in entries:
        if entry is None:
            continue
        video_id = entry.get('id', '')
        video_url = entry.get('webpage_url') or entry.get('url') or ''
        if video_url and not video_url.startswith('http'):
            video_url = f"https://www.youtube.com/watch?v={video_id or video_url}"
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
        videos.append({
            'id': video_id,
            'title': entry.get('title', 'Unknown'),
            'url': video_url,
            'duration': entry.get('duration'),
            'view_count': entry.get('view_count'),
            'upload_date': entry.get('upload_date'),
        })

    if sort_by == 'popular' and any(v.get('view_count') is not None for v in videos):
        videos.sort(key=lambda x: x.get('view_count') or 0, reverse=True)

    return {
        'channel_name': channel_name,
        'videos': videos[:limit],
    }


def fetch_video_metadata(video_url):
    """Fetch title/description/tags for one YouTube video without downloading media."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': False,
    }
    _apply_cookie_options(ydl_opts, allow_auto=True)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    if not info:
        raise ValueError("Could not fetch video metadata.")

    tags = info.get('tags') or []
    if not isinstance(tags, list):
        tags = []

    return {
        'title': info.get('title', 'Unknown') or 'Unknown',
        'description': info.get('description', '') or '',
        'tags': [str(tag).strip() for tag in tags if str(tag).strip()],
        'video_id': info.get('id', '') or '',
        'source_url': info.get('webpage_url') or video_url,
    }


def find_video_url_by_title(channel_name, title, limit=10):
    """Best-effort lookup for older items that have a title but no stored video ID/URL."""
    search_query = f"ytsearch{limit}:{title} {channel_name}".strip()
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
        'playlistend': limit,
    }
    _apply_cookie_options(ydl_opts, allow_auto=True)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(search_query, download=False)

    entries = info.get('entries', []) if isinstance(info, dict) else []
    normalized_title = sanitize_filename(title).casefold()
    normalized_channel = sanitize_filename(channel_name).casefold()

    def score(entry):
        entry_title = sanitize_filename(entry.get('title', '')).casefold()
        entry_channel = sanitize_filename(
            entry.get('channel') or entry.get('uploader') or ''
        ).casefold()
        exact_title = int(entry_title == normalized_title)
        channel_match = int(entry_channel == normalized_channel)
        title_contains = int(normalized_title and normalized_title in entry_title)
        return (exact_title, channel_match, title_contains)

    ranked = sorted((entry for entry in entries if entry), key=score, reverse=True)
    if not ranked:
        return ""

    top = ranked[0]
    video_id = top.get('id', '')
    video_url = top.get('webpage_url') or top.get('url') or ''
    if video_url and not video_url.startswith('http') and video_id:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
    if not video_url and video_id:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
    return video_url


def download_video_assets(video_url, channel_name, output_base_dir):
    """
    Download audio (MP3) **and** thumbnail (JPG) for a YouTube video.

    Files are saved to ``output_base_dir/<channel_name>/`` using the
    video title as the base filename.

    Args:
        video_url: YouTube video URL
        channel_name: Name of the channel (used as subfolder)
        output_base_dir: Root output directory

    Returns:
        Dict with title, description, tags, video_id, audio_filename, thumb_filename,
        channel_name, duration.
    """
    safe_channel = sanitize_filename(channel_name)
    channel_dir = os.path.join(output_base_dir, safe_channel)
    os.makedirs(channel_dir, exist_ok=True)

    # ── 1. Download audio via yt-dlp ─────────────────────────
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.join(channel_dir, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'restrictfilenames': False,
        'windowsfilenames': True,
    }
    ffmpeg_dir = get_ffmpeg_dir()
    if ffmpeg_dir:
        ydl_opts['ffmpeg_location'] = ffmpeg_dir
    _apply_cookie_options(ydl_opts, allow_auto=True)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        title = info.get('title', 'Unknown')
        filename = ydl.prepare_filename(info)
        mp3_path = os.path.splitext(filename)[0] + '.mp3'

    audio_basename = os.path.basename(mp3_path)
    base_name = os.path.splitext(audio_basename)[0]

    # ── 2. Download thumbnail ────────────────────────────────
    thumb_basename = None
    thumbnail_url = info.get('thumbnail', '')

    if not thumbnail_url:
        thumbnails = info.get('thumbnails', [])
        if thumbnails:
            thumbnail_url = thumbnails[-1].get('url', '')

    # Fallback: construct from video ID
    if not thumbnail_url:
        video_id = info.get('id', '')
        if video_id:
            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    if thumbnail_url:
        thumb_basename = base_name + '.jpg'
        thumb_path = os.path.join(channel_dir, thumb_basename)
        if not _download_thumbnail(thumbnail_url, thumb_path):
            thumb_basename = None

    return {
        'title': title,
        'description': info.get('description', '') or '',
        'tags': [str(tag).strip() for tag in (info.get('tags') or []) if str(tag).strip()],
        'video_id': info.get('id', ''),
        'source_url': info.get('webpage_url') or video_url,
        'audio_filename': audio_basename,
        'thumb_filename': thumb_basename,
        'channel_name': safe_channel,
        'duration': info.get('duration'),
    }
