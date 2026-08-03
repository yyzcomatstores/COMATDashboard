// ════════════════════════════════════════════════════════
// LIVE FLIGHT LOADER — reads from published Google Sheet CSV
// Replace SHEET_ID below with your actual Google Sheet ID
// Column order here must match HEADERS in scripts/sync_flights.py
//
// The sheet now holds rows for ALL home stations (YYZ, YTZ, YHZ, YOW).
// A flight touching two of our stations (e.g. YYZ<->YOW) appears as TWO
// rows — one per station — each with the gate/status/time relevant to
// that station. Every flight object carries a `station` field, and the
// dashboard (index.html) filters to whichever station the logged-in
// user belongs to.
// ════════════════════════════════════════════════════════

const SHEET_ID      = "1rJDB_S7xw4Mg-3Z1lYkiV9-oJzUqRmC_kAq_AldWP4c";   // paste your Sheet ID
const SHEET_TAB      = "Flights";
const REFRESH_MS      = 60_000;                        // poll every 60 seconds
const SHEET_CSV_URL  = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(SHEET_TAB)}`;

// Column index map — matches HEADERS in sync_flights.py
const COL = {
  flight:    0,   // "Flight"
  origin:    1,   // "Origin"
  dest:      2,   // "Destination"
  station:   3,   // "Station" (which home station this row belongs to)
  direction: 4,   // "Direction" (ARRIVAL / DEPARTURE relative to Station)
  scheduled: 5,   // "Scheduled Time"
  estimated: 6,   // "Estimated Time"
  status:    7,   // "Status"
  gate:      8,   // "Gate"
  tail:      9,   // "Tail Number"
  codeshare: 10,  // "Codeshare"
  updated:   11,  // "Last Updated (UTC)"
};

// Simple CSV parser (handles quoted fields with commas inside)
function parseCSV(text) {
  const lines = text.trim().split("\n");
  return lines.map(line => {
    const cols = [];
    let cur = "", inQuote = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        if (inQuote && line[i + 1] === '"') { cur += '"'; i++; }
        else inQuote = !inQuote;
      } else if (ch === "," && !inQuote) {
        cols.push(cur.trim()); cur = "";
      } else {
        cur += ch;
      }
    }
    cols.push(cur.trim());
    return cols;
  });
}

// Porter's times come as "YYYY-MM-DD HH:MM" — convert to something Date() can parse
function toDate(value) {
  if (!value || value === "-") return null;
  return new Date(value.replace(" ", "T"));
}

// ════════════════════════════════════════════════════════
// YYZ GATE → AVOP STAND TRANSLATION
// The gate shown in Porter's feed (and on flyporter.com) is the passenger-
// facing gate number — it does NOT match the AVOP stand number used airside.
// This only ever applies to YYZ; every other station's gate passes through
// unchanged. Sourced from the YYZ Terminal 3 AVOP map — update this table
// directly if the mapping ever changes, no need to touch anything else.
//
// Anything NOT in this table falls back to showing the original passenger
// gate rather than guessing — but is flagged (gateNeedsVerification) so the
// UI can visibly warn that a given gate hasn't been confirmed against the
// AVOP map yet. Never trust an unflagged/unmapped gate as an AVOP stand.
// ════════════════════════════════════════════════════════
const YYZ_GATE_TO_STAND = {
  "B2A": "A2",  "B2C": "A3",  "B3": "A4",   "B4": "A5",   "B5": "A6",
  "A7":  "B7",  "A8":  "B8",  "A9":  "B9",  "A10": "B10", "A11": "B11",
  "A12": "B12", "A13": "B13", "A14": "B14", "A15": "B15", "A16": "B16",
  "A17": "B17", "A18": "B18", "A19": "B19", "A20": "B20",
  "B22": "B22", "B23": "B23",
  "B24": "C24", "B25": "C25", "B26": "C26", "B27": "C27", "B28": "C28", "B29": "C29",
  "C30": "C30", "C31": "C31", "C32": "C32", "C33": "C33", "C34": "C34", "C35": "C35", "C36": "C36",
  "B37": "C37", "B38": "C38", "B39": "C39", "B40": "C40", "B41": "C41",
};

function translateYYZGate(rawGate, station) {
  const clean = (rawGate || "").trim().toUpperCase();

  if (station !== "YYZ" || !clean || clean === "-") {
    // Not YYZ, or no gate assigned — nothing to translate
    return { gate: rawGate || "-", original: rawGate || "-", isAVOP: false, needsVerification: false };
  }

  const stand = YYZ_GATE_TO_STAND[clean];
  if (stand) {
    return { gate: stand, original: rawGate, isAVOP: true, needsVerification: false };
  }

  // A YYZ gate that isn't in the mapping table yet — show the original
  // passenger gate, but flag it so the UI can warn it's unverified.
  return { gate: rawGate, original: rawGate, isAVOP: false, needsVerification: true };
}

async function fetchLiveFlights() {
  const icon = document.getElementById("refresh-icon");
  if (icon) icon.classList.add("spin");

  try {
    const resp = await fetch(SHEET_CSV_URL + "&cachebust=" + Date.now());
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const text = await resp.text();
    const rows = parseCSV(text);

    const dataRows = rows.slice(1).filter(r => r[COL.flight] && r[COL.flight] !== "Flight");

    if (!dataRows.length) {
      console.warn("Sheet returned no flight rows — falling back to sample data");
      loadSampleFlights();
      return;
    }

    const flights = dataRows.map(r => {
      const station = r[COL.station] || r[COL.dest]; // fall back for older sheet format
      const std = toDate(r[COL.scheduled]);
      // Date tag (YYYY-MM-DD) so the same flight number recurring on different
      // days gets a distinct id — otherwise "today's PD157" and "tomorrow's
      // PD157" would collide under the same id and COMAT/favourites couldn't
      // tell them apart.
      const dateTag = (std && !Number.isNaN(std.getTime())) ? std.toISOString().slice(0, 10) : "unknown";
      const gateInfo = translateYYZGate(r[COL.gate], station);
      return {
        // Unique per (flight, station, date)
        id:        `${r[COL.flight]}_${station}_${dateTag}`,
        flight:    r[COL.flight],
        origin:    r[COL.origin],
        dest:      r[COL.dest],
        station:   station,
        direction: r[COL.direction],  // ARRIVAL / DEPARTURE, relative to `station`
        std:       std,
        etd:       toDate(r[COL.estimated]),
        status:    r[COL.status]    || "Scheduled",
        gate:              gateInfo.gate,             // AVOP stand for YYZ, passenger gate everywhere else
        gateOriginal:      gateInfo.original,          // the raw passenger gate, always, for reference
        gateIsAVOP:        gateInfo.isAVOP,             // true only when a real AVOP mapping was found
        gateNeedsVerification: gateInfo.needsVerification, // true for a YYZ gate not yet in the mapping table
        tail:      r[COL.tail]      || "-",
        aircraft:  r[COL.tail]      || "-",   // your table's "Aircraft" column renders f.aircraft — feed has no aircraft type, so show tail number here instead
        codeshare: r[COL.codeshare] || "-",
      };
    });

    saveFlights(flights);
    renderFlights();
    updateFlightDropdown();

    const lastUpdated = dataRows[0]?.[COL.updated] || new Date().toLocaleTimeString();
    const el = document.getElementById("flight-last-updated");
    if (el) el.textContent = "Live data — sheet updated " + lastUpdated;

    const badge = document.getElementById("live-badge");
    if (badge) badge.style.display = "inline-flex";

  } catch (err) {
    console.error("Failed to load from Sheet:", err);
    loadSampleFlights();
    const el = document.getElementById("flight-last-updated");
    if (el) el.textContent = "Sheet unavailable — showing cached data";
  } finally {
    if (icon) icon.classList.remove("spin");
  }
}

fetchLiveFlights();                          // run once immediately on load
setInterval(fetchLiveFlights, REFRESH_MS);   // then keep refreshing every 60s
