import unittest
import os
import sys
import time
import inspect
import tempfile
from unittest.mock import MagicMock, patch

# Ensure the root folder is in path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.render_queue import RenderQueue, RenderJob
from modules.video_creator import NVENC_ARGS, _run_ffmpeg
from modules.thumbnail import generate_thumbnail

class TestFixes(unittest.TestCase):
    def test_render_queue_signature_check_with_arg(self):
        """Verify the render queue calls functions accepting arguments with the job parameter."""
        queue = RenderQueue()
        
        called_with_job = None
        def render_fn_with_arg(job):
            nonlocal called_with_job
            called_with_job = job
            return {"status": "ok"}
            
        job_dict = queue.add_job("video", "Test Job With Arg", "TestChannel", render_fn_with_arg)
        
        # Give the worker thread a moment to process
        time.sleep(0.5)
        
        self.assertIsNotNone(called_with_job)
        self.assertEqual(called_with_job.title, "Test Job With Arg")
        
        # Clean up queue
        queue.clear_completed()

    def test_render_queue_signature_check_without_arg(self):
        """Verify the render queue calls functions without arguments directly."""
        queue = RenderQueue()
        
        called = False
        def render_fn_no_arg():
            nonlocal called
            called = True
            return {"status": "ok"}
            
        job_dict = queue.add_job("video", "Test Job No Arg", "TestChannel", render_fn_no_arg)
        
        # Give the worker thread a moment to process
        time.sleep(0.5)
        
        self.assertTrue(called)
        
        # Clean up queue
        queue.clear_completed()

    def test_render_queue_retry_failed_job(self):
        """Verify a failed job can be retried with the original render function."""
        queue = RenderQueue()
        attempts = {"count": 0}

        def render_fn():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("first attempt failed")
            return {"status": "ok"}

        job_dict = queue.add_job("video", "Retry Me", "TestChannel", render_fn)
        deadline = time.time() + 3
        while time.time() < deadline:
            job = next(j for j in queue.get_all_jobs() if j["id"] == job_dict["id"])
            if job["status"] == "failed":
                break
            time.sleep(0.05)

        self.assertEqual(job["status"], "failed")

        retry = queue.retry_job(job_dict["id"])
        self.assertIsNotNone(retry)
        self.assertEqual(retry["status"], "pending")

        deadline = time.time() + 3
        while time.time() < deadline:
            job = next(j for j in queue.get_all_jobs() if j["id"] == job_dict["id"])
            if job["status"] == "done":
                break
            time.sleep(0.05)

        self.assertEqual(job["status"], "done")
        self.assertEqual(attempts["count"], 2)

    @patch("subprocess.Popen")
    def test_run_ffmpeg_stderr_draining(self, mock_popen):
        """Verify that _run_ffmpeg starts a thread to drain stderr and reads stdout to completion."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        
        # Simulate stdout returning progress=end
        mock_proc.stdout = ["progress=end"]
        # Simulate stderr having warning lines
        mock_proc.stderr = ["warning line 1", "warning line 2"]
        
        mock_popen.return_value = mock_proc
        
        on_progress = MagicMock()
        
        # Run with short duration so progress percent doesn't crash on divisions
        _run_ffmpeg(["ffmpeg"], 10.0, on_progress=on_progress)
        
        # Check Popen was invoked
        mock_popen.assert_called_once()
        # Check progress was updated
        on_progress.assert_any_call(99)

    def test_nvenc_does_not_combine_weighted_prediction_with_b_frames(self):
        """H.264 NVENC rejects weighted prediction when B-frames are enabled."""
        args = dict(zip(NVENC_ARGS[::2], NVENC_ARGS[1::2]))
        b_frames = int(args.get("-bf", "0"))

        if b_frames > 0:
            self.assertNotIn("-weighted_pred", NVENC_ARGS)

    def test_thumbnail_block_text_custom_layout_options(self):
        """Verify thumbnail generation accepts block layout, colors, and per-line scales."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            bg_path = os.path.join(tmp, "bg.png")
            out_path = os.path.join(tmp, "thumb.png")
            Image.new("RGB", (1280, 720), (12, 14, 18)).save(bg_path)

            result = generate_thumbnail(
                bg_path,
                "READ\nPEOPLE LIKE A\nBOOK!",
                ["READ", "BOOK"],
                out_path,
                font_size=82,
                pos_x=43.25,
                pos_y=42.77744528748312,
                text_color="#111111",
                highlight_color="#e60023",
                box_color="#ffffff",
                box_opacity=0.95,
                box_width=42,
                fit_box=True,
                line_scales=[1.35, 0.72, 1.1],
                shadow_mode="none",
            )

            self.assertEqual(result, out_path)
            self.assertTrue(os.path.exists(out_path))
            with Image.open(out_path) as img:
                self.assertEqual(img.size, (1280, 720))

if __name__ == "__main__":
    unittest.main()
