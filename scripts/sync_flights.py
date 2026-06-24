"""
Porter YYZ Flight Sync
Fetches live flight data from radar.flyporter.com and writes it to a Google Sheet.
Filters to flights touching YYZ only, picks the gate/status/time relevant to YYZ
depending on whether the flight is arriving or departing there.
Run manually or via GitHub Actions on a schedule.
"""

import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials

# ── CONFIG ──────────────────────────────────────────────────────────────────
PORTER_URL    = "https://radar.flyporter.com/flightinformation/get?nocache=true"
SHEET_ID      = os.environ["GOOGLE_SHEET_ID"]       # set in GitHub Actions secrets
SHEET_TAB     = "Flights"                            # tab name inside the spreadsheet
HOME_AIRPORT  = "YYZ"

# How many days around "today" to keep (the feed spans multiple days of history/future).
# 0 = today only. 1 = today +/- 1 day. Adjust to taste.
DATE_WINDOW_DAYS = 1

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Flight",
    "Origin",
    "Destination",
    "Direction",          # ARRIVAL or DEPARTURE (relative to YYZ)
    "Scheduled Time",      # local time, YYZ-relevant leg
    "Estimated Time",
    "Status",
    "Gate",
    "Tail Number",
    "Codeshare",
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

    try:
        sheet = spreadsheet.worksheet(SHEET_TAB)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=SHEET_TAB, rows=500, cols=len(HEADERS))

    return sheet


# ── FETCH ────────────────────────────────────────────────────────────────────
def fetch_flights():
    """Fetch XML from Porter's radar endpoint and parse into a list of row dicts."""
    resp = requests.get(PORTER_URL, timeout=20)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    now_utc = datetime.now(timezone.utc)
    now_utc_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    today = datetime.now().date()
    valid_dates = {
        (today + timedelta(days=d)).isoformat()
        for d in range(-DATE_WINDOW_DAYS, DATE_WINDOW_DAYS + 1)
    }

    rows = []

    for item in root.iter("item"):
        def get(tag, default="-"):
            el = item.find(tag)
            return el.text.strip() if el is not None and el.text else default

        flight_date = get("flightDate")
        if flight_date not in valid_dates:
            continue  # outside our date window — skip

        origin = get("departureAirportCode")
        dest   = get("destinationAirportCode")

        if origin != HOME_AIRPORT and dest != HOME_AIRPORT:
            continue  # doesn't touch YYZ at all — skip

        flight_num = get("flightNumber").replace(" ", "")
        tail       = get("tailNumber")

        # Codeshare number lives in a nested <codeshare><codeshareFlightNumber> element
        codeshare_el = item.find("codeshare/codeshareFlightNumber")
        codeshare = codeshare_el.text.strip() if codeshare_el is not None and codeshare_el.text else "-"

        if dest == HOME_AIRPORT:
            # This leg is an arrival INTO YYZ — use destination-side fields
            direction  = "ARRIVAL"
            gate       = get("destinationGate")
            status     = get("arrivalStatus")
            scheduled  = get("scheduledArrivalTime")
            estimated  = get("estimatedArrivalTime")
        else:
            # This leg is a departure FROM YYZ — use departure-side fields
            direction  = "DEPARTURE"
            gate       = get("departureGate")
            status     = get("departureStatus")
            scheduled  = get("scheduledDepartureTime")
            estimated  = get("estimatedDepartureTime")

        rows.append([
            flight_num,
            origin,
            dest,
            direction,
            scheduled,
            estimated,
            status,
            gate,
            tail,
            codeshare,
            now_utc_str,
        ])

    # Sort by scheduled time so the sheet reads top-to-bottom chronologically
    rows.sort(key=lambda r: r[4])

    return rows


# ── WRITE ────────────────────────────────────────────────────────────────────
def write_to_sheet(sheet, rows):
    """Clear the sheet and rewrite all rows atomically."""
    all_data = [HEADERS] + rows
    sheet.clear()
    sheet.update(values=all_data, range_name="A1")  # keyword args avoid the deprecation warning

    sheet.format("A1:K1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
    })
    print(f"Wrote {len(rows)} YYZ flights to sheet '{SHEET_TAB}' at {datetime.now(timezone.utc).strftime('%H:%M UTC')}")


# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Fetching Porter flight data...")
    try:
        rows = fetch_flights()
        if not rows:
            print("No YYZ flights found in the current date window — check DATE_WINDOW_DAYS or HOME_AIRPORT")
        else:
            sheet = get_sheet()
            write_to_sheet(sheet, rows)
    except requests.RequestException as e:
        print(f"Network error: {e}")
        raise
    except ET.ParseError as e:
        print(f"XML parse error: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise
