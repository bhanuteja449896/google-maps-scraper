# Google Maps Scraper — Cloud Run Deployment Guide

This guide walks you through deploying your API to Google Cloud Run from scratch.

> **Note on Security:** As requested, `API_KEY` authentication has been made entirely optional and CORS is open (`*`). Anyone with your Cloud Run URL can make requests to it.

---

### Step 1: Install & Initialize Google Cloud SDK

1. Download and install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) for Windows.
2. Open your terminal (PowerShell or Command Prompt) and run:
   ```bash
   gcloud init
   ```
3. A browser window will open. Log in with your Google account.
4. When prompted in the terminal, select **Create a new project** (or select an existing one) and name it something like `gmaps-scraper`.

---

### Step 2: Set the Active Project

If you created a new project named `gmaps-scraper-123` (check the exact ID shown in `gcloud init`), set it as active:

```bash
gcloud config set project YOUR_PROJECT_ID
```

---

### Step 3: Enable Required Google Cloud APIs

Run this command to enable the necessary services for your project:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com drive.googleapis.com sheets.googleapis.com
```

---

### Step 4: Verify Files

Ensure you are inside the `google-maps-scraper` folder and these files exist:
- `credentials.json` (The Google Sheets/Drive OAuth client secret)
- `token.json` (Your authorized token)
- `Dockerfile`
- `cloudbuild.yaml`

---

### Step 5: Build and Deploy

Run this single command to build the Docker image and deploy it to Cloud Run:

```bash
gcloud builds submit --config cloudbuild.yaml --substitutions="_REGION=asia-south1,_SERVICE=gmaps-scraper-api" .
```

*Note: `asia-south1` is Mumbai. You can change this to `us-central1` or another region if preferred.*

This process will take **3–5 minutes**. When finished, it will print a **Service URL** (e.g., `https://gmaps-scraper-api-xxxx-el.a.run.app`). **Save this URL.**

---

### Step 6: Set Environment Variables in Cloud Console

1. Open your browser and go to the [Google Cloud Run Console](https://console.cloud.google.com/run).
2. Click on your service: **`gmaps-scraper-api`**.
3. Click **Edit & Deploy New Revision** at the top.
4. Scroll down to the **Variables & Secrets** tab.
5. Under **Environment variables**, click **Add Variable** and add:
   - Name: `SHARE_WITH_EMAIL`
   - Value: `bhanutejamakkineni@gmail.com` (Your Gmail address)
6. Add another variable:
   - Name: `GOOGLE_DRIVE_FOLDER_ID`
   - Value: `1pBp2CphmuqhBmiGGuLmeAi4Diy7b1MQq` (Your Google Drive folder ID)
7. Click **Deploy** at the bottom.

---

### Step 7: Test Your Deployment

Once the new revision is deployed, test it using your Service URL:

1. **Wake up the server (Cold Start):**
   Open in browser: `https://YOUR_SERVICE_URL/wakeup`
   *(It will take 15–30 seconds the first time, then respond with `"status": "active"`)*

2. **Run a test scrape:**
   Send a POST request (via Postman, curl, or your frontend):
   ```bash
   curl -X POST https://YOUR_SERVICE_URL/scrape/search \
     -H "Content-Type: application/json" \
     -d '{"query":"test cafe", "max_places":2}'
   ```

---

### Cost & Scaling Behavior

- The deployment is configured with `--min-instances=0`.
- This means when the API is not being used, it completely shuts down, resulting in **$0 cost**.
- When you send a request after it has shut down, it performs a **"cold start"**, taking 15–30 seconds to wake up (which is why your frontend should call `/wakeup` on load).
- After waking up, it stays warm for a few minutes to quickly handle subsequent requests.
