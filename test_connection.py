from dotenv import load_dotenv
load_dotenv()

from utils.google_auth import build_services
import os

print("Testing Google API connection...")
sheets, drive = build_services()

# Test: create a tiny test spreadsheet to verify full write access
resp = sheets.spreadsheets().create(body={"properties": {"title": "_test_connection"}}).execute()
sid = resp["spreadsheetId"]
url = f"https://docs.google.com/spreadsheets/d/{sid}"
print(f"Test spreadsheet created: {url}")

# Share with user email if set
share_email = os.environ.get("SHARE_WITH_EMAIL", "").strip()
if share_email and share_email != "your_personal_gmail@gmail.com":
    drive.permissions().create(
        fileId=sid,
        body={"type": "user", "role": "writer", "emailAddress": share_email},
        sendNotificationEmail=False,
    ).execute()
    print(f"Shared with: {share_email}")

# Clean up test spreadsheet
drive.files().delete(fileId=sid).execute()
print("Test spreadsheet deleted (cleanup).")
print()
print("Connection OK! You are ready to run the scraper.")
print()
print("Next step: set SHARE_WITH_EMAIL in .env to your Gmail,")
print("then run:  python main.py search \"Chartered Accountants in Mumbai\" --max-places 200 --max-reviews 2")
