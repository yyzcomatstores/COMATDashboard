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
      return {
        // Unique per (flight, station) — the same flight number can appear once
        // per home station it touches, each with that station's own gate/status.
        id:        `${r[COL.flight]}_${station}`,
        flight:    r[COL.flight],
        origin:    r[COL.origin],
        dest:      r[COL.dest],
        station:   station,
        direction: r[COL.direction],  // ARRIVAL / DEPARTURE, relative to `station`
        std:       toDate(r[COL.scheduled]),
        etd:       toDate(r[COL.estimated]),
        status:    r[COL.status]    || "Scheduled",
        gate:      r[COL.gate]      || "-",
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
