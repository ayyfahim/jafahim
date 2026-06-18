import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime

from PIL import Image, ImageDraw


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.ffmpeg_utils import get_ffmpeg_path  # noqa: E402


OUTPUT_DIR = os.path.join(ROOT, "output")
RUNS_DIR = os.path.join(ROOT, "test_runs")
DEFAULT_CHANNEL = "https://www.youtube.com/@Google/videos"


def safe_print(value):
    print(str(value).encode("ascii", errors="replace").decode("ascii"), flush=True)


def api_json(path, method="GET", data=None, timeout=300):
    url = f"http://127.0.0.1:8000{path}"
    body = None
    headers = {}
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {path}: {body}") from e


def upload_file(path):
    boundary = f"----yt-automator-{int(time.time() * 1000)}"
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        file_bytes = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/library/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def make_library_image(path, label, color):
    img = Image.new("RGB", (1920, 1080), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 770, 1920, 1080), fill=(8, 10, 14))
    draw.text((90, 830), label, fill=(255, 255, 255))
    draw.text((90, 900), "Real YouTube Channel E2E Test", fill=(190, 205, 220))
    img.save(path, "JPEG", quality=92)


def nvidia_sample():
    dmon = subprocess.run(
        ["nvidia-smi", "dmon", "-s", "u", "-c", "1"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    sample_line = next((line for line in dmon if line.strip() and not line.lstrip().startswith("#")), "")
    parts = sample_line.split()
    sm = mem = enc = dec = None
    if len(parts) >= 5:
        sm, mem, enc, dec = map(int, parts[1:5])
    return {"ts": time.time(), "sm_percent": sm, "mem_percent": mem, "encoder_percent": enc, "decoder_percent": dec}


def summarize(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return {"min": None, "avg": None, "max": None}
    return {"min": min(nums), "avg": round(sum(nums) / len(nums), 2), "max": max(nums)}


def wait_for_jobs(job_ids, timeout=1800):
    samples = []
    last = None
    start = time.time()
    while True:
        jobs = api_json("/api/queue")["jobs"]
        target = [j for j in jobs if j["id"] in job_ids]
        status = [(j["title"], j["status"], j.get("progress", 0)) for j in target]
        if status != last:
            safe_print(status)
            last = status
        samples.append(nvidia_sample())
        if target and all(j["status"] in ("done", "failed") for j in target):
            return target, samples
        if time.time() - start > timeout:
            raise TimeoutError("Timed out waiting for YouTube E2E render jobs")
        time.sleep(1)


def copy_channel_outputs(channel_name, run_dir):
    safe = channel_name
    src = os.path.join(OUTPUT_DIR, safe)
    dst = os.path.join(run_dir, "channel_output")
    if os.path.exists(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


def main():
    channel_url = os.environ.get("YT_TEST_CHANNEL", DEFAULT_CHANNEL)
    count = int(os.environ.get("YT_TEST_COUNT", "2"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(RUNS_DIR, f"youtube_channel_e2e_{stamp}")
    assets_dir = os.path.join(run_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    safe_print(f"Channel: {channel_url}")
    safe_print(f"FFmpeg: {get_ffmpeg_path()}")

    api_json("/api/queue/clear", method="POST")

    fetched = api_json(
        "/api/fetch-channel",
        method="POST",
        data={"channel_url": channel_url, "sort_by": "latest", "limit": max(count * 4, 8)},
        timeout=300,
    )
    if fetched.get("status") != "ok":
        raise RuntimeError(f"Fetch failed: {fetched}")

    videos = [v for v in fetched["videos"] if v.get("url")]
    videos.sort(key=lambda v: v.get("duration") if v.get("duration") is not None else 10**9)
    videos = videos[:count]
    if len(videos) < count:
        raise RuntimeError(f"Only found {len(videos)} videos in fetch result: {fetched}")

    uploaded_images = []
    for idx, color in enumerate(((40, 72, 120), (120, 52, 68), (52, 112, 84)), 1):
        path = os.path.join(assets_dir, f"yt_e2e_bg_{idx}.jpg")
        make_library_image(path, f"YouTube E2E Background {idx}", color)
        upload = upload_file(path)
        if upload.get("status") != "ok":
            raise RuntimeError(f"Library upload failed: {upload}")
        uploaded_images.append(upload["result"]["name"])

    downloads = []
    for idx, video in enumerate(videos, 1):
        safe_print(f"Downloading {idx}/{len(videos)}: {video['title']}")
        result = api_json(
            "/api/download",
            method="POST",
            data={"video_url": video["url"], "channel_name": fetched["channel_name"]},
            timeout=900,
        )
        if result.get("status") != "ok":
            raise RuntimeError(f"Download failed: {result}")
        downloads.append({"video": video, "download": result["result"]})

    history = api_json("/api/download-history")
    missing_history = [
        item["download"]["video_id"]
        for item in downloads
        if item["download"]["video_id"] not in set(history.get("video_ids", []))
    ]
    if missing_history:
        raise RuntimeError(f"Server history did not record downloaded IDs: {missing_history}")

    channel_name = downloads[0]["download"]["channel_name"]
    studio = api_json(f"/api/channels/{urllib.parse.quote(channel_name)}")
    if studio.get("status") != "ok" or len(studio.get("items", [])) < len(downloads):
        raise RuntimeError(f"Studio listing missing downloaded items: {studio}")

    jobs = []
    thumbnails = []
    for item in studio["items"][: len(downloads)]:
        thumb = api_json(
            "/api/create-thumbnail",
            method="POST",
            data={
                "channel_name": channel_name,
                "audio_file": item["files"]["audio"],
                "library_image": uploaded_images[0],
                "text": item["title"][:60],
                "highlighted_words": ",".join(item["title"].split()[:2]),
                "font_size": "64",
                "pos_x": "50",
                "pos_y": "82",
            },
            timeout=120,
        )
        if thumb.get("status") != "ok":
            raise RuntimeError(f"Thumbnail generation failed: {thumb}")
        thumbnails.append(thumb["result"])

        queued = api_json(
            "/api/queue/video",
            method="POST",
            data={
                "channel_name": channel_name,
                "audio_file": item["files"]["audio"],
                "library_images": json.dumps(uploaded_images),
            },
            timeout=120,
        )
        if queued.get("status") != "ok":
            raise RuntimeError(f"Queue failed: {queued}")
        jobs.append(queued["job"])

    final_jobs, gpu_samples = wait_for_jobs({j["id"] for j in jobs})
    failed = [j for j in final_jobs if j["status"] != "done"]
    if failed:
        raise RuntimeError(f"One or more YouTube render jobs failed: {failed}")

    output_copy = copy_channel_outputs(channel_name, run_dir)
    report = {
        "run_dir": run_dir,
        "channel_url": channel_url,
        "channel_name": channel_name,
        "fetched_count": len(fetched["videos"]),
        "downloaded": downloads,
        "uploaded_images": uploaded_images,
        "thumbnails": thumbnails,
        "jobs": final_jobs,
        "output_copy": output_copy,
        "gpu_summary": {
            "sm_percent": summarize([s["sm_percent"] for s in gpu_samples]),
            "encoder_percent": summarize([s["encoder_percent"] for s in gpu_samples]),
            "mem_percent": summarize([s["mem_percent"] for s in gpu_samples]),
        },
        "ffmpeg_path": get_ffmpeg_path(),
    }
    with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(run_dir, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("# YouTube Channel E2E Test\n\n")
        f.write(f"- Channel URL: `{channel_url}`\n")
        f.write(f"- Channel name: `{channel_name}`\n")
        f.write(f"- Downloaded videos: `{len(downloads)}`\n")
        f.write(f"- Render jobs done: `{sum(1 for j in final_jobs if j['status'] == 'done')}`\n")
        f.write(f"- Output copy: `{output_copy}`\n")
        f.write(f"- FFmpeg: `{get_ffmpeg_path()}`\n\n")
        f.write("## GPU Summary\n\n")
        for key, val in report["gpu_summary"].items():
            f.write(f"- {key}: min `{val['min']}`, avg `{val['avg']}`, max `{val['max']}`\n")

    safe_print(json.dumps({
        "run_dir": run_dir,
        "report": os.path.join(run_dir, "REPORT.md"),
        "output_copy": output_copy,
        "gpu_summary": report["gpu_summary"],
        "jobs": final_jobs,
    }, indent=2))


if __name__ == "__main__":
    main()
