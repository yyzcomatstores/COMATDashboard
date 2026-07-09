"""
Porter COMAT -> Google Sheets Sync
Reads COMAT records straight out of Firebase Realtime Database and writes
them into a "COMAT" tab in the same Google Sheet sync_flights.py already
writes to — so AppSheet (or anything else that reads Sheets) can display
them, including a Flight Key column that links each COMAT row back to its
matching row in the Flights tab.

This is a READ-ONLY mirror: it only ever reads from Firebase and writes to
the Sheet. It never writes anything back to Firebase. Adding, editing, or
marking COMAT received all still only happens through the website.

Run manually or via GitHub Actions on a schedule (see sync_comat.yml).
"""

import os
import json
import requests
import gspread
from datetime import datetime, timezone
from google.oauth2.service_account import Credentials
import google.auth.transport.requests

# ── CONFIG ──────────────────────────────────────────────────────────────────
FIREBASE_DB_URL = "https://yyz-stores-1a98f-default-rtdb.firebaseio.com"
SHEET_ID        = os.environ["GOOGLE_SHEET_ID"]   # same sheet sync_flights.py uses
SHEET_TAB       = "COMAT"                          # separate tab, doesn't touch "Flights"

# Credentials for reading Firebase (separate from the Sheets credentials below —
# Firebase Realtime Database isn't reachable with a plain Sheets-scoped account).
FIREBASE_SCOPES = [
    "https://www.googleapis.com/auth/firebase.database",
    "https://www.googleapis.com/auth/userinfo.email",
]

# Credentials for writing to Google Sheets — same pattern as sync_flights.py.
SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Tracking Number",     # PDT1, PDT2, ... — the record's primary key for AppSheet
    "Flight Key",          # matches the "Flight Key" column in the Flights tab — this is the Ref link
    "Flight Number",
    "Order Number",
    "Person Responsible",
    "Priority",             # AOG / Normal / Expedite / N/A
    "Pieces",
    "Direction",            # OUTBOUND / INBOUND
    "Status",                # active / completed
    "Added By",
    "Added At (UTC)",
    "Received By",
    "Received At (UTC)",
    "Notes",
]


# ── AUTH: FIREBASE ────────────────────────────────────────────────────────────
def get_firebase_token():
    """Mint a short-lived Google OAuth2 access token for reading Firebase."""
    raw = os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=FIREBASE_SCOPES)
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


# ── FETCH ────────────────────────────────────────────────────────────────────
def fetch_comat(token):
    """Read the entire comat_data node from Firebase via its REST API."""
    url = f"{FIREBASE_DB_URL}/comat_data.json"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    resp.raise_for_status()
    return resp.json() or {}


def to_rows(comat_dict):
    """Convert Firebase's {recordId: {...}} shape into Sheet rows."""
    rows = []
    for record_id, c in comat_dict.items():
        if not isinstance(c, dict):
            continue

        flight_key = c.get("flightId", "") or ""
        # The flight number is always the part before the first underscore
        # in the flight key (e.g. "PD157_YYZ_2026-07-09" -> "PD157").
        flight_number = flight_key.split("_")[0] if flight_key else ""

        rows.append([
            c.get("trackingNumber", "") or "",
            flight_key,
            flight_number,
            c.get("orderNumber", "") or "",
            c.get("partNumber", "") or "",
            c.get("description", "") or "",
            c.get("quantity", "") or "",
            c.get("direction", "") or "",
            c.get("status", "") or "",
            c.get("addedBy", "") or "",
            c.get("addedAt", "") or "",
            c.get("receivedBy", "") or "",
            c.get("completedAt", "") or "",
            c.get("notes", "") or "",
        ])

    # Newest first
    rows.sort(key=lambda r: r[10], reverse=True)
    return rows


# ── AUTH + WRITE: GOOGLE SHEETS ────────────────────────────────────────────────
def get_sheet():
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)

    try:
        sheet = spreadsheet.worksheet(SHEET_TAB)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=SHEET_TAB, rows=2000, cols=len(HEADERS))

    return sheet


def write_to_sheet(sheet, rows):
    all_data = [HEADERS] + rows
    sheet.clear()
    sheet.update(values=all_data, range_name="A1")

    sheet.format("A1:N1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
    })
    active = sum(1 for r in rows if r[8] != "completed")
    print(f"Wrote {len(rows)} COMAT records ({active} active) to sheet '{SHEET_TAB}' at {datetime.now(timezone.utc).strftime('%H:%M UTC')}")


# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Fetching COMAT data from Firebase...")
    try:
        token = get_firebase_token()
        comat_dict = fetch_comat(token)
        rows = to_rows(comat_dict)
        if not rows:
            print("No COMAT records found — writing an empty sheet with headers only.")
        sheet = get_sheet()
        write_to_sheet(sheet, rows)
    except requests.RequestException as e:
        print(f"Network/Firebase error: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise
