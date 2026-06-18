"""
Video creator using FFmpeg.
Combines audio files with image overlays to produce MP4 videos.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading


from PIL import Image

from modules.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path


VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30
VIDEO_BITRATE = "15M"
MAX_BITRATE = "20M"
BUFFER_SIZE = "30M"
AUDIO_BITRATE = "320k"


# Preset names changed in the new NVENC SDK (requires driver 522+):
#   old: hq / vbr_hq  →  new: p7 / vbr
# The bundled FFmpeg 4.2.2 is incompatible with current drivers; use system ffmpeg.
#
# p7 is the slowest/highest-quality NVENC preset – it pushes the fixed-function
# encoder harder per frame.  multipass=fullres runs an internal two-pass encode
# for better bitrate distribution.  Note: NVENC is a dedicated ASIC, so GPU
# utilisation will never hit 100% (the CUDA cores are not involved).
# Do not enable -weighted_pred with B-frames; H.264 NVENC rejects that combo.
NVENC_ARGS = [
    "-c:v", "h264_nvenc",
    "-gpu", "0",
    "-preset", "p7",
    "-profile:v", "high",
    "-rc", "vbr",
    "-multipass", "fullres",
    "-b:v", VIDEO_BITRATE,
    "-maxrate", MAX_BITRATE,
    "-bufsize", BUFFER_SIZE,
    "-bf", "4",
    "-b_ref_mode", "middle",
    "-spatial-aq", "1",
    "-temporal-aq", "1",
    "-aq-strength", "8",
    "-rc-lookahead", "32",
]


def get_audio_duration(audio_path):
    """Get audio duration in seconds using ffprobe."""
    cmd = [
        get_ffprobe_path(), "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def _escape_concat_path(path):
    return path.replace("\\", "/").replace("'", "'\\''")


def _prepare_video_image(src_path, dst_path):
    """Resize/pad one image to 1080p before FFmpeg starts encoding.

    The image is scaled (up or down) to fit within 1920x1080 while
    maintaining its aspect ratio, then centred on a black canvas.
    """
    with Image.open(src_path) as img:
        img = img.convert("RGBA")

        # Scale to fit within VIDEO_WIDTH x VIDEO_HEIGHT, maintaining aspect ratio.
        # Unlike thumbnail(), this also scales UP smaller images.
        scale = min(VIDEO_WIDTH / img.width, VIDEO_HEIGHT / img.height)
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 255))
        x = (VIDEO_WIDTH - new_w) // 2
        y = (VIDEO_HEIGHT - new_h) // 2
        canvas.alpha_composite(img, (x, y))
        canvas.convert("RGB").save(dst_path, "JPEG", quality=95, subsampling=0)


def _progress_percent(key, value, duration):
    try:
        if key in ("out_time_ms", "out_time_us"):
            elapsed = float(value) / 1_000_000
        elif key == "out_time":
            h, m, s = value.split(":")
            elapsed = (int(h) * 3600) + (int(m) * 60) + float(s)
        else:
            return None
        return min(99, max(0, int((elapsed / duration) * 100)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _run_ffmpeg(cmd, duration, on_process_start=None, on_progress=None):
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if on_process_start:
        on_process_start(proc)
    if on_progress:
        on_progress(0)

    # Drain stderr in a background thread to prevent buffer deadlock
    stderr_lines = []
    def drain_stderr(stream):
        try:
            for line in stream:
                stderr_lines.append(line)
        except Exception:
            pass

    stderr_thread = threading.Thread(target=drain_stderr, args=(proc.stderr,), daemon=True)
    stderr_thread.start()

    if proc.stdout:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            if key == "progress" and value == "end":
                if on_progress:
                    on_progress(99)
                continue

            percent = _progress_percent(key, value, duration)
            if percent is not None and on_progress:
                on_progress(percent)

    proc.wait()
    stderr_thread.join(timeout=1.0)
    stderr = "".join(stderr_lines)

    if proc.returncode != 0:
        raise RuntimeError(
            f"FFmpeg NVENC error (exit {proc.returncode}): {stderr[-3000:] if stderr else 'Unknown process crash'}"
        )



def _build_single_image_cmd(image_path, audio_path, output_path, duration):
    return [
        get_ffmpeg_path(), "-y",
        "-hide_banner", "-nostats", "-loglevel", "error",
        "-progress", "pipe:1",
        "-framerate", str(VIDEO_FPS),
        "-loop", "1",
        "-t", f"{duration:.3f}",
        "-i", image_path,
        "-i", audio_path,
        "-map", "0:v",
        "-map", "1:a",
        *NVENC_ARGS,
        "-c:a", "aac", "-b:a", AUDIO_BITRATE,
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]


def _build_slideshow_cmd(image_paths, audio_path, output_path, duration, list_file):
    segment_dur = duration / len(image_paths)
    with open(list_file, "w", encoding="utf-8") as f:
        for image_path in image_paths:
            f.write(f"file '{_escape_concat_path(image_path)}'\n")
            f.write(f"duration {segment_dur:.6f}\n")
        f.write(f"file '{_escape_concat_path(image_paths[-1])}'\n")

    return [
        get_ffmpeg_path(), "-y",
        "-hide_banner", "-nostats", "-loglevel", "error",
        "-progress", "pipe:1",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-i", audio_path,
        "-map", "0:v",
        "-map", "1:a",
        *NVENC_ARGS,
        "-c:a", "aac", "-b:a", AUDIO_BITRATE,
        "-pix_fmt", "yuv420p",
        "-vsync", "cfr",
        "-r", str(VIDEO_FPS),
        "-t", f"{duration:.3f}",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]


def create_video(audio_path, image_paths, output_path, on_process_start=None, on_progress=None):
    """
    Create MP4 video from audio + image(s).

    Single image: loops for full audio duration.
    Multiple images: divides audio duration equally among images.

    Output: 1920x1080 MP4 (h264_nvenc + aac), images scaled/padded to fit.

    Args:
        audio_path: Path to the audio file (MP3, WAV, etc.)
        image_paths: List of image file paths
        output_path: Output MP4 file path
        on_process_start: Optional callable to receive the Popen process reference when it starts
        on_progress: Optional callable receiving render progress as a percentage

    Returns:
        The output file path
    """
    if not image_paths:
        raise ValueError("At least one image path must be provided.")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    duration = get_audio_duration(audio_path)
    temp_dir = tempfile.mkdtemp(prefix="yt_automator_render_")

    try:
        prepared_images = []
        for idx, image_path in enumerate(image_paths):
            prepared_path = os.path.join(temp_dir, f"frame_{idx:04d}.jpg")
            _prepare_video_image(image_path, prepared_path)
            prepared_images.append(prepared_path)

        if len(prepared_images) == 1:
            cmd = _build_single_image_cmd(
                prepared_images[0], audio_path, output_path, duration
            )
        else:
            list_file = os.path.join(temp_dir, "slideshow.txt")
            cmd = _build_slideshow_cmd(
                prepared_images, audio_path, output_path, duration, list_file
            )

        _run_ffmpeg(cmd, duration, on_process_start, on_progress)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return output_path
