"""FFmpeg path helpers.

The system FFmpeg is preferred because the bundled FFmpeg 4.2.2 binary
has NVENC preset incompatibilities with modern NVIDIA drivers (522+).
The bundled binary is kept as a fallback in case system ffmpeg is absent.
"""

import os
import shutil


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED_FFMPEG = os.path.join(BASE_DIR, "tools", "ffmpeg", "ffmpeg.exe")


def get_ffmpeg_path():
    """Return the FFmpeg executable path to use for encoding/conversion."""
    env_path = os.environ.get("YT_AUTOMATOR_FFMPEG")
    if env_path and os.path.exists(env_path):
        return env_path
    # Prefer system ffmpeg — bundled 4.2.2 has NVENC preset issues with modern drivers
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    if os.path.exists(BUNDLED_FFMPEG):
        return BUNDLED_FFMPEG
    return "ffmpeg"


def get_ffmpeg_dir():
    """Return a directory suitable for yt-dlp's ffmpeg_location option."""
    path = get_ffmpeg_path()
    return os.path.dirname(path) if os.path.isabs(path) else None


def get_ffprobe_path():
    """Return ffprobe. The system ffprobe is fine for probing durations."""
    env_path = os.environ.get("YT_AUTOMATOR_FFPROBE")
    if env_path and os.path.exists(env_path):
        return env_path
    bundled_dir = get_ffmpeg_dir()
    if bundled_dir:
        bundled_probe = os.path.join(bundled_dir, "ffprobe.exe")
        if os.path.exists(bundled_probe):
            return bundled_probe
    return shutil.which("ffprobe") or "ffprobe"
