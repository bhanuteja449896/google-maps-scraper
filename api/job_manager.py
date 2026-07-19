"""
Queue-based job manager for the Google Maps Scraper API.

Design
------
- A single background worker thread processes jobs ONE AT A TIME from a FIFO queue.
- New jobs return immediately with status "queued".
- The worker picks up the next job, sets status → "processing", runs the scraper,
  then sets status → "available" (success) or "failed" (error).
- All live state is kept in an in-memory dict (_jobs) protected by a lock.
- A single shared SheetsDatabase instance is reused across jobs (lazy-initialised).
- Cancellation is best-effort: sets a cancel event checked by progress callbacks.
"""

import hashlib
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# In-memory state
# ─────────────────────────────────────────────────────────────────────────────

_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()

# FIFO queue of (job_id, job_type, params) tuples
_job_queue: queue.Queue = queue.Queue()

# Per-job cancel events
_cancel_events: Dict[str, threading.Event] = {}

# Worker thread
_worker_thread: Optional[threading.Thread] = None

# Shared SheetsDatabase instance (lazy-init, one per process)
_db_instance = None
_db_init_lock = threading.Lock()

# Stats
_started_at = datetime.now(timezone.utc)
_total_enqueued = 0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db():
    """Lazy-init the shared SheetsDatabase. Thread-safe."""
    global _db_instance
    with _db_init_lock:
        if _db_instance is None:
            try:
                from dotenv import load_dotenv
                load_dotenv()
            except ImportError:
                pass
            from sheets_db import SheetsDatabase
            _db_instance = SheetsDatabase()
            logger.info("SheetsDatabase initialised (shared instance)")
    return _db_instance


def _update_job(job_id: str, **kwargs) -> None:
    """Thread-safely update fields on an in-memory job record."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job.update(kwargs)
            job["updated_at"] = _now_iso()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def enqueue_job(job_type: str, params: dict) -> str:
    """
    Add a job to the FIFO queue. Returns the job_id immediately.

    job_type: "search" | "place" | "resume"
    params:   dict matching the relevant Pydantic model fields.
    """
    global _total_enqueued

    if job_type == "resume":
        # For resume, use the original job_id so we track progress on it
        job_id = params.get("job_id", "")
        if not job_id:
            raise ValueError("Resume jobs require a job_id")
        query = f"resume:{job_id}"
    else:
        raw_id = params.get("job_id")
        if raw_id:
            job_id = raw_id
        else:
            seed = (params.get("query", "") or params.get("place_id", "")) + str(time.time())
            job_id = hashlib.sha256(seed.encode()).hexdigest()[:16]
        query = params.get("query") or f"place:{params.get('place_id', '')}"

    now = _now_iso()
    with _jobs_lock:
        # Prevent duplicate queueing of the same job_id
        existing = _jobs.get(job_id)
        if existing and existing["status"] in ("queued", "processing"):
            logger.info("Job %s already queued/running — returning existing entry", job_id)
            return job_id

        _jobs[job_id] = {
            "job_id": job_id,
            "job_type": job_type,
            "query": query,
            "status": "queued",
            "places_found": 0,
            "places_done": 0,
            "reviews_done": 0,
            "progress_pct": 0,
            "started_at": None,
            "updated_at": now,
            "enqueued_at": now,
            "spreadsheet_url": None,
            "error": None,
        }
        _cancel_events[job_id] = threading.Event()
        _total_enqueued += 1

    _job_queue.put((job_id, job_type, params))
    logger.info("Enqueued job %s (%s) — queue depth: %d", job_id, job_type, _job_queue.qsize())
    return job_id


def cancel_job(job_id: str) -> bool:
    """
    Request cancellation of a job.
    Returns True if found and not already terminal; False otherwise.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return False
        if job["status"] in ("available", "failed", "cancelled"):
            return False
        # Signal the worker
        event = _cancel_events.get(job_id)
        if event:
            event.set()
        job["status"] = "cancelled"
        job["updated_at"] = _now_iso()
    logger.info("Cancellation requested for job %s", job_id)
    return True


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Return the in-memory status dict for a job, or None if unknown."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            return {k: v for k, v in job.items() if k != "enqueued_at"}
    return None


def get_all_jobs() -> List[Dict[str, Any]]:
    """Return all tracked jobs sorted by updated_at descending."""
    with _jobs_lock:
        result = [{k: v for k, v in j.items() if k != "enqueued_at"} for j in _jobs.values()]
    result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return result


def get_queue_position(job_id: str) -> Optional[int]:
    """Return 1-based queue position for a queued job, or None."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job["status"] != "queued":
            return None
        enqueued_at = job.get("enqueued_at", "")
        pos = 1
        for jid, j in _jobs.items():
            if jid != job_id and j["status"] == "queued":
                if j.get("enqueued_at", "") < enqueued_at:
                    pos += 1
        return pos


def get_queue_length() -> int:
    return _job_queue.qsize()


def get_metrics() -> Dict[str, Any]:
    """Return monitoring metrics including memory, CPU and job counts."""
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        mem_mb = round(proc.memory_info().rss / 1024 / 1024, 1)
        cpu_pct = round(proc.cpu_percent(interval=0.1), 1)
    except Exception:
        mem_mb = 0.0
        cpu_pct = 0.0

    with _jobs_lock:
        statuses = [j["status"] for j in _jobs.values()]

    uptime = int((datetime.now(timezone.utc) - _started_at).total_seconds())

    return {
        "status": "ok",
        "uptime_seconds": uptime,
        "active_jobs": statuses.count("processing"),
        "queued_jobs": statuses.count("queued"),
        "total_jobs_tracked": len(statuses),
        "total_jobs_enqueued": _total_enqueued,
        "completed_jobs": statuses.count("available"),
        "failed_jobs": statuses.count("failed"),
        "cancelled_jobs": statuses.count("cancelled"),
        "memory_mb": mem_mb,
        "cpu_percent": cpu_pct,
        "worker_alive": _worker_thread is not None and _worker_thread.is_alive(),
        "version": "1.0.0",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Worker thread
# ─────────────────────────────────────────────────────────────────────────────

def _worker_loop() -> None:
    """
    Main loop for the background worker thread.
    Processes exactly one job at a time from the FIFO queue.
    """
    logger.info("Job worker thread started — waiting for work")
    while True:
        try:
            job_id, job_type, params = _job_queue.get(block=True, timeout=2)
        except queue.Empty:
            continue

        # Skip if cancelled while sitting in queue
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job or job["status"] == "cancelled":
                logger.info("Job %s was cancelled before processing — skipping", job_id)
                _job_queue.task_done()
                continue

        _update_job(job_id, status="processing", started_at=_now_iso())
        logger.info("Processing job %s (type=%s): %s", job_id, job_type, params.get("query", ""))

        try:
            _execute_job(job_id, job_type, params)
        except Exception as exc:
            logger.exception("Job %s crashed: %s", job_id, exc)
            _update_job(job_id, status="failed", error=str(exc))
            # Try to mark as failed in Sheets too
            try:
                db = _get_db()
                db.update_job_status(job_id, "failed", error=str(exc))
            except Exception:
                pass
        finally:
            _job_queue.task_done()
            logger.info("Job %s finished", job_id)


def _make_progress_callbacks(job_id: str, cancel_event: threading.Event):
    """Return (overall_cb, review_cb) that update in-memory state and honour cancel."""

    def _overall_cb(done: int, total: int) -> None:
        pct = int(100 * done / total) if total > 0 else 0
        _update_job(job_id, places_done=done, places_found=total, progress_pct=pct)

    def _review_cb(done: int, total: int, place_name: str = "") -> None:
        _update_job(job_id, reviews_done=done)

    return _overall_cb, _review_cb


def _execute_job(job_id: str, job_type: str, params: dict) -> None:
    """Run a single job synchronously inside the worker thread."""
    from scraper import GoogleMapsScraper

    cancel_event = _cancel_events.get(job_id, threading.Event())
    overall_cb, review_cb = _make_progress_callbacks(job_id, cancel_event)

    delay = params.get("delay", 2.5)
    workers_n = params.get("workers", 4)

    db = _get_db()

    # ── search job ────────────────────────────────────────────────────────────
    if job_type == "search":
        with GoogleMapsScraper(
            proxy=params.get("proxy"),
            lang=params.get("lang", "en"),
            gl=params.get("gl", "us"),
            min_delay=delay,
            max_delay=delay * 2,
            workers=workers_n,
            session_file="/tmp/session.json",
            extract_emails=params.get("extract_emails", False),
        ) as scraper:
            stats = scraper.search_and_scrape(
                db=db,
                query=params["query"],
                lat=params.get("lat", 0.0),
                lng=params.get("lng", 0.0),
                zoom=params.get("zoom", 13),
                max_places=params.get("max_places"),
                max_reviews=params.get("max_reviews"),
                job_id=job_id,
                progress_callback=overall_cb,
                review_progress_callback=review_cb,
            )

        # Pull spreadsheet URL from Sheets DB
        sheet_url = None
        try:
            job_info = db.get_job(job_id)
            sheet_url = (job_info or {}).get("spreadsheet_url")
        except Exception:
            pass

        _update_job(
            job_id,
            status="available",
            places_done=stats["places_saved"],
            places_found=stats["places_found"],
            reviews_done=stats["reviews_saved"],
            progress_pct=100,
            spreadsheet_url=sheet_url,
            error=None,
        )

    # ── single place job ──────────────────────────────────────────────────────
    elif job_type == "place":
        place_id = params["place_id"]

        # Create job entry in Sheets so a spreadsheet is provisioned
        db.create_job(job_id, f"place:{place_id}")

        with GoogleMapsScraper(
            proxy=params.get("proxy"),
            lang=params.get("lang", "en"),
            gl=params.get("gl", "us"),
            min_delay=delay,
            max_delay=delay * 2,
            workers=workers_n,
            session_file="/tmp/session.json",
            extract_emails=params.get("extract_emails", False),
        ) as scraper:
            place, reviews_saved = scraper.scrape_single_place(
                db=db,
                place_id=place_id,
                max_reviews=params.get("max_reviews"),
                lat=params.get("lat", 0.0),
                lng=params.get("lng", 0.0),
                query="",
                job_id=job_id,
            )

        db.update_job_status(job_id, "done")

        sheet_url = None
        try:
            job_info = db.get_job(job_id)
            sheet_url = (job_info or {}).get("spreadsheet_url")
        except Exception:
            pass

        if place:
            _update_job(
                job_id,
                status="available",
                places_done=1,
                places_found=1,
                reviews_done=reviews_saved,
                progress_pct=100,
                spreadsheet_url=sheet_url,
                error=None,
            )
        else:
            _update_job(
                job_id,
                status="failed",
                places_done=0,
                places_found=1,
                progress_pct=0,
                error="Could not fetch place details from Google Maps",
            )

    # ── resume job ────────────────────────────────────────────────────────────
    elif job_type == "resume":
        with GoogleMapsScraper(
            proxy=params.get("proxy"),
            lang=params.get("lang", "en"),
            gl=params.get("gl", "us"),
            min_delay=delay,
            max_delay=delay * 2,
            workers=workers_n,
            session_file="/tmp/session.json",
        ) as scraper:
            stats = scraper.resume_job(
                db=db,
                job_id=job_id,
                max_reviews=params.get("max_reviews"),
                place_progress_callback=overall_cb,
                review_progress_callback=review_cb,
            )

        sheet_url = None
        try:
            job_info = db.get_job(job_id)
            sheet_url = (job_info or {}).get("spreadsheet_url")
        except Exception:
            pass

        _update_job(
            job_id,
            status="available",
            places_done=stats.get("places_saved", 0),
            places_found=stats.get("places_found", 0),
            reviews_done=stats.get("reviews_saved", 0),
            progress_pct=100,
            spreadsheet_url=sheet_url,
            error=None,
        )

    else:
        raise ValueError(f"Unknown job type: {job_type!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────

def start_worker() -> None:
    """Start the background worker thread (idempotent)."""
    global _worker_thread
    with _jobs_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            logger.debug("Worker thread already running")
            return
        _worker_thread = threading.Thread(
            target=_worker_loop,
            daemon=True,
            name="scraper-worker",
        )
        _worker_thread.start()
    logger.info("Job worker thread started (id=%s)", _worker_thread.ident)
