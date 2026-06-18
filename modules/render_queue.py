"""
Render queue for background video rendering.
Processes jobs sequentially in a single daemon thread so only one
FFmpeg render runs at a time (avoids GPU contention on the 1660 Ti).
"""

import threading
import queue
import uuid
import time
import inspect



class RenderJob:
    """Represents a single render job in the queue."""

    def __init__(self, job_id, job_type, title, channel_name, render_fn=None):
        self.id = job_id
        self.job_type = job_type        # 'video' or 'thumbnail'
        self.title = title
        self.channel_name = channel_name
        self.render_fn = render_fn
        self.status = 'pending'         # pending | rendering | done | failed
        self.progress = 0
        self.result = None
        self.error = None
        self.created_at = time.time()
        self.started_at = None
        self.finished_at = None
        self.process = None             # Active subprocess.Popen reference

    def cancel(self):
        """Cancel the job. If rendering, kills the active subprocess."""
        if self.status == 'pending':
            self.status = 'failed'
            self.error = 'Cancelled by user'
            return True
        elif self.status == 'rendering':
            self.status = 'failed'
            self.error = 'Cancelled by user'
            if self.process:
                try:
                    self.process.kill()
                except Exception:
                    pass
            return True
        return False

    def to_dict(self):
        d = {
            'id': self.id,
            'job_type': self.job_type,
            'title': self.title,
            'channel_name': self.channel_name,
            'status': self.status,
            'progress': self.progress,
            'created_at': self.created_at,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
        }
        if self.result:
            d['result'] = self.result
        if self.error:
            d['error'] = self.error
        return d


class RenderQueue:
    """
    Thread-safe render queue.
    Accepts jobs via add_job() and processes them one at a time in a
    background daemon thread.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._jobs = []
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def add_job(self, job_type, title, channel_name, render_fn):
        """
        Add a render job to the queue.

        Args:
            job_type: 'video' or 'thumbnail'
            title: Display title for the job
            channel_name: Channel the output belongs to
            render_fn: Zero-arg or one-arg (accepts RenderJob) callable that performs
                       the render and returns a result dict (e.g. {'filename': ..., 'url': ...})

        Returns:
            dict snapshot of the newly-created job.
        """
        job_id = uuid.uuid4().hex[:8]
        job = RenderJob(job_id, job_type, title, channel_name, render_fn)
        with self._lock:
            self._jobs.append(job)
        self._queue.put((job, render_fn))
        return job.to_dict()

    def get_all_jobs(self):
        """Return a list of all jobs (newest last)."""
        with self._lock:
            return [j.to_dict() for j in self._jobs]

    def clear_completed(self):
        """Remove finished (done / failed) jobs from the list."""
        with self._lock:
            self._jobs = [j for j in self._jobs
                          if j.status in ('pending', 'rendering')]

    def cancel_job(self, job_id):
        """Cancel a pending or rendering job."""
        with self._lock:
            for job in self._jobs:
                if job.id == job_id:
                    return job.cancel()
        return False

    def retry_job(self, job_id):
        """Move a failed job back to pending and enqueue the original render function."""
        with self._lock:
            target = None
            for job in self._jobs:
                if job.id == job_id:
                    target = job
                    break

            if not target or target.status != 'failed' or not target.render_fn:
                return None

            target.status = 'pending'
            target.progress = 0
            target.result = None
            target.error = None
            target.started_at = None
            target.finished_at = None
            target.process = None
            render_fn = target.render_fn

        self._queue.put((target, render_fn))
        return target.to_dict()

    # ── internal ─────────────────────────────────────────

    def _worker_loop(self):
        """Process jobs one at a time, forever."""
        while True:
            job, render_fn = self._queue.get()
            with self._lock:
                if job.status == 'failed':
                    # Already cancelled while pending
                    self._queue.task_done()
                    continue
                job.status = 'rendering'
                job.progress = 0
                job.started_at = time.time()
            try:
                # Inspect signature to determine if we should pass the job object
                try:
                    sig = inspect.signature(render_fn)
                    has_params = len(sig.parameters) > 0
                except Exception:
                    has_params = False

                if has_params:
                    result = render_fn(job)
                else:
                    result = render_fn()

                
                with self._lock:
                    if job.status != 'failed':
                        job.status = 'done'
                        job.progress = 100
                        job.result = result
                        job.finished_at = time.time()
            except Exception as e:
                with self._lock:
                    if job.status != 'failed':
                        job.status = 'failed'
                        job.error = str(e)
                    job.finished_at = time.time()
            self._queue.task_done()


# Singleton — shared across the FastAPI app
render_queue = RenderQueue()
