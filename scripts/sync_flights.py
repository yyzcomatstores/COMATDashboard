"""
Porter Multi-Station Flight Sync
Fetches live flight data from radar.flyporter.com and writes it to a Google Sheet.
Filters to flights touching one of our home stations, and — for any flight that
touches more than one of them (e.g. YYZ<->YOW) — writes one row PER station,
each carrying the gate/status/time relevant to that station's side of the leg.
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
PORTER_URL     = "https://radar.flyporter.com/flightinformation/get?nocache=true"
SHEET_ID       = os.environ["GOOGLE_SHEET_ID"]       # set in GitHub Actions secrets
SHEET_TAB      = "Flights"                            # tab name inside the spreadsheet

# All stations that get their own dashboard login. Add/remove airport codes here
# to change which stations are tracked — no other code changes needed.
HOME_AIRPORTS = ["YYZ", "YTZ", "YHZ", "YOW"]

# How many days around "today" to keep (the feed spans multiple days of history/future).
# 0 = today only. 1 = today +/- 1 day. Adjust to taste.
DATE_WINDOW_DAYS = 1

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Flight Key",           # unique per (flight, station, date) — used by AppSheet to link COMAT rows to their flight
    "Flight",
    "Origin",
    "Destination",
    "Station",             # which home station this row is written for
    "Direction",            # ARRIVAL or DEPARTURE, relative to Station
    "Scheduled Time",       # local time, Station-relevant leg
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
    """Fetch XML from Porter's radar endpoint and parse into a list of row dicts.

    A flight that touches two of our home stations (e.g. YYZ -> YOW) produces
    TWO rows — one for each station — so each station's dashboard shows the
    gate/status/time that's relevant to it.
    """
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

        touched_stations = [s for s in HOME_AIRPORTS if s in (origin, dest)]
        if not touched_stations:
            continue  # doesn't touch any of our stations — skip

        flight_num = get("flightNumber").replace(" ", "")
        tail       = get("tailNumber")

        # Codeshare number lives in a nested <codeshare><codeshareFlightNumber> element
        codeshare_el = item.find("codeshare/codeshareFlightNumber")
        codeshare = codeshare_el.text.strip() if codeshare_el is not None and codeshare_el.text else "-"

        for station in touched_stations:
            if dest == station:
                # This leg is an arrival INTO this station — use destination-side fields
                direction  = "ARRIVAL"
                gate       = get("destinationGate")
                status     = get("arrivalStatus")
                scheduled  = get("scheduledArrivalTime")
                estimated  = get("estimatedArrivalTime")
            else:
                # This leg is a departure FROM this station — use departure-side fields
                direction  = "DEPARTURE"
                gate       = get("departureGate")
                status     = get("departureStatus")
                scheduled  = get("scheduledDepartureTime")
                estimated  = get("estimatedDepartureTime")

            # NOTE ON THE JOIN KEY: this mirrors the id scheme the website builds
            # client-side (FlightNumber_Station_Date), so AppSheet can match COMAT
            # rows to flights. The date here comes from Porter's own flightDate
            # field for reliability. In rare cases (a flight scheduled right at a
            # midnight boundary), the website's browser-local date could differ by
            # a day from this — if that ever happens, that one COMAT row just won't
            # show a linked flight in AppSheet; it doesn't affect the website itself.
            flight_key = f"{flight_num}_{station}_{flight_date}"

            rows.append([
                flight_key,
                flight_num,
                origin,
                dest,
                station,
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
    rows.sort(key=lambda r: r[6])

    return rows


# ── WRITE ────────────────────────────────────────────────────────────────────
def write_to_sheet(sheet, rows):
    """Clear the sheet and rewrite all rows atomically."""
    all_data = [HEADERS] + rows
    sheet.clear()
    sheet.update(values=all_data, range_name="A1")  # keyword args avoid the deprecation warning

    sheet.format("A1:M1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
    })
    per_station = {}
    for r in rows:
        per_station[r[4]] = per_station.get(r[4], 0) + 1
    summary = ", ".join(f"{k}:{v}" for k, v in sorted(per_station.items()))
    print(f"Wrote {len(rows)} rows ({summary}) to sheet '{SHEET_TAB}' at {datetime.now(timezone.utc).strftime('%H:%M UTC')}")


# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Fetching Porter flight data for stations: {', '.join(HOME_AIRPORTS)}...")
    try:
        rows = fetch_flights()
        if not rows:
            print("No flights found in the current date window — check DATE_WINDOW_DAYS or HOME_AIRPORTS")
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
