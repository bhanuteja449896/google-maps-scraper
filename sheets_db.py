"""Google Sheets + Drive backed database — drop-in replacement for db.py.

Storage layout
--------------
Every scrape job gets its own Google Spreadsheet created by the service account.
Each spreadsheet has three sheets:

  • Places   — one row per scraped place
  • Reviews  — one row per review
  • Jobs     — job-level metadata and progress

Sharing
-------
If SHARE_WITH_EMAIL is set in .env, every new spreadsheet is automatically
shared with that email as Editor, so it appears in "Shared with me" in
Google Drive.  The GOOGLE_DRIVE_FOLDER_ID is now optional: if set and
accessible the spreadsheet is moved there; otherwise it lives in the
service account's own Drive (still fully accessible via the URL).

Environment variables (set in .env)
------------------------------------
  GOOGLE_CREDENTIALS_FILE  Path to Service Account or OAuth2 JSON (default: credentials.json)
  SHARE_WITH_EMAIL         Your personal Gmail — spreadsheets are shared with you automatically
  GOOGLE_DRIVE_FOLDER_ID   (optional) Drive folder ID to organise spreadsheets
"""

import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Column headers
# ─────────────────────────────────────────────────────────────────────────────

PLACE_HEADERS = [
    "place_id", "name", "address", "address_components", "plus_code",
    "lat", "lng", "rating", "review_count",
    "website", "phone", "email", "fax", "price_level",
    "description", "categories", "primary_type",
    "hours", "photos", "about", "menu",
    "booking_links", "social_links",
    "hotel_class", "business_status",
    "reviews_fetched", "reviews_cursor", "reviews_total_saved",
    "scraped_at",
]

REVIEW_HEADERS = [
    "review_id", "place_id",
    "reviewer_name", "reviewer_profile_url", "reviewer_avatar_url",
    "reviewer_user_id", "reviewer_review_count", "reviewer_is_local_guide",
    "rating", "text", "date", "language",
    "photos", "owner_reply", "owner_reply_date",
    "scraped_at",
]

JOB_HEADERS = [
    "job_id", "query", "status",
    "places_total", "places_done", "reviews_done",
    "created_at", "updated_at",
    "spreadsheet_url", "error",
]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _to_str(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, (list, dict)):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return str(obj)


def _col_letter(n: int) -> str:
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _build_media(content: str):
    from googleapiclient.http import MediaIoBaseUpload
    return MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")),
        mimetype="application/json",
        resumable=False,
    )


def _retry(fn, retries: int = 5, base_delay: float = 2.0):
    from googleapiclient.errors import HttpError
    delay = base_delay
    for attempt in range(retries):
        try:
            return fn()
        except HttpError as exc:
            if exc.resp.status in (429, 500, 503) and attempt < retries - 1:
                logger.warning(
                    "Sheets API rate-limit (attempt %d/%d) — sleeping %.1fs",
                    attempt + 1, retries, delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                raise


# ─────────────────────────────────────────────────────────────────────────────
# Main class
# ─────────────────────────────────────────────────────────────────────────────

class SheetsDatabase:
    """
    Google Sheets + Drive backed storage with the same interface as ``Database``
    in db.py, so it can be used as a drop-in replacement throughout the codebase.
    """

    def __init__(self, folder_id: str | None = None, creds_path: str | None = None):
        from utils.google_auth import build_services

        # folder_id is now optional
        self.folder_id = folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
        # Email to auto-share spreadsheets with (your personal Gmail)
        self.share_email = os.environ.get("SHARE_WITH_EMAIL", "").strip()

        self._sheets, self._drive = build_services(creds_path=creds_path)
        self._lock = threading.Lock()
        self._job_cache: dict[str, dict] = {}

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Spreadsheet creation
    # ──────────────────────────────────────────────────────────────────────

    def _create_spreadsheet(self, title: str) -> tuple[str, str]:
        """Create a new spreadsheet. Returns (sheet_id, url).

        When GOOGLE_DRIVE_FOLDER_ID is set, creates the file directly inside
        that folder using the Drive API (avoids the buggy 2-step move that
        fails with restricted Drive scopes).
        """
        if self.folder_id:
            # ── Create via Drive API so we can specify parents at creation ──
            # This is the only reliable way to place files in a specific folder
            # without needing the 'removeParents' permission.
            file_meta = {
                "name": title,
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": [self.folder_id],
            }
            try:
                drive_resp = _retry(lambda: self._drive.files().create(
                    body=file_meta,
                    fields="id",
                ).execute())
                sid = drive_resp["id"]
                logger.info("Created spreadsheet in Drive folder %s: %s", self.folder_id, sid)
            except Exception as exc:
                logger.warning(
                    "Could not create spreadsheet in Drive folder %s (%s). "
                    "Falling back to default location.", self.folder_id, exc
                )
                # Fallback: create via Sheets API (root of Drive)
                body = {
                    "properties": {"title": title},
                    "sheets": [
                        {"properties": {"title": "Places",  "index": 0, "sheetId": 0}},
                        {"properties": {"title": "Reviews", "index": 1, "sheetId": 1}},
                        {"properties": {"title": "Jobs",    "index": 2, "sheetId": 2}},
                    ],
                }
                resp = _retry(lambda: self._sheets.spreadsheets().create(body=body).execute())
                sid = resp["spreadsheetId"]
        else:
            # No folder specified — create via Sheets API (creates in root)
            body = {
                "properties": {"title": title},
                "sheets": [
                    {"properties": {"title": "Places",  "index": 0, "sheetId": 0}},
                    {"properties": {"title": "Reviews", "index": 1, "sheetId": 1}},
                    {"properties": {"title": "Jobs",    "index": 2, "sheetId": 2}},
                ],
            }
            resp = _retry(lambda: self._sheets.spreadsheets().create(body=body).execute())
            sid = resp["spreadsheetId"]

        url = f"https://docs.google.com/spreadsheets/d/{sid}"

        # When created via Drive API we need to add the sheet tabs via batchUpdate
        # because Drive API doesn't support specifying sheet structure at creation.
        if self.folder_id:
            try:
                # Add the three sheets (a new Google Sheet has one default sheet "Sheet1")
                requests = [
                    {"updateSheetProperties": {
                        "properties": {"sheetId": 0, "title": "Places", "index": 0},
                        "fields": "title,index",
                    }},
                    {"addSheet": {"properties": {"title": "Reviews", "index": 1, "sheetId": 1}}},
                    {"addSheet": {"properties": {"title": "Jobs",    "index": 2, "sheetId": 2}}},
                ]
                _retry(lambda: self._sheets.spreadsheets().batchUpdate(
                    spreadsheetId=sid,
                    body={"requests": requests},
                ).execute())
            except Exception as exc:
                logger.warning("Could not rename/add sheets, will use default layout: %s", exc)

        # Auto-share with the user's personal email so it appears in "Shared with me"
        if self.share_email:
            try:
                _retry(lambda: self._drive.permissions().create(
                    fileId=sid,
                    body={"type": "user", "role": "writer", "emailAddress": self.share_email},
                    sendNotificationEmail=False,
                ).execute())
                logger.info("Shared spreadsheet with %s", self.share_email)
            except Exception as exc:
                logger.warning("Could not share spreadsheet with %s: %s", self.share_email, exc)

        # Write header rows
        self._write_headers(sid)
        logger.info("Created spreadsheet '%s': %s", title, url)
        return sid, url


    def _write_headers(self, sid: str):
        data = [
            {"range": "Places!A1",  "values": [PLACE_HEADERS]},
            {"range": "Reviews!A1", "values": [REVIEW_HEADERS]},
            {"range": "Jobs!A1",    "values": [JOB_HEADERS]},
        ]
        body = {"valueInputOption": "RAW", "data": data}
        _retry(lambda: self._sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body=body
        ).execute())

    def _get_or_create_job_cache(self, job_id: str) -> dict:
        if job_id not in self._job_cache:
            state = self._load_state(job_id)
            if state:
                self._job_cache[job_id] = state
            else:
                self._job_cache[job_id] = {
                    "spreadsheet_id": None,
                    "spreadsheet_url": None,
                    "place_row": {},
                    "review_ids_seen": set(),
                    "job_row": 2,
                    "next_place_row": 2,
                    "next_review_row": 2,
                    "places_total": 0,
                    "places_done": 0,
                    "reviews_done": 0,
                    "query": "",
                    "status": "running",
                    "created_at": _now_iso(),
                    "pending_places": [],
                    "job_places_status": {},
                    "job_places_cursor": {},
                    "job_places_reviews": {},
                }
        return self._job_cache[job_id]

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Drive state file (resume support)
    # ──────────────────────────────────────────────────────────────────────

    def _state_filename(self, job_id: str) -> str:
        return f"{job_id}_state.json"

    def _save_state(self, job_id: str):
        cache = self._job_cache.get(job_id)
        if not cache:
            return
        data = {k: list(v) if isinstance(v, set) else v for k, v in cache.items()}
        content = json.dumps(data, ensure_ascii=False, indent=2)
        fname = self._state_filename(job_id)
        existing_id = self._find_drive_file(fname)
        media = _build_media(content)
        if existing_id:
            _retry(lambda: self._drive.files().update(
                fileId=existing_id, media_body=media
            ).execute())
        else:
            file_meta = {
                "name": fname,
                "mimeType": "application/json",
            }
            # Optionally place state file in configured folder
            if self.folder_id:
                file_meta["parents"] = [self.folder_id]
            _retry(lambda: self._drive.files().create(
                body=file_meta, media_body=media, fields="id"
            ).execute())

    def _load_state(self, job_id: str) -> dict | None:
        fname = self._state_filename(job_id)
        fid = self._find_drive_file(fname)
        if not fid:
            return None
        raw = _retry(lambda: self._drive.files().get_media(fileId=fid).execute())
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if "review_ids_seen" in data and isinstance(data["review_ids_seen"], list):
            data["review_ids_seen"] = set(data["review_ids_seen"])
        logger.info("Loaded state for job %s from Drive", job_id)
        return data

    def _find_drive_file(self, name: str) -> str | None:
        q = f"name='{name}' and trashed=false"
        if self.folder_id:
            q += f" and '{self.folder_id}' in parents"
        resp = _retry(lambda: self._drive.files().list(
            q=q, fields="files(id)", pageSize=1
        ).execute())
        files = resp.get("files", [])
        return files[0]["id"] if files else None

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Master Tasks Sheet
    # ──────────────────────────────────────────────────────────────────────

    def _get_or_create_master_tasks(self) -> str:
        """Finds or creates a master 'Tasks' spreadsheet. Returns its spreadsheetId."""
        fid = self._find_drive_file("Tasks")
        if fid:
            # Check if it's actually a spreadsheet (the name could be anything, but we assume it's a sheet)
            return fid
        
        # Doesn't exist, create it
        logger.info("Master 'Tasks' sheet not found, creating it...")
        sid, url = self._create_spreadsheet("Tasks")
        
        # Overwrite the headers in this new Tasks sheet (since _create_spreadsheet creates Places/Reviews/Jobs)
        # We'll just rename "Places" to "Tasks" or use "Sheet1". Let's just create a new sheet with correct headers.
        try:
            # If created via Drive API it might just have "Sheet1" or if fallback it has Places, Reviews, Jobs.
            # Let's just write to the first sheet (which is index 0). We'll assume A1:D1
            headers = [["Job ID", "Task Name", "Created At", "Spreadsheet Link"]]
            body = {"values": headers}
            _retry(lambda: self._sheets.spreadsheets().values().update(
                spreadsheetId=sid,
                range="A1:D1",
                valueInputOption="RAW",
                body=body,
            ).execute())
        except Exception as exc:
            logger.warning("Could not set headers for master Tasks sheet: %s", exc)
            
        return sid

    def _append_to_master_tasks(self, job_id: str, query: str, created_at: str, url: str):
        try:
            sid = self._get_or_create_master_tasks()
            values = [[job_id, query, created_at, url]]
            body = {"values": values}
            _retry(lambda: self._sheets.spreadsheets().values().append(
                spreadsheetId=sid,
                range="A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute())
            logger.info("Appended job %s to master Tasks sheet", job_id)
        except Exception as exc:
            logger.error("Failed to append to master Tasks sheet: %s", exc)

    def get_tasks_history(self) -> list[dict]:
        """Returns all past tasks from the master Tasks sheet."""
        try:
            fid = self._find_drive_file("Tasks")
            if not fid:
                return []
            
            resp = _retry(lambda: self._sheets.spreadsheets().values().get(
                spreadsheetId=fid,
                range="A:D",
            ).execute())
            rows = resp.get("values", [])
            if len(rows) <= 1:
                return []
            
            # Skip header row
            result = []
            for r in rows[1:]:
                # Pad to 4 cols
                r = r + [""] * (4 - len(r))
                result.append({
                    "job_id": r[0],
                    "query": r[1],
                    "created_at": r[2],
                    "spreadsheet_url": r[3]
                })
            # Reverse sort by created_at (assuming newer is at bottom)
            result.reverse()
            return result
        except Exception as exc:
            logger.error("Failed to fetch tasks history: %s", exc)
            return []

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Sheets I/O
    # ──────────────────────────────────────────────────────────────────────

    def _append_row(self, sid: str, sheet: str, values: list):
        safe_values = [v[:49000] + "... [truncated]" if isinstance(v, str) and len(v) > 49000 else v for v in values]
        body = {"values": [safe_values]}
        _retry(lambda: self._sheets.spreadsheets().values().append(
            spreadsheetId=sid,
            range=f"{sheet}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute())

    def _update_row(self, sid: str, sheet: str, row: int, values: list):
        safe_values = [v[:49000] + "... [truncated]" if isinstance(v, str) and len(v) > 49000 else v for v in values]
        col_end = _col_letter(len(safe_values))
        body = {"values": [safe_values]}
        _retry(lambda: self._sheets.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"{sheet}!A{row}:{col_end}{row}",
            valueInputOption="RAW",
            body=body,
        ).execute())

    def _get_all_rows(self, sid: str, sheet: str, headers: list) -> list[dict]:
        resp = _retry(lambda: self._sheets.spreadsheets().values().get(
            spreadsheetId=sid,
            range=f"{sheet}!A1:{_col_letter(len(headers))}",
        ).execute())
        rows = resp.get("values", [])
        if len(rows) <= 1:
            return []
        header_row = rows[0]
        result = []
        for row in rows[1:]:
            padded = row + [""] * (len(header_row) - len(row))
            result.append(dict(zip(header_row, padded)))
        return result

    # ──────────────────────────────────────────────────────────────────────
    # Public API — Places
    # ──────────────────────────────────────────────────────────────────────

    def upsert_place(self, place, job_id: str | None = None):
        if not job_id:
            return
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            sid = cache.get("spreadsheet_id")
            if not sid:
                return

            hours_str = ""
            if place.opening_hours:
                hours_str = json.dumps({
                    "periods": place.opening_hours.periods,
                    "weekday_text": place.opening_hours.weekday_text,
                    "open_now": place.opening_hours.open_now,
                    "next_opening": place.opening_hours.next_opening,
                })

            row_values = [
                place.place_id, place.name, place.address,
                _to_str(place.address_components), place.plus_code,
                place.lat, place.lng, place.rating, place.review_count,
                place.website, place.phone, place.email or "", place.fax or "",
                place.price_level or "", place.description or "",
                _to_str(place.categories), place.primary_type or "",
                hours_str, _to_str(place.photos), _to_str(place.about),
                _to_str(place.menu), _to_str(place.booking_links),
                _to_str(place.social_links),
                place.hotel_class or "", place.business_status or "",
                0, "", 0, _now_iso(),
            ]

            existing_row = cache["place_row"].get(place.place_id)
            if existing_row:
                self._update_row(sid, "Places", existing_row, row_values)
            else:
                self._append_row(sid, "Places", row_values)
                row_num = cache["next_place_row"]
                cache["place_row"][place.place_id] = row_num
                cache["next_place_row"] += 1

    def mark_reviews_fetched(self, place_id: str, cursor: str = "", total_saved: int = 0, job_id: str | None = None):
        if not job_id:
            return
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            sid = cache.get("spreadsheet_id")
            if not sid:
                return
            row_num = cache["place_row"].get(place_id)
            if not row_num:
                return
            col_fetched = _col_letter(PLACE_HEADERS.index("reviews_fetched") + 1)
            col_cursor  = _col_letter(PLACE_HEADERS.index("reviews_cursor") + 1)
            col_total   = _col_letter(PLACE_HEADERS.index("reviews_total_saved") + 1)
            updates = [{
                "range": f"Places!{col_fetched}{row_num}:{col_total}{row_num}",
                "values": [[1, cursor, total_saved]],
            }]
            body = {"valueInputOption": "RAW", "data": updates}
            _retry(lambda: self._sheets.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body=body
            ).execute())

    def get_place_cursor(self, place_id: str, job_id: str | None = None) -> dict:
        if not job_id:
            return {"cursor": "", "total_saved": 0}
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            sid = cache.get("spreadsheet_id")
            if not sid:
                return {"cursor": "", "total_saved": 0}
            row_num = cache["place_row"].get(place_id)
            if not row_num:
                return {"cursor": "", "total_saved": 0}
            col_cursor = _col_letter(PLACE_HEADERS.index("reviews_cursor") + 1)
            col_total  = _col_letter(PLACE_HEADERS.index("reviews_total_saved") + 1)
            resp = _retry(lambda: self._sheets.spreadsheets().values().get(
                spreadsheetId=sid,
                range=f"Places!{col_cursor}{row_num}:{col_total}{row_num}",
            ).execute())
            vals = (resp.get("values") or [[]])[0]
            return {
                "cursor": vals[0] if len(vals) > 0 else "",
                "total_saved": int(vals[1]) if len(vals) > 1 and vals[1] else 0,
            }

    def get_place(self, place_id: str, job_id: str | None = None) -> dict | None:
        if not job_id:
            return None
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            sid = cache.get("spreadsheet_id")
            if not sid:
                return None
            row_num = cache["place_row"].get(place_id)
            if not row_num:
                return None
            col_end = _col_letter(len(PLACE_HEADERS))
            resp = _retry(lambda: self._sheets.spreadsheets().values().get(
                spreadsheetId=sid,
                range=f"Places!A{row_num}:{col_end}{row_num}",
            ).execute())
            vals = (resp.get("values") or [[]])[0]
            padded = vals + [""] * (len(PLACE_HEADERS) - len(vals))
            return dict(zip(PLACE_HEADERS, padded))

    def get_pending_places(self, job_id: str | None = None) -> list[dict]:
        if not job_id:
            return []
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            sid = cache.get("spreadsheet_id")
            if not sid:
                return []
            rows = self._get_all_rows(sid, "Places", PLACE_HEADERS)
            return [
                {
                    "place_id": r["place_id"],
                    "name": r["name"],
                    "cursor": r.get("reviews_cursor", ""),
                    "total_saved": int(r["reviews_total_saved"] or 0),
                }
                for r in rows
                if r.get("reviews_fetched", "0") in ("", "0")
            ]

    def get_stats(self, job_id: str | None = None) -> dict:
        if not job_id:
            return {"places": 0, "reviews": 0, "pending_reviews": 0}
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            sid = cache.get("spreadsheet_id")
            if not sid:
                return {"places": 0, "reviews": 0, "pending_reviews": 0}
            rows = self._get_all_rows(sid, "Places", PLACE_HEADERS)
            places = len(rows)
            pending = sum(1 for r in rows if r.get("reviews_fetched", "0") in ("", "0"))
            rev_rows = self._get_all_rows(sid, "Reviews", REVIEW_HEADERS)
            return {"places": places, "reviews": len(rev_rows), "pending_reviews": pending}

    # ──────────────────────────────────────────────────────────────────────
    # Public API — Reviews
    # ──────────────────────────────────────────────────────────────────────

    def insert_review(self, place_id: str, review, job_id: str | None = None):
        if not review.review_id or not job_id:
            return
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            sid = cache.get("spreadsheet_id")
            if not sid:
                return
            if review.review_id in cache["review_ids_seen"]:
                return
            cache["review_ids_seen"].add(review.review_id)
            row_values = [
                review.review_id, place_id,
                review.reviewer.name, review.reviewer.profile_url,
                review.reviewer.avatar_url, review.reviewer.user_id,
                review.reviewer.review_count,
                1 if review.reviewer.is_local_guide else 0,
                review.rating, review.text or "", review.date or "",
                review.language or "",
                _to_str(review.photos),
                review.owner_reply or "", review.owner_reply_date or "",
                _now_iso(),
            ]
            self._append_row(sid, "Reviews", row_values)
            cache["next_review_row"] += 1

    # ──────────────────────────────────────────────────────────────────────
    # Public API — Jobs
    # ──────────────────────────────────────────────────────────────────────

    def create_job(self, job_id: str, query: str):
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            if cache.get("spreadsheet_id"):
                return  # idempotent

            title = f"GMaps - {query[:60]} - {job_id}"
            sid, url = self._create_spreadsheet(title)
            cache["spreadsheet_id"] = sid
            cache["spreadsheet_url"] = url
            cache["query"] = query
            cache["created_at"] = _now_iso()

            row_values = [
                job_id, query, "running", 0, 0, 0,
                cache["created_at"], cache["created_at"], url, "",
            ]
            self._append_row(sid, "Jobs", row_values)
            self._save_state(job_id)
            
            # Append to master Tasks sheet
            self._append_to_master_tasks(job_id, query, cache["created_at"], url)
            
            print(f"  Spreadsheet: {url}")
            if self.share_email:
                print(f"  Shared with: {self.share_email} (check 'Shared with me' in Drive)")

    def add_job_places(self, job_id: str, place_ids: list[str]):
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            for pid in place_ids:
                if pid not in cache["job_places_status"]:
                    cache["job_places_status"][pid] = "pending"
                    cache["job_places_cursor"][pid] = ""
                    cache["job_places_reviews"][pid] = 0
            cache["places_total"] = len(cache["job_places_status"])
            self._update_job_row(job_id)
            self._save_state(job_id)

    def get_pending_job_places(self, job_id: str) -> list[dict]:
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            return [
                {
                    "place_id": pid,
                    "cursor": cache["job_places_cursor"].get(pid, ""),
                    "name": "",
                    "total_saved": cache["job_places_reviews"].get(pid, 0),
                }
                for pid, status in cache["job_places_status"].items()
                if status == "pending"
            ]

    def get_job_place_cursor(self, job_id: str, place_id: str) -> str:
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            return cache["job_places_cursor"].get(place_id, "")

    def mark_job_place_done(self, job_id: str, place_id: str, reviews_count: int = 0, cursor: str = ""):
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            cache["job_places_status"][place_id] = "done"
            cache["job_places_cursor"][place_id] = cursor
            cache["job_places_reviews"][place_id] = reviews_count
            cache["places_done"] = sum(
                1 for s in cache["job_places_status"].values() if s == "done"
            )
            cache["reviews_done"] = sum(cache["job_places_reviews"].values())
            self._update_job_row(job_id)
            self._save_state(job_id)

    def reopen_job_places_for_reviews(self, job_id: str, max_reviews: int | None) -> int:
        if max_reviews is None or max_reviews <= 0:
            return 0
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            reopened = 0
            for pid, status in list(cache["job_places_status"].items()):
                if status == "done" and cache["job_places_reviews"].get(pid, 0) < max_reviews:
                    cache["job_places_status"][pid] = "pending"
                    reopened += 1
            if reopened:
                self._save_state(job_id)
            return reopened

    def update_job_status(self, job_id: str, status: str, error: str | None = None):
        with self._lock:
            cache = self._get_or_create_job_cache(job_id)
            cache["status"] = status
            if error:
                cache["error"] = error
            self._update_job_row(job_id)
            self._save_state(job_id)

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            if job_id in self._job_cache:
                c = self._job_cache[job_id]
                return {
                    "job_id": job_id,
                    "query": c.get("query", ""),
                    "status": c.get("status", ""),
                    "places_total": c.get("places_total", 0),
                    "places_done": c.get("places_done", 0),
                    "reviews_done": c.get("reviews_done", 0),
                    "created_at": c.get("created_at", ""),
                    "updated_at": c.get("updated_at", ""),
                    "spreadsheet_url": c.get("spreadsheet_url", ""),
                    "error": c.get("error", ""),
                }
            state = self._load_state(job_id)
            if state:
                self._job_cache[job_id] = state
                return self.get_job(job_id)
            return None

    def list_jobs(self, limit: int = 20) -> list[dict]:
        q = "name contains '_state.json' and trashed=false"
        resp = _retry(lambda: self._drive.files().list(
            q=q, fields="files(id, name, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=limit,
        ).execute())
        jobs = []
        for f in resp.get("files", []):
            job_id = f["name"].replace("_state.json", "")
            job = self.get_job(job_id)
            if job:
                jobs.append(job)
        return jobs

    def _update_job_row(self, job_id: str):
        cache = self._job_cache.get(job_id)
        if not cache:
            return
        sid = cache.get("spreadsheet_id")
        if not sid:
            return
        cache["updated_at"] = _now_iso()
        row_values = [
            job_id, cache.get("query", ""), cache.get("status", "running"),
            cache.get("places_total", 0), cache.get("places_done", 0),
            cache.get("reviews_done", 0),
            cache.get("created_at", ""), cache.get("updated_at", ""),
            cache.get("spreadsheet_url", ""), cache.get("error", ""),
        ]
        self._update_row(sid, "Jobs", 2, row_values)

    # ──────────────────────────────────────────────────────────────────────
    # Context manager
    # ──────────────────────────────────────────────────────────────────────

    def close(self):
        for job_id in list(self._job_cache.keys()):
            try:
                self._save_state(job_id)
            except Exception as exc:
                logger.warning("Failed to save state for job %s: %s", job_id, exc)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat alias
# ─────────────────────────────────────────────────────────────────────────────

class Database(SheetsDatabase):
    """Alias of SheetsDatabase for backward-compatibility with main.py."""
    def __init__(self, _path_ignored=None, **kwargs):
        super().__init__(**kwargs)
