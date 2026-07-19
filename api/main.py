"""
Google Maps Scraper — Production REST API
==========================================
FastAPI backend designed for GCP Cloud Run deployment.

Status lifecycle
----------------
  POST /scrape/*  →  job_id, status="queued"
  GET  /jobs/{id}  →  status="processing"  (while running)
  GET  /jobs/{id}  →  status="available"   (on success)
  GET  /jobs/{id}  →  status="failed"      (on error)
  GET  /jobs/{id}  →  status="cancelled"   (after DELETE)

Cloud Run wakeup
----------------
  Call GET /wakeup on page load (no auth).
  - If Cloud Run is cold-starting → request hangs then resolves → show "Waking up" until response
  - Once response arrives → show "Active"

Auth
----
  Pass your secret in the X-API-Key header.
  If API_KEY env var is not set, auth is disabled (dev mode).
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from api.auth import verify_api_key
from api.job_manager import (
    cancel_job,
    enqueue_job,
    get_all_jobs,
    get_job_status,
    get_metrics,
    get_queue_length,
    get_queue_position,
    start_worker,
    _get_db,
)
from api.models import PlaceRequest, ResumeRequest, SearchRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# App lifecycle
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: spin up the background worker thread."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    start_worker()
    logger.info("Google Maps Scraper API started — worker ready")
    yield
    logger.info("Google Maps Scraper API shutting down")


# ─────────────────────────────────────────────────────────────────────────────
# App + middleware
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Google Maps Scraper API",
    description=(
        "Production REST API for Google Maps data extraction. "
        "Jobs are queued and processed one at a time. "
        "Results are stored in Google Sheets via the Sheets API."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow your frontend origin via ALLOWED_ORIGINS env var
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_MAP = {
    "running": "processing",
    "done": "available",
    "queued": "queued",
    "processing": "processing",
    "available": "available",
    "cancelled": "cancelled",
    "failed": "failed",
}


def _map_status(s: str) -> str:
    return _STATUS_MAP.get(s, s)


def _enrich_job(job: dict) -> dict:
    """Add computed fields and normalise status for API consumers."""
    places_found = job.get("places_found", 0)
    places_done = job.get("places_done", 0)
    pct = job.get("progress_pct", 0)
    if places_found > 0 and pct == 0 and job.get("status") not in ("queued",):
        pct = int(100 * places_done / places_found)

    return {
        **{k: v for k, v in job.items() if k != "job_type"},
        "status": _map_status(job.get("status", "")),
        "progress_pct": min(pct, 100),
        "queue_position": get_queue_position(job["job_id"]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Root & Health
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/",
    tags=["Info"],
    summary="API info and available endpoints",
)
def root():
    return {
        "name": "Google Maps Scraper API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "status": [
                "GET /wakeup  — wake up the server & check if active (no auth)",
                "GET /health  — server health + worker status",
            ],
            "scrape": [
                "POST /scrape/search  — search query → scrape all results",
                "POST /scrape/place   — scrape a single place by ID",
                "POST /scrape/resume  — resume an interrupted job",
            ],
            "jobs": [
                "GET    /jobs               — list all jobs",
                "GET    /jobs/{job_id}      — get job status",
                "DELETE /jobs/{job_id}      — cancel a job",
                "GET    /jobs/{job_id}/places — places scraped for a job",
                "GET    /jobs/{job_id}/stats  — place & review counts",
            ],
            "monitor": [
                "GET /monitor         — system metrics (memory, CPU, uptime)",
                "GET /monitor/metrics — Prometheus-style text metrics",
            ],
        },
        "auth": "Pass your secret in the X-API-Key header (no auth needed for /wakeup and /health)",
    }


@app.get(
    "/wakeup",
    tags=["Health"],
    summary="Wake-up ping — call this on page load to wake Cloud Run",
)
def wakeup():
    """
    **No authentication required.**

    Call this endpoint when your frontend page loads.

    - **Cloud Run cold start**: the request will take 5–15 seconds while the container
      warms up. Show a *"Waking up..."* spinner in your UI while waiting.
    - **Already warm**: responds in < 100ms. Show *"Active"* in your UI.

    Your frontend logic:
    ```js
    // Before calling: set UI state to "waking_up"
    fetch('/wakeup')
      .then(() => setStatus('active'))
      .catch(() => setStatus('offline'))
    ```
    """
    m = get_metrics()
    return {
        "status": "active",
        "message": "Server is awake and ready",
        "uptime_seconds": m["uptime_seconds"],
        "worker_alive": m["worker_alive"],
        "active_jobs": m["active_jobs"],
        "queued_jobs": m["queued_jobs"],
    }


@app.get(
    "/health",
    tags=["Health"],
    summary="Health check — always returns 200 if the server is up",
)
@app.get("/healthz", tags=["Health"], include_in_schema=False)  # GCP-style alias
def health():
    m = get_metrics()
    return {
        "status": "ok",
        "uptime_seconds": m["uptime_seconds"],
        "active_jobs": m["active_jobs"],
        "queued_jobs": m["queued_jobs"],
        "worker_alive": m["worker_alive"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scrape endpoints  (all require API key)
# ─────────────────────────────────────────────────────────────────────────────

@app.post(
    "/scrape/search",
    tags=["Scrape"],
    summary="Start a search + scrape job",
    status_code=status.HTTP_202_ACCEPTED,
)
def scrape_search(req: SearchRequest, _=Depends(verify_api_key)):
    """
    Submit a Google Maps search query.
    The job is added to the queue and processed as soon as the current job
    (if any) completes.  Poll **GET /jobs/{job_id}** to track progress.

    - **status="queued"** → waiting in queue
    - **status="processing"** → currently running
    - **status="available"** → done, results in Google Sheets
    """
    params = req.model_dump()
    job_id = enqueue_job("search", params)
    jstatus = get_job_status(job_id)
    return {
        "job_id": job_id,
        "status": _map_status(jstatus["status"]),
        "queue_position": get_queue_position(job_id),
        "queue_length": get_queue_length(),
        "message": "Job queued successfully. Poll /jobs/{job_id} for live status.",
        "poll_url": f"/jobs/{job_id}",
    }


@app.post(
    "/scrape/place",
    tags=["Scrape"],
    summary="Scrape a single Google Maps place",
    status_code=status.HTTP_202_ACCEPTED,
)
def scrape_place(req: PlaceRequest, _=Depends(verify_api_key)):
    """
    Scrape full details + all reviews for a specific Google Maps **place_id**.
    Queued like any other job — poll **GET /jobs/{job_id}** for progress.
    """
    params = req.model_dump()
    job_id = enqueue_job("place", params)
    jstatus = get_job_status(job_id)
    return {
        "job_id": job_id,
        "status": _map_status(jstatus["status"]),
        "queue_position": get_queue_position(job_id),
        "queue_length": get_queue_length(),
        "message": "Place scrape queued. Poll /jobs/{job_id} for status.",
        "poll_url": f"/jobs/{job_id}",
    }


@app.post(
    "/scrape/resume",
    tags=["Scrape"],
    summary="Resume an interrupted scrape job",
    status_code=status.HTTP_202_ACCEPTED,
)
def scrape_resume(req: ResumeRequest, _=Depends(verify_api_key)):
    """
    Re-queue a previously interrupted or paused job.
    The original **job_id** is reused so progress continues from where it left off.
    """
    params = req.model_dump()
    job_id = enqueue_job("resume", params)
    jstatus = get_job_status(job_id)
    return {
        "job_id": job_id,
        "status": _map_status(jstatus["status"] if jstatus else "queued"),
        "queue_position": get_queue_position(job_id),
        "message": "Resume job queued. Poll /jobs/{job_id} for status.",
        "poll_url": f"/jobs/{job_id}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Job management endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/jobs",
    tags=["Jobs"],
    summary="List all tracked jobs",
)
def list_jobs(_=Depends(verify_api_key)):
    """
    Returns all jobs tracked in this API instance's memory.

    For historical jobs (from previous deployments) use **GET /jobs/{job_id}**
    which also falls back to Google Sheets state.
    """
    jobs = [_enrich_job(j) for j in get_all_jobs()]
    return {
        "jobs": jobs,
        "total": len(jobs),
        "processing": sum(1 for j in jobs if j["status"] == "processing"),
        "queued": sum(1 for j in jobs if j["status"] == "queued"),
        "queue_length": get_queue_length(),
    }


@app.get(
    "/jobs/{job_id}",
    tags=["Jobs"],
    summary="Get live status of a specific job",
)
def get_job(job_id: str, _=Depends(verify_api_key)):
    """
    Returns the current status for the given job.

    | status | meaning |
    |--------|---------|
    | `queued` | waiting behind another job |
    | `processing` | actively scraping right now |
    | `available` | finished — check `spreadsheet_url` for results |
    | `failed` | error — check `error` field |
    | `cancelled` | cancelled via DELETE |

    If the job is not in memory (e.g. previous deployment), this endpoint
    falls back to the Google Sheets state file stored in Drive.
    """
    # 1. Check in-memory first (fastest path)
    jstatus = get_job_status(job_id)
    if jstatus:
        return _enrich_job(jstatus)

    # 2. Fall back to Sheets DB (historical jobs / after restart)
    try:
        db = _get_db()
        job = db.get_job(job_id)
        if job:
            raw_status = job.get("status", "")
            places_total = job.get("places_total", 0)
            places_done = job.get("places_done", 0)
            pct = int(100 * places_done / places_total) if places_total else (
                100 if raw_status == "done" else 0
            )
            return {
                "job_id": job_id,
                "query": job.get("query", ""),
                "status": _map_status(raw_status),
                "places_found": places_total,
                "places_done": places_done,
                "reviews_done": job.get("reviews_done", 0),
                "progress_pct": pct,
                "started_at": job.get("created_at"),
                "updated_at": job.get("updated_at"),
                "spreadsheet_url": job.get("spreadsheet_url"),
                "error": job.get("error"),
                "queue_position": None,
                "source": "sheets_db",
            }
    except Exception as exc:
        logger.warning("Sheets DB fallback failed for job %s: %s", job_id, exc)

    raise HTTPException(
        status_code=404,
        detail=f"Job '{job_id}' not found. It may belong to a previous deployment.",
    )


@app.delete(
    "/jobs/{job_id}",
    tags=["Jobs"],
    summary="Cancel a queued or running job",
)
def delete_job(job_id: str, _=Depends(verify_api_key)):
    """
    Requests cancellation of a job.
    - If the job is **queued**, it is removed before processing starts.
    - If the job is **processing**, the current batch completes then stops.
    - Terminal jobs (available / failed / cancelled) cannot be cancelled.
    """
    cancelled = cancel_job(job_id)
    if not cancelled:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found or already in a terminal state.",
        )
    return {
        "job_id": job_id,
        "status": "cancelled",
        "message": "Cancellation requested. The job will stop after the current operation.",
    }


@app.get(
    "/jobs/{job_id}/places",
    tags=["Jobs"],
    summary="Get all places scraped for a job",
)
def get_job_places(job_id: str, _=Depends(verify_api_key)):
    """
    Fetches the **Places** sheet from the Google Spreadsheet for this job.
    Returns a list of all scraped place records.

    ⚠️ This calls the Sheets API — may take a moment for large datasets.
    """
    try:
        db = _get_db()
        job_info = db.get_job(job_id)
        if not job_info:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found in Sheets DB")

        from sheets_db import PLACE_HEADERS
        cache = db._get_or_create_job_cache(job_id)
        sid = cache.get("spreadsheet_id")
        if not sid:
            return {
                "job_id": job_id,
                "places": [],
                "total": 0,
                "spreadsheet_url": job_info.get("spreadsheet_url"),
                "message": "Spreadsheet not yet created (job may still be queued)",
            }

        places = db._get_all_rows(sid, "Places", PLACE_HEADERS)
        return {
            "job_id": job_id,
            "places": places,
            "total": len(places),
            "spreadsheet_url": job_info.get("spreadsheet_url"),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch places for job %s: %s", job_id, exc)
        raise HTTPException(status_code=500, detail=f"Sheets API error: {exc}")


@app.get(
    "/jobs/{job_id}/stats",
    tags=["Jobs"],
    summary="Get scraped counts for a job",
)
def get_job_stats(job_id: str, _=Depends(verify_api_key)):
    """
    Returns place and review counts for a job.
    Reads from the Google Sheets state — reflects the actual saved rows.
    """
    try:
        db = _get_db()
        job_info = db.get_job(job_id)
        if not job_info:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        stats = db.get_stats(job_id=job_id)
        return {
            "job_id": job_id,
            "query": job_info.get("query", ""),
            "status": _map_status(job_info.get("status", "")),
            "spreadsheet_url": job_info.get("spreadsheet_url"),
            **stats,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sheets API error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Monitoring endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/monitor",
    tags=["Monitor"],
    summary="System metrics — memory, CPU, job counts, uptime",
)
def monitor(_=Depends(verify_api_key)):
    return get_metrics()


@app.get(
    "/monitor/metrics",
    tags=["Monitor"],
    summary="Prometheus-style text metrics",
    response_class=PlainTextResponse,
)
def prometheus_metrics(_=Depends(verify_api_key)):
    """
    Exposes key metrics in Prometheus text format.
    Can be scraped by Prometheus, Google Cloud Monitoring, etc.
    """
    m = get_metrics()
    lines = [
        "# HELP gmaps_uptime_seconds API server uptime in seconds",
        "# TYPE gmaps_uptime_seconds counter",
        f"gmaps_uptime_seconds {m['uptime_seconds']}",
        "",
        "# HELP gmaps_active_jobs Currently processing jobs",
        "# TYPE gmaps_active_jobs gauge",
        f"gmaps_active_jobs {m['active_jobs']}",
        "",
        "# HELP gmaps_queued_jobs Jobs waiting in queue",
        "# TYPE gmaps_queued_jobs gauge",
        f"gmaps_queued_jobs {m['queued_jobs']}",
        "",
        "# HELP gmaps_total_jobs_enqueued Total jobs submitted to the queue",
        "# TYPE gmaps_total_jobs_enqueued counter",
        f"gmaps_total_jobs_enqueued {m['total_jobs_enqueued']}",
        "",
        "# HELP gmaps_completed_jobs Jobs that completed successfully",
        "# TYPE gmaps_completed_jobs counter",
        f"gmaps_completed_jobs {m['completed_jobs']}",
        "",
        "# HELP gmaps_failed_jobs Jobs that failed",
        "# TYPE gmaps_failed_jobs counter",
        f"gmaps_failed_jobs {m['failed_jobs']}",
        "",
        "# HELP gmaps_memory_mb Process memory usage in MB",
        "# TYPE gmaps_memory_mb gauge",
        f"gmaps_memory_mb {m['memory_mb']}",
        "",
        "# HELP gmaps_cpu_percent Process CPU usage percent",
        "# TYPE gmaps_cpu_percent gauge",
        f"gmaps_cpu_percent {m['cpu_percent']}",
    ]
    return "\n".join(lines)
