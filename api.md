# Google Maps Scraper — API Documentation & Cloud Run Deployment Guide

> **Base URL (Local):** `http://localhost:8000`  
> **Base URL (Cloud Run):** `https://gmaps-scraper-api-XXXX-uc.a.run.app`  
> **Interactive Docs:** `{BASE_URL}/docs`

---

## Table of Contents

1. [Authentication](#authentication)
2. [API Endpoints](#api-endpoints)
   - [GET /wakeup](#get-wakeup)
   - [GET /health](#get-health)
   - [POST /scrape/search](#post-scrapesearch)
   - [POST /scrape/place](#post-scrapeplace)
   - [POST /scrape/resume](#post-scraperesume)
   - [GET /jobs](#get-jobs)
   - [GET /jobs/{job_id}](#get-jobsjob_id)
   - [DELETE /jobs/{job_id}](#delete-jobsjob_id)
   - [GET /jobs/{job_id}/places](#get-jobsjob_idplaces)
   - [GET /jobs/{job_id}/stats](#get-jobsjob_idstats)
   - [GET /monitor](#get-monitor)
   - [GET /monitor/metrics](#get-monitormetrics)
3. [Job Status Reference](#job-status-reference)
4. [Parameter Reference](#parameter-reference)
5. [Frontend Integration Guide](#frontend-integration-guide)
6. [Cloud Run Deployment Guide](#cloud-run-deployment-guide)

---

## Authentication

All endpoints **except** `/wakeup`, `/health`, `/healthz`, and `/` require the `X-API-Key` header.

```
X-API-Key: your_secret_key_here
```

> **Dev mode:** If `API_KEY` is not set on the server, auth is disabled and the header is not required.

---

## API Endpoints

---

### `GET /wakeup`

**No authentication required.**

Use this endpoint to detect if the Cloud Run container is awake. Call it when your frontend page loads.

| State | Response time | What to show |
|-------|--------------|--------------|
| Container cold-starting | 5–30 seconds | `"Waking up..."` |
| Container already warm | < 100ms | `"Active"` |

**Response `200 OK`:**
```json
{
  "status": "active",
  "message": "Server is awake and ready",
  "uptime_seconds": 43,
  "worker_alive": true,
  "active_jobs": 0,
  "queued_jobs": 0
}
```

**Frontend usage:**
```js
setServerStatus('waking_up');  // show "Waking up..." before calling

fetch(`${BASE_URL}/wakeup`)
  .then(r => r.json())
  .then(data => setServerStatus('active'))    // show "Active"
  .catch(() => setServerStatus('offline'));   // show "Offline"
```

---

### `GET /health`

**No authentication required.**

Simple health check for Cloud Run and uptime monitoring.

**Response `200 OK`:**
```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "active_jobs": 0,
  "queued_jobs": 1,
  "worker_alive": true
}
```

Also available at `/healthz` (same response).

---

### `POST /scrape/search`

**Authentication required.**

Submit a Google Maps search query. All matching places are scraped along with their reviews. The job is added to a queue and processed one at a time.

**Request Body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | `string` | **required** | Google Maps search query e.g. `"Restaurants in Mumbai"` |
| `max_places` | `int` | `250` | Max number of places to scrape |
| `max_reviews` | `int` | `0` | Reviews per place — `0`=skip, any number=fetch that many |
| `workers` | `int` | `4` | Concurrent sessions for bot bypass (1–16) |
| `delay` | `float` | `2.5` | Min delay in seconds between requests per worker |
| `proxy` | `string` | `null` | Proxy URL e.g. `"http://user:pass@host:port"` or `"socks5://host:1080"` |
| `extract_emails` | `bool` | `false` | Visit each website to extract email if not in Google Maps |
| `lang` | `string` | `"en"` | Language code: `"en"`, `"hi"`, `"ar"`, `"fr"` etc. |
| `gl` | `string` | `"us"` | Country code: `"in"` (India), `"us"`, `"gb"` etc. |
| `lat` | `float` | `0.0` | Latitude bias for search center |
| `lng` | `float` | `0.0` | Longitude bias for search center |
| `zoom` | `int` | `13` | Zoom level for search grid (1–21, higher = tighter area) |
| `job_id` | `string` | auto | Custom job ID (auto-generated 16-char hex if omitted) |

**Minimal request (only required field):**
```json
{
  "query": "Hotels in Delhi"
}
```

**Full request example:**
```json
{
  "query": "Restaurants in Mumbai",
  "max_places": 50,
  "max_reviews": 100,
  "workers": 4,
  "delay": 2.5,
  "proxy": null,
  "extract_emails": false,
  "lang": "en",
  "gl": "in",
  "lat": 19.0760,
  "lng": 72.8777
}
```

**Response `202 Accepted`:**
```json
{
  "job_id": "845b2195ee9d725c",
  "status": "queued",
  "queue_position": 1,
  "queue_length": 1,
  "message": "Job queued successfully. Poll /jobs/{job_id} for live status.",
  "poll_url": "/jobs/845b2195ee9d725c"
}
```

**curl:**
```bash
curl -X POST https://YOUR-API.run.app/scrape/search \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Restaurants in Mumbai",
    "max_places": 50,
    "max_reviews": 100,
    "workers": 4,
    "lang": "en",
    "gl": "in"
  }'
```

---

### `POST /scrape/place`

**Authentication required.**

Scrape a single Google Maps place by its Place ID.

**Request Body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `place_id` | `string` | **required** | Google Maps place ID e.g. `"ChIJN1t_tDeuEmsRUsoyG83frY4"` |
| `max_reviews` | `int` | `0` | Reviews — `0`=skip, any number=fetch that many |
| `workers` | `int` | `4` | Concurrent sessions (1–16) |
| `delay` | `float` | `2.5` | Min delay between requests |
| `proxy` | `string` | `null` | Proxy URL |
| `extract_emails` | `bool` | `false` | Visit website to extract email |
| `lang` | `string` | `"en"` | Language code |
| `gl` | `string` | `"us"` | Country code |
| `lat` | `float` | `0.0` | Latitude hint (optional) |
| `lng` | `float` | `0.0` | Longitude hint (optional) |

```json
{
  "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
  "max_reviews": 200,
  "extract_emails": true
}
```

**Response `202 Accepted`:**
```json
{
  "job_id": "f7a3c2d1e5b9a0f4",
  "status": "queued",
  "queue_position": 1,
  "queue_length": 1,
  "message": "Place scrape queued. Poll /jobs/{job_id} for status.",
  "poll_url": "/jobs/f7a3c2d1e5b9a0f4"
}
```

---

### `POST /scrape/resume`

**Authentication required.**

Resume a previously interrupted or cancelled job. Continues scraping from where it stopped.

**Request Body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `job_id` | `string` | **required** | The job ID to resume |
| `max_reviews` | `int` | `null` | Override review limit for the resumed run |

```json
{
  "job_id": "845b2195ee9d725c",
  "max_reviews": 50
}
```

**Response `202 Accepted`:**
```json
{
  "job_id": "845b2195ee9d725c",
  "status": "queued",
  "queue_position": 1,
  "message": "Resume job queued. Poll /jobs/{job_id} for status.",
  "poll_url": "/jobs/845b2195ee9d725c"
}
```

---

### `GET /jobs`

**Authentication required.**

List all jobs tracked in the current API instance. Returns jobs sorted by most recently updated.

**Response `200 OK`:**
```json
{
  "jobs": [
    {
      "job_id": "845b2195ee9d725c",
      "query": "Restaurants in Mumbai",
      "status": "available",
      "places_found": 50,
      "places_done": 50,
      "reviews_done": 4820,
      "progress_pct": 100,
      "started_at": "2026-07-19T10:00:00Z",
      "updated_at": "2026-07-19T10:45:00Z",
      "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1abc...",
      "error": null,
      "queue_position": null
    }
  ],
  "total": 1,
  "processing": 0,
  "queued": 0,
  "queue_length": 0
}
```

**curl:**
```bash
curl https://YOUR-API.run.app/jobs -H "X-API-Key: YOUR_KEY"
```

---

### `GET /jobs/{job_id}`

**Authentication required.**

Get live status of a specific job. Falls back to Google Sheets state for historical jobs (from previous deployments).

**Response `200 OK` — while processing:**
```json
{
  "job_id": "845b2195ee9d725c",
  "query": "Restaurants in Mumbai",
  "status": "processing",
  "places_found": 50,
  "places_done": 12,
  "reviews_done": 340,
  "progress_pct": 24,
  "started_at": "2026-07-19T10:00:00Z",
  "updated_at": "2026-07-19T10:05:00Z",
  "spreadsheet_url": null,
  "error": null,
  "queue_position": null
}
```

**Response `200 OK` — when done (`available`):**
```json
{
  "job_id": "845b2195ee9d725c",
  "query": "Restaurants in Mumbai",
  "status": "available",
  "places_found": 50,
  "places_done": 50,
  "reviews_done": 4820,
  "progress_pct": 100,
  "started_at": "2026-07-19T10:00:00Z",
  "updated_at": "2026-07-19T10:45:00Z",
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1abc...",
  "error": null,
  "queue_position": null
}
```

**Response `404 Not Found`:**
```json
{
  "detail": "Job '845b2195ee9d725c' not found. It may belong to a previous deployment."
}
```

---

### `DELETE /jobs/{job_id}`

**Authentication required.**

Cancel a queued or running job.

- **Queued** jobs: removed before they start
- **Processing** jobs: stop after the current batch completes
- **Terminal** jobs (available/failed/cancelled): returns 404

**Response `200 OK`:**
```json
{
  "job_id": "845b2195ee9d725c",
  "status": "cancelled",
  "message": "Cancellation requested. The job will stop after the current operation."
}
```

**curl:**
```bash
curl -X DELETE https://YOUR-API.run.app/jobs/845b2195ee9d725c \
  -H "X-API-Key: YOUR_KEY"
```

---

### `GET /jobs/{job_id}/places`

**Authentication required.**

Fetch all scraped place records for a job directly from Google Sheets.

> ⚠️ Calls the Sheets API — may take a few seconds for large datasets.

**Response `200 OK`:**
```json
{
  "job_id": "845b2195ee9d725c",
  "total": 50,
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1abc...",
  "places": [
    {
      "place_id": "ChIJ...",
      "name": "Taj Mahal Palace",
      "address": "Apollo Bunder, Colaba, Mumbai",
      "rating": "4.6",
      "review_count": "32543",
      "phone": "+91 22 6665 3366",
      "email": "",
      "website": "https://www.tajhotels.com",
      "categories": "[\"Hotel\", \"5-star hotel\"]",
      "hours": "{}",
      "lat": "18.9217",
      "lng": "72.8332",
      "reviews_fetched": "1",
      "reviews_total_saved": "100"
    }
  ]
}
```

---

### `GET /jobs/{job_id}/stats`

**Authentication required.**

Quick count of scraped records from Google Sheets.

**Response `200 OK`:**
```json
{
  "job_id": "845b2195ee9d725c",
  "query": "Restaurants in Mumbai",
  "status": "available",
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/1abc...",
  "places": 50,
  "reviews": 4820,
  "pending_reviews": 0
}
```

---

### `GET /monitor`

**Authentication required.**

Full system metrics — memory, CPU, uptime, and job counts.

**Response `200 OK`:**
```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "active_jobs": 1,
  "queued_jobs": 2,
  "total_jobs_tracked": 15,
  "total_jobs_enqueued": 15,
  "completed_jobs": 12,
  "failed_jobs": 1,
  "cancelled_jobs": 1,
  "memory_mb": 128.4,
  "cpu_percent": 45.2,
  "worker_alive": true,
  "version": "1.0.0"
}
```

---

### `GET /monitor/metrics`

**Authentication required.**

Prometheus-style text metrics for dashboards (Grafana, Google Cloud Monitoring, etc.).

**Response `200 OK` (`text/plain`):**
```
gmaps_uptime_seconds 3600
gmaps_active_jobs 1
gmaps_queued_jobs 2
gmaps_total_jobs_enqueued 15
gmaps_completed_jobs 12
gmaps_failed_jobs 1
gmaps_memory_mb 128.4
gmaps_cpu_percent 45.2
```

---

## Job Status Reference

| `status` | Meaning | What to show in UI |
|----------|---------|-------------------|
| `queued` | Waiting behind another job | `"Queued — position 1"` |
| `processing` | Actively scraping right now | `"Scraping... 24%"` |
| `available` | Finished — results in Sheets | `"✅ Done"` + open Sheet link |
| `failed` | Error occurred | `"❌ Failed: <error>"` |
| `cancelled` | Cancelled by DELETE request | `"Cancelled"` |

---

## Parameter Reference

### `workers` — Bot Bypass / Concurrent Sessions

Each worker is an **independent browser-like HTTP session**. More workers = faster scraping but slightly more detectable.

| Value | Speed | Recommendation |
|-------|-------|----------------|
| `1` | Slowest | Testing only |
| `4` | Good | **Default — best balance** |
| `8` | Fast | Large scrapes |
| `16` | Fastest | Use with `proxy` for safety |

### `max_reviews`

| Value | Behaviour |
|-------|-----------|
| `0` | **Default** — skip reviews completely (fastest) |
| `50` | Fetch up to 50 reviews per place |
| `500` | Fetch up to 500 reviews per place |
| `99999` | Fetch ALL reviews (very slow for popular places) |

### `max_places`

| Value | Behaviour |
|-------|-----------|
| `250` | **Default** |
| `50` | Scrape only first 50 results |
| `1000` | Scrape up to 1000 results |

### `proxy`

Optional. Routes all traffic through a proxy server.  
Format: `"http://user:pass@host:port"` or `"socks5://host:port"`

### `gl` — Country Code Examples

| Code | Country |
|------|---------|
| `"in"` | India |
| `"us"` | United States |
| `"gb"` | United Kingdom |
| `"ae"` | UAE |
| `"sg"` | Singapore |

### `lang` — Language Code Examples

| Code | Language |
|------|----------|
| `"en"` | English |
| `"hi"` | Hindi |
| `"ar"` | Arabic |
| `"fr"` | French |

---

## Frontend Integration Guide

### Complete JavaScript Example

```js
const API_BASE = 'https://YOUR-API.run.app';  // or http://localhost:8000 locally
const API_KEY  = 'your_secret_key';

// ── 1. Wake up server on page load ──────────────────────────────────────────
async function wakeupServer() {
  setServerStatus('waking_up');   // Show "Waking up..."
  try {
    const res = await fetch(`${API_BASE}/wakeup`);
    const data = await res.json();
    setServerStatus('active');    // Show "Active ✅"
    return data;
  } catch (err) {
    setServerStatus('offline');   // Show "Offline ❌"
  }
}

// ── 2. Start a scrape job ────────────────────────────────────────────────────
async function startScrape({ query, maxPlaces = 250, maxReviews = 0, workers = 4, gl = 'in' }) {
  const res = await fetch(`${API_BASE}/scrape/search`, {
    method: 'POST',
    headers: {
      'X-API-Key': API_KEY,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query,
      max_places: maxPlaces,
      max_reviews: maxReviews,
      workers,
      gl,
      lang: 'en',
    }),
  });
  return res.json();
  // Returns: { job_id, status, queue_position, poll_url }
}

// ── 3. Poll job status ───────────────────────────────────────────────────────
async function pollJobStatus(jobId, onUpdate, intervalMs = 4000) {
  const poll = async () => {
    const res = await fetch(`${API_BASE}/jobs/${jobId}`, {
      headers: { 'X-API-Key': API_KEY },
    });
    const job = await res.json();
    onUpdate(job);

    const isTerminal = ['available', 'failed', 'cancelled'].includes(job.status);
    if (!isTerminal) setTimeout(poll, intervalMs);
  };
  await poll();
}

// ── 4. Get all jobs ──────────────────────────────────────────────────────────
async function listJobs() {
  const res = await fetch(`${API_BASE}/jobs`, {
    headers: { 'X-API-Key': API_KEY },
  });
  return res.json();
  // Returns: { jobs: [...], total, processing, queued }
}

// ── 5. Cancel a job ──────────────────────────────────────────────────────────
async function cancelJob(jobId) {
  await fetch(`${API_BASE}/jobs/${jobId}`, {
    method: 'DELETE',
    headers: { 'X-API-Key': API_KEY },
  });
}

// ── Usage example ────────────────────────────────────────────────────────────
wakeupServer();   // Call on every page load

const { job_id } = await startScrape({
  query: 'Restaurants in Hyderabad',
  maxPlaces: 100,
  maxReviews: 50,
  workers: 4,
  gl: 'in',
});

pollJobStatus(job_id, (job) => {
  if (job.status === 'queued')      console.log(`Queued — position ${job.queue_position}`);
  if (job.status === 'processing')  console.log(`${job.progress_pct}% — ${job.places_done}/${job.places_found} places | ${job.reviews_done} reviews`);
  if (job.status === 'available')   console.log(`Done! Open: ${job.spreadsheet_url}`);
  if (job.status === 'failed')      console.log(`Error: ${job.error}`);
});
```

---

## Cloud Run Deployment Guide

### Prerequisites

- [Google Cloud account](https://cloud.google.com) with billing enabled
- [Google Cloud SDK (`gcloud`)](https://cloud.google.com/sdk/docs/install) installed and initialized
- Docker Desktop installed (for local testing only)
- The project files ready with `credentials.json` and `token.json` present

---

### Step 1 — Install & Initialize gcloud CLI

```bash
# Download from https://cloud.google.com/sdk/docs/install
# Then initialize:
gcloud init
```

Follow the prompts to log in and select your Google account.

---

### Step 2 — Create or Select a GCP Project

```bash
# Create a new project (skip if you have one already)
gcloud projects create gmaps-scraper-prod --name="GMaps Scraper"

# Set it as active
gcloud config set project gmaps-scraper-prod
```

---

### Step 3 — Enable Required GCP APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  containerregistry.googleapis.com \
  drive.googleapis.com \
  sheets.googleapis.com
```

---

### Step 4 — Generate Your API Key

```bash
# Run this in your terminal — copy the output and save it securely
python -c "import secrets; print(secrets.token_hex(32))"
```

Add the key to your `.env` file:
```
API_KEY=your_generated_key_here
ALLOWED_ORIGINS=https://yourfrontend.com
```

---

### Step 5 — Verify Files Are Present

Make sure these files exist in your project directory before deploying:

```
google-maps-scraper/
├── credentials.json    ← OAuth client secret (must exist)
├── token.json          ← OAuth token with refresh_token (must exist)
├── .env                ← your environment variables
├── Dockerfile          ← already created
├── cloudbuild.yaml     ← already created
└── api/                ← already created
```

---

### Step 6 — Deploy with Cloud Build (Recommended)

This single command builds the Docker image, pushes it to Container Registry, and deploys to Cloud Run:

```bash
# From inside the google-maps-scraper/ directory:
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions _REGION=asia-south1,_SERVICE=gmaps-scraper-api \
  .
```

> **Region options:** `asia-south1` (Mumbai), `us-central1`, `europe-west1`, `asia-southeast1` (Singapore)

This will take **3–5 minutes** on first deploy. You'll see output like:
```
Step #0 - "build": Successfully built abc123
Step #1 - "push":  Pushed gcr.io/gmaps-scraper-prod/gmaps-scraper-api
Step #2 - "deploy": Service [gmaps-scraper-api] revision deployed
Service URL: https://gmaps-scraper-api-xxxx-el.a.run.app
```

**Save the Service URL** — that's your API base URL.

---

### Step 7 — Set Environment Variables in Cloud Run

After first deploy, set your secret environment variables in the GCP Console:

1. Go to [GCP Console → Cloud Run](https://console.cloud.google.com/run)
2. Click your service **`gmaps-scraper-api`**
3. Click **Edit & Deploy New Revision**
4. Scroll to **Environment Variables** section
5. Add these variables:

| Variable | Value |
|----------|-------|
| `API_KEY` | your generated secret key |
| `SHARE_WITH_EMAIL` | `bhanutejamakkineni@gmail.com` |
| `GOOGLE_DRIVE_FOLDER_ID` | `1pBp2CphmuqhBmiGGuLmeAi4Diy7b1MQq` |
| `ALLOWED_ORIGINS` | `https://yourfrontend.com` |
| `GOOGLE_CREDENTIALS_FILE` | `credentials.json` |

6. Click **Deploy**

---

### Step 8 — Verify the Deployment

```bash
# Replace with your actual Cloud Run URL
BASE=https://gmaps-scraper-api-xxxx-el.a.run.app

# 1. Wake up the server
curl $BASE/wakeup
# Expected: {"status":"active","message":"Server is awake and ready",...}

# 2. Health check
curl $BASE/health
# Expected: {"status":"ok","worker_alive":true,...}

# 3. Test a scrape (replace YOUR_KEY)
curl -X POST $BASE/scrape/search \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"test cafe", "max_places":2}'

# 4. Check job status (use job_id from step 3)
curl $BASE/jobs/JOB_ID_HERE -H "X-API-Key: YOUR_KEY"
```

---

### Step 9 — (Optional) Set Up Auto-Deploy on Git Push

Connect your repository to Cloud Build for automatic deployments:

```bash
# In GCP Console → Cloud Build → Triggers → Create Trigger
# - Connect your GitHub/GitLab repo
# - Set trigger to: Push to main branch
# - Build config: cloudbuild.yaml
```

Every `git push` to main will automatically rebuild and redeploy.

---

### Cloud Run Settings Explained

The `cloudbuild.yaml` deploys with these settings (already configured):

| Setting | Value | Why |
|---------|-------|-----|
| `--min-instances=0` | 0 | Scales to ZERO when idle — saves money ($0 cost when not in use) |
| `--max-instances=1` | 1 | Single instance required (job queue is in-memory) |
| `--memory=1Gi` | 1 GB | Enough for browser sessions + Sheets API |
| `--cpu=2` | 2 vCPU | Handles 4 concurrent workers smoothly |
| `--timeout=3600` | 60 min | Long scrapes can take hours |
| `--allow-unauthenticated` | yes | Your API key handles auth at the app level |

---

### Auto-Restart on Crash

Cloud Run **automatically restarts** the container whenever it:
- Crashes / throws an unhandled exception
- Runs out of memory
- Becomes unresponsive

Because `min-instances=0`, if it restarts due to a crash, the next request will trigger a cold start (15–30 seconds).

---

### Estimated Cloud Run Cost

With `min-instances=0` (scale-to-zero):

| Resource | Cost |
|----------|------|
| Always-on | **$0/month** |
| Active usage | fractions of a cent per request |
| **Estimated total** | **$0.00/month** (unless you do millions of scrapes) |

> Because it scales to zero, the very first request (your frontend's `/wakeup` call) will take 15–30 seconds to wake up the server. After that, it stays warm while in use.

---

### Troubleshooting

**Problem:** Deploy fails with `permission denied`  
**Fix:** Run `gcloud auth login` and `gcloud auth configure-docker`

**Problem:** API returns 500 on first scrape  
**Fix:** Check Cloud Run logs → GCP Console → Cloud Run → Logs. Usually a missing env var.

**Problem:** Google Sheets permission error  
**Fix:** The `token.json` may need refreshing. Run the project locally once (`python main.py stats`) to re-authenticate, then redeploy.

**Problem:** Jobs show `failed` with auth error  
**Fix:** Make sure `SHARE_WITH_EMAIL` and `GOOGLE_DRIVE_FOLDER_ID` are set in Cloud Run env vars.

**View live logs:**
```bash
gcloud run services logs read gmaps-scraper-api \
  --region=asia-south1 \
  --limit=50
```
