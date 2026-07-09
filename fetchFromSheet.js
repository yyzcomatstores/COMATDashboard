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
  flightKey: 0,   // "Flight Key" (used by AppSheet to link COMAT to flights — not needed by the website itself)
  flight:    1,   // "Flight"
  origin:    2,   // "Origin"
  dest:      3,   // "Destination"
  station:   4,   // "Station" (which home station this row belongs to)
  direction: 5,   // "Direction" (ARRIVAL / DEPARTURE relative to Station)
  scheduled: 6,   // "Scheduled Time"
  estimated: 7,   // "Estimated Time"
  status:    8,   // "Status"
  gate:      9,   // "Gate"
  tail:      10,  // "Tail Number"
  codeshare: 11,  // "Codeshare"
  updated:   12,  // "Last Updated (UTC)"
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
      const std = toDate(r[COL.scheduled]);
      // Date tag (YYYY-MM-DD) so the same flight number recurring on different
      // days gets a distinct id — otherwise "today's PD157" and "tomorrow's
      // PD157" would collide under the same id and COMAT/favourites couldn't
      // tell them apart.
      const dateTag = (std && !Number.isNaN(std.getTime())) ? std.toISOString().slice(0, 10) : "unknown";
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
