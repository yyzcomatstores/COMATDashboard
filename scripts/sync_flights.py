"""
Porter YYZ Flight Sync
Fetches live flight data from radar.flyporter.com and writes it to a Google Sheet.
Run manually or via GitHub Actions on a schedule.
"""

import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials

# ── CONFIG ──────────────────────────────────────────────────────────────────
PORTER_URL   = "https://radar.flyporter.com/flightinformation/get?nocache=true"
SHEET_ID     = os.environ["GOOGLE_SHEET_ID"]       # set in GitHub Actions secrets
SHEET_TAB    = "Flights"                            # tab name inside the spreadsheet

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Flight",
    "Origin",
    "Destination",
    "Aircraft",
    "Scheduled (STD)",
    "Status",
    "Gate",
    "Tail Number",
    "Direction",
    "Last Updated (UTC)",
]

# ── AUTH ─────────────────────────────────────────────────────────────────────
def get_sheet():
    """Authenticate with Google Sheets using the service account JSON from env."""
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)

    # Create the tab if it doesn't exist yet
    try:
        sheet = spreadsheet.worksheet(SHEET_TAB)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=SHEET_TAB, rows=200, cols=len(HEADERS))

    return sheet


# ── FETCH ────────────────────────────────────────────────────────────────────
def fetch_flights():
    """Fetch XML from Porter's radar endpoint and parse into a list of row dicts."""
    resp = requests.get(PORTER_URL, timeout=15)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)

    # DEBUG: print one complete <item> element so we can see every field name
    # with nothing truncated. Remove this block once the mapping below is confirmed.
    first_item = root.find(".//item")
    if first_item is not None:
        print("──── SAMPLE ITEM (full) ────")
        print(ET.tostring(first_item, encoding="unicode"))
        print("─────────────────────────────")

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []

    # Porter's feed is RSS-style: each flight is an <item> directly under <channel>.
    for flight in root.iter("item"):
        def get(tag, default="-"):
            el = flight.find(tag)
            return el.text.strip() if el is not None and el.text else default

        flight_num = get("flightNumber")
        origin     = get("departureAirportCode")
        dest       = get("destinationAirportCode")
        aircraft   = get("aircraftType")       # ← unconfirmed, may not be the real tag name
        std        = get("estimatedDepartureTime") if get("estimatedDepartureTime") != "-" else get("scheduledDepartureTime")
        status     = get("flightStatus")       # ← unconfirmed, may not be the real tag name
        gate       = get("departureGate")      # ← unconfirmed, may not be the real tag name
        tail       = get("tailNumber")

        direction  = "OUTBOUND" if origin == "YYZ" else "INBOUND"

        rows.append([
            flight_num,
            origin,
            dest,
            aircraft,
            std,
            status,
            gate,
            tail,
            direction,
            now_utc,
        ])

    return rows


# ── WRITE ────────────────────────────────────────────────────────────────────
def write_to_sheet(sheet, rows):
    """Clear the sheet and rewrite all rows atomically."""
    all_data = [HEADERS] + rows
    sheet.clear()
    sheet.update("A1", all_data, value_input_option="USER_ENTERED")

    # Bold the header row
    sheet.format("A1:J1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
    })
    print(f"✓ Wrote {len(rows)} flights to sheet '{SHEET_TAB}' at {datetime.now(timezone.utc).strftime('%H:%M UTC')}")


# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Fetching Porter flight data...")
    try:
        rows = fetch_flights()
        if not rows:
            print("⚠ No flights found in response — check XML tag names (see NOTE in fetch_flights)")
        else:
            sheet = get_sheet()
            write_to_sheet(sheet, rows)
    except requests.RequestException as e:
        print(f"✗ Network error: {e}")
        raise
    except ET.ParseError as e:
        print(f"✗ XML parse error: {e}")
        raise
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        raise
