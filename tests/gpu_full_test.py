import csv
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modules.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path  # noqa: E402
from modules.video_creator import NVENC_ARGS  # noqa: E402

OUTPUT_DIR = os.path.join(ROOT, "output")
LIBRARY_DIR = os.path.join(OUTPUT_DIR, "library")
RUNS_DIR = os.path.join(ROOT, "test_runs")


def api_json(path, method="GET", data=None):
    url = f"http://127.0.0.1:8000{path}"
    body = None
    headers = {}
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kwargs)


def make_image(path, label, color):
    img = Image.new("RGB", (1920, 1080), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 790, 1920, 1080), fill=(10, 12, 18))
    draw.text((90, 840), label, fill=(255, 255, 255))
    draw.text((90, 900), "YT Automator GPU Test", fill=(180, 190, 210))
    img.save(path, "JPEG", quality=92)


def make_audio(path, seconds, freq):
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:duration={seconds}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        path,
    ]
    run(cmd)


def nvidia_sample():
    # dmon exposes encoder usage on this driver, while query-gpu does not.
    dmon = run(["nvidia-smi", "dmon", "-s", "u", "-c", "1"]).stdout.splitlines()
    sample_line = next((line for line in dmon if line.strip() and not line.lstrip().startswith("#")), "")
    parts = sample_line.split()
    sm = mem = enc = dec = None
    if len(parts) >= 5:
        sm = int(parts[1])
        mem = int(parts[2])
        enc = int(parts[3])
        dec = int(parts[4])

    query = run([
        "nvidia-smi",
        "--query-gpu=memory.used,memory.total,utilization.gpu,utilization.memory",
        "--format=csv,noheader,nounits",
    ]).stdout.strip()
    mem_used, mem_total, util_gpu, util_mem = [int(x.strip()) for x in query.split(",")]
    return {
        "ts": time.time(),
        "sm_percent": sm,
        "mem_percent": mem,
        "encoder_percent": enc,
        "decoder_percent": dec,
        "memory_used_mb": mem_used,
        "memory_total_mb": mem_total,
        "query_gpu_percent": util_gpu,
        "query_memory_percent": util_mem,
    }


def ffmpeg_cpu_sample():
    ps = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "$p=Get-Process ffmpeg -ErrorAction SilentlyContinue | "
            "Measure-Object CPU -Sum; "
            "if ($p.Count -gt 0) { [double]$p.Sum } else { 0 }",
        ],
        text=True,
        capture_output=True,
    )
    try:
        return float(ps.stdout.strip() or "0")
    except ValueError:
        return 0.0


def summarize(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return {"min": None, "max": None, "avg": None}
    return {
        "min": min(nums),
        "max": max(nums),
        "avg": round(sum(nums) / len(nums), 2),
    }


def nvenc_probe():
    probe_path = os.path.join(RUNS_DIR, "_nvenc_probe.mp4")
    proc = subprocess.run(
        [
            get_ffmpeg_path(),
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=1",
            *NVENC_ARGS,
            "-y",
            probe_path,
        ],
        text=True,
        capture_output=True,
    )
    if os.path.exists(probe_path):
        os.remove(probe_path)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stderr": proc.stderr[-4000:],
    }


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(RUNS_DIR, f"gpu_full_test_{stamp}")
    assets_dir = os.path.join(run_dir, "assets")
    videos_dir = os.path.join(run_dir, "videos")
    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(videos_dir, exist_ok=True)
    os.makedirs(LIBRARY_DIR, exist_ok=True)

    channel = f"GPU_TEST_{stamp}"
    channel_dir = os.path.join(OUTPUT_DIR, channel)
    os.makedirs(channel_dir, exist_ok=True)

    image_names = []
    colors = [(36, 70, 120), (120, 54, 64), (48, 105, 80), (112, 92, 42)]
    for idx, color in enumerate(colors, 1):
        name = f"{channel}_image_{idx}.jpg"
        path = os.path.join(LIBRARY_DIR, name)
        make_image(path, f"Test Image {idx}", color)
        shutil.copy2(path, os.path.join(assets_dir, name))
        image_names.append(name)

    audio_files = []
    for idx, seconds in enumerate((25, 30, 35), 1):
        name = f"gpu_test_video_{idx}.mp3"
        path = os.path.join(channel_dir, name)
        make_audio(path, seconds, 360 + idx * 110)
        shutil.copy2(path, os.path.join(assets_dir, name))
        audio_files.append(name)

    queued_jobs = []
    for idx, audio in enumerate(audio_files):
        selected = image_names[: idx + 2]
        payload = {
            "channel_name": channel,
            "audio_file": audio,
            "library_images": json.dumps(selected),
        }
        queued_jobs.append(api_json("/api/queue/video", method="POST", data=payload)["job"])

    samples = []
    cpu_samples = []
    last_status = None
    started = time.time()
    timeout = 900

    while True:
        queue_state = api_json("/api/queue")["jobs"]
        target_jobs = [j for j in queue_state if j["id"] in {q["id"] for q in queued_jobs}]
        status = [(j["title"], j["status"], j.get("progress", 0)) for j in target_jobs]
        if status != last_status:
            print(status, flush=True)
            last_status = status

        samples.append(nvidia_sample())
        cpu_samples.append({"ts": time.time(), "ffmpeg_cpu_seconds": ffmpeg_cpu_sample()})

        if target_jobs and all(j["status"] in ("done", "failed") for j in target_jobs):
            break
        if time.time() - started > timeout:
            raise TimeoutError("Timed out waiting for queued GPU render jobs")
        time.sleep(1)

    final_jobs = [j for j in api_json("/api/queue")["jobs"] if j["id"] in {q["id"] for q in queued_jobs}]
    for job in final_jobs:
        if job.get("result", {}).get("filename"):
            src = os.path.join(channel_dir, job["result"]["filename"])
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(videos_dir, job["result"]["filename"]))

    gpu_info = run([
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total",
        "--format=csv,noheader,nounits",
    ]).stdout.strip()
    probe = nvenc_probe()

    report = {
        "run_dir": run_dir,
        "channel": channel,
        "gpu": gpu_info,
        "ffmpeg_path": get_ffmpeg_path(),
        "ffprobe_path": get_ffprobe_path(),
        "nvenc_probe": probe,
        "jobs": final_jobs,
        "samples": samples,
        "cpu_samples": cpu_samples,
        "summary": {
            "sm_percent": summarize([s["sm_percent"] for s in samples]),
            "encoder_percent": summarize([s["encoder_percent"] for s in samples]),
            "memory_used_mb": summarize([s["memory_used_mb"] for s in samples]),
            "query_gpu_percent": summarize([s["query_gpu_percent"] for s in samples]),
        },
        "notes": [
            "The production FFmpeg render path uses h264_nvenc with the same high-quality NVENC settings as the app.",
            "Images are prepared to 1920x1080 before FFmpeg starts, so FFmpeg no longer runs the old CPU scale/pad filter chain.",
            "Some CPU usage is still expected for image prep, process orchestration, audio muxing, disk I/O, and feeding frames to NVENC.",
            "NVENC encoder utilization may not reach 100% on short/simple 1080p static-image videos because the encoder can finish frames faster than the workload can feed them.",
        ],
    }

    report_json = os.path.join(run_dir, "report.json")
    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    report_md = os.path.join(run_dir, "REPORT.md")
    with open(report_md, "w", encoding="utf-8") as f:
        f.write("# GPU Full Test Report\n\n")
        f.write(f"- Run folder: `{run_dir}`\n")
        f.write(f"- GPU: `{gpu_info}`\n")
        f.write(f"- NVENC probe: `{'ok' if probe['ok'] else 'failed'}`\n")
        f.write(f"- Jobs: `{len(final_jobs)}`\n")
        f.write(f"- Done: `{sum(1 for j in final_jobs if j['status'] == 'done')}`\n")
        f.write(f"- Failed: `{sum(1 for j in final_jobs if j['status'] == 'failed')}`\n\n")
        f.write("## GPU Summary\n\n")
        for key, val in report["summary"].items():
            f.write(f"- {key}: min `{val['min']}`, avg `{val['avg']}`, max `{val['max']}`\n")
        f.write("\n## Notes\n\n")
        for note in report["notes"]:
            f.write(f"- {note}\n")
        if not probe["ok"]:
            f.write("\n## NVENC Probe Error\n\n")
            f.write("```text\n")
            f.write(probe["stderr"])
            f.write("\n```\n")

    print(json.dumps({
        "run_dir": run_dir,
        "report_json": report_json,
        "report_md": report_md,
        "videos_dir": videos_dir,
        "summary": report["summary"],
        "jobs": final_jobs,
    }, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"GPU full test failed: {exc}", file=sys.stderr)
        raise
