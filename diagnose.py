"""Diagnostic script — prints full error details to pinpoint the 403 cause."""
import json, os
from dotenv import load_dotenv
load_dotenv()

from google.oauth2 import service_account
import httplib2

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
creds = service_account.Credentials.from_service_account_file(creds_file, scopes=SCOPES)
authed_http = google_auth_httplib2 = __import__("google_auth_httplib2")
http = authed_http.AuthorizedHttp(creds, http=httplib2.Http())

# Test Drive API
print("=== DRIVE API TEST ===")
resp, content = http.request("https://www.googleapis.com/drive/v3/about?fields=user")
print(f"Status: {resp.status}")
print(json.loads(content).get("user", {}).get("emailAddress", content[:300]))

# Test Sheets API — raw HTTP so we see full error body
print("\n=== SHEETS API TEST ===")
import json as _json
body = _json.dumps({"properties": {"title": "_diag_test"}}).encode()
resp2, content2 = http.request(
    "https://sheets.googleapis.com/v4/spreadsheets",
    method="POST",
    body=body,
    headers={"Content-Type": "application/json"},
)
print(f"Status: {resp2.status}")
try:
    data = _json.loads(content2)
    print(_json.dumps(data, indent=2))
except Exception:
    print(content2[:500])
