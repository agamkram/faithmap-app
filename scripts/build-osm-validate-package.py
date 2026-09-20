#!/usr/bin/env python3
"""Rebuild data/raw/osm-validate-package/ from the current map.

Package only. Does not write places.json, gzip, census, app assets,
version bumps, commits, or pushes.

Failed methods this script will not repeat:
- Same-name snaps for generic churches (the 15,017 First-Baptist dry-run)
- Two sources onto one dest (Praise Cathedral stack)
- Applying a stale leftover package
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".pydeps"))
import shapefile  # noqa: E402

PLACES = ROOT / "data" / "places.json"
RAW = ROOT / "data" / "raw" / "geofabrik-pofw"
OUT = ROOT / "data" / "raw" / "osm-validate-package"

MUSLIM = 2
STOP = {"THE", "OF", "AND", "A", "AN", "CHURCH", "INC", "PARISH"}
LANDMARK = re.compile(r"\b(CATHEDRAL|BASILICA|ABBEY|MINSTER|SHRINE)\b", re.I)
MIN_MOVE_M = 1500.0
GEO_GLADES_M = 5000.0
MAX_BUILDING_LANDMARK_M = 800.0
MAX_BUILDING_GEO_M = 5000.0
MAX_LANDMARK_MOVE_KM = 100.0
CELL = 0.05
MIN_TOKENS = 3

SLUG_STATE = [
    ("alabama", "AL"),
    ("alaska", "AK"),
    ("arizona", "AZ"),
    ("arkansas", "AR"),
    ("california_norcal", "CA"),
    ("california_socal", "CA"),
    ("colorado", "CO"),
    ("connecticut", "CT"),
    ("delaware", "DE"),
    ("district-of-columbia", "DC"),
    ("florida", "FL"),
    ("georgia", "GA"),
    ("hawaii", "HI"),
    ("idaho", "ID"),
    ("illinois", "IL"),
    ("indiana", "IN"),
    ("iowa", "IA"),
    ("kansas", "KS"),
    ("kentucky", "KY"),
    ("louisiana", "LA"),
    ("maine", "ME"),
    ("maryland", "MD"),
    ("massachusetts", "MA"),
    ("michigan", "MI"),
    ("minnesota", "MN"),
    ("mississippi", "MS"),
    ("missouri", "MO"),
    ("montana", "MT"),
    ("nebraska", "NE"),
    ("nevada", "NV"),
    ("new-hampshire", "NH"),
    ("new-jersey", "NJ"),
    ("new-mexico", "NM"),
    ("new-york", "NY"),
    ("north-carolina", "NC"),
    ("north-dakota", "ND"),
    ("ohio", "OH"),
    ("oklahoma", "OK"),
    ("oregon", "OR"),
    ("pennsylvania", "PA"),
    ("rhode-island", "RI"),
    ("south-carolina", "SC"),
    ("south-dakota", "SD"),
    ("tennessee", "TN"),
    ("texas", "TX"),
    ("utah", "UT"),
    ("vermont", "VT"),
    ("virginia", "VA"),
    ("washington", "WA"),
    ("west-virginia", "WV"),
    ("wisconsin", "WI"),
    ("wyoming", "WY"),
]

MIAMI_JUNK = (25.2004, -80.8434)
MIAMI_BUILDING = (25.84396, -80.20026)
PRAISE_DEST = (28.06006, -80.65319)


def tokens(name: str) -> frozenset[str]:
    n = (name or "").upper().replace(".", " ")
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = re.sub(r"\bSAINT\b", "ST", n)
    n = re.sub(r"\bMARYS\b", "MARY", n)
    return frozenset(w for w in n.split() if w and w not in STOP)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def bbox_span_m(xs: list[float], ys: list[float]) -> float:
    return haversine_m(min(ys), min(xs), max(ys), max(xs))


def area_point(shape) -> tuple[float, float, float] | None:
    pts = shape.points or []
    if len(pts) < 3:
        return None
    parts = list(shape.parts or [0])
    end = parts[1] if len(parts) > 1 else len(pts)
    ring = pts[0:end]
    if len(ring) < 3:
        return None
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    span = bbox_span_m(xs, ys)
    return sum(ys) / len(ys), sum(xs) / len(xs), span


def cell_key(lat: float, lon: float) -> tuple[int, int]:
    return (int(math.floor(lat / CELL)), int(math.floor(lon / CELL)))


def dest_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(float(lat), 5), round(float(lon), 5))


def tsv_write(path: Path, headers: list[str], rows: list[list]) -> None:
    def cell(v) -> str:
        return str(v).replace("\t", " ").replace("\n", " ").replace("\r", "")

    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(cell(c) for c in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_areas() -> tuple[dict, dict]:
    """Return (areas_by_tok, geo_grid)."""
    areas_by_tok: dict[tuple[str, frozenset[str]], list] = defaultdict(list)
    geo_grid: dict[tuple[int, int], list] = defaultdict(list)
    n_lm = n_geo = 0
    for slug, st in SLUG_STATE:
        folder = RAW / slug
        shp = folder / "gis_osm_pofw_a_free_1.shp"
        if not shp.exists():
            print("  missing areas %s" % slug, flush=True)
            continue
        sf = shapefile.Reader(str(folder / "gis_osm_pofw_a_free_1"))
        fields = [f[0] for f in sf.fields[1:]]
        ni = fields.index("name")
        for i in range(len(sf)):
            try:
                rec = sf.record(i)
                sh = sf.shape(i)
            except Exception:
                continue
            name = (rec[ni] or "").strip()
            if not name:
                continue
            ap = area_point(sh)
            if not ap:
                continue
            lat, lon, span = ap
            if span <= MAX_BUILDING_GEO_M:
                geo_grid[cell_key(lat, lon)].append((lat, lon, name, span))
                n_geo += 1
            tok = tokens(name)
            if len(tok) >= 2 and span <= MAX_BUILDING_LANDMARK_M:
                areas_by_tok[(st, tok)].append((lat, lon, span, name))
                n_lm += 1
        print("  %s areas loaded" % st, flush=True)
    print("  landmark areas %d  geo buildings %d" % (n_lm, n_geo), flush=True)
    return areas_by_tok, geo_grid


def nearest_building(lat: float, lon: float, grid: dict) -> tuple[float, str, float]:
    n_cells = int(math.ceil((GEO_GLADES_M / 1000.0) / (CELL * 111.0))) + 2
    ci, cj = cell_key(lat, lon)
    best = 1e18
    best_name = ""
    best_span = 0.0
    for di in range(-n_cells, n_cells + 1):
        for dj in range(-n_cells, n_cells + 1):
            for alat, alon, aname, span in grid.get((ci + di, cj + dj), ()):
                d = haversine_m(lat, lon, alat, alon)
                if d < best:
                    best = d
                    best_name = aname
                    best_span = span
    return best, best_name, best_span


def osm_gap_pins(rows: list) -> list[dict]:
    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            continue
        if int(row[1]) == MUSLIM:
            continue
        city = (row[6] or "").strip() if len(row) > 6 else ""
        if city:
            continue
        out.append(
            {
                "i": i,
                "name": row[0] or "",
                "r": int(row[1]),
                "lat": float(row[2]),
                "lon": float(row[3]),
                "state": row[4] or "",
                "county": row[5] or "",
            }
        )
    return out


def main() -> int:
    if not PLACES.exists():
        print("missing %s" % PLACES, file=sys.stderr)
        return 1
    raw_bytes = PLACES.read_bytes()
    places_sha = hashlib.sha256(raw_bytes).hexdigest()[:12]
    payload = json.loads(raw_bytes.decode("utf-8"))
    rows = payload.get("p") or []
    print("places %d sha %s" % (len(rows), places_sha), flush=True)

    occupied: dict[tuple[float, float], list[str]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, list) or len(row) < 4:
            continue
        occupied[dest_key(row[2], row[3])].append(row[0] or "")

    gaps = osm_gap_pins(rows)
    print("osm-gap (empty city, not muslim) %d" % len(gaps), flush=True)

    print("loading Geofabrik building polygons…", flush=True)
    areas_by_tok, geo_grid = load_areas()

    proposed = []
    skipped = []
    flags = []
    already_on = 0
    long_demoted = 0

    print("landmark pass…", flush=True)
    for p in gaps:
        if not LANDMARK.search(p["name"]):
            continue
        tok = tokens(p["name"])
        if len(tok) < MIN_TOKENS:
            skipped.append([p["name"], p["state"], "token-length<%d" % MIN_TOKENS])
            flags.append(
                [p["name"], p["state"], p["lat"], p["lon"], "landmark review: token-length<%d" % MIN_TOKENS]
            )
            continue
        cands = areas_by_tok.get((p["state"], tok)) or []
        if len(cands) == 0:
            skipped.append([p["name"], p["state"], "no-matching-small-area"])
            flags.append(
                [p["name"], p["state"], p["lat"], p["lon"], "landmark review: no unique small area match"]
            )
            continue
        if len(cands) != 1:
            skipped.append(
                [p["name"], p["state"], "ambiguous-multiple-areas (%d)" % len(cands)]
            )
            flags.append(
                [
                    p["name"],
                    p["state"],
                    p["lat"],
                    p["lon"],
                    "landmark review: %d same-name buildings (not unique)" % len(cands),
                ]
            )
            continue
        lat, lon, span, aname = cands[0]
        d = haversine_m(p["lat"], p["lon"], lat, lon)
        if d < MIN_MOVE_M:
            already_on += 1
            skipped.append([p["name"], p["state"], "too-close (%dm)" % round(d)])
            continue
        move_km = d / 1000.0
        rec = {
            "from_name": p["name"],
            "to_name": aname,
            "state": p["state"],
            "old_lat": round(p["lat"], 5),
            "old_lon": round(p["lon"], 5),
            "new_lat": round(lat, 5),
            "new_lon": round(lon, 5),
            "move_km": round(move_km, 2),
            "building_m": round(span, 1),
            "why": "landmark OSM-gap pin ≥1.5km from unique small same-name area",
        }
        if move_km > MAX_LANDMARK_MOVE_KM:
            long_demoted += 1
            skipped.append(
                [
                    p["name"],
                    p["state"],
                    "long-move-same-name-risk (%.2fkm)" % move_km,
                ]
            )
            flags.append(
                [
                    p["name"],
                    p["state"],
                    p["lat"],
                    p["lon"],
                    "demoted: long-move-same-name-risk %.2fkm → %s,%s"
                    % (move_km, rec["new_lat"], rec["new_lon"]),
                ]
            )
            continue
        dk = dest_key(rec["new_lat"], rec["new_lon"])
        others = [n for n in occupied.get(dk, []) if n != p["name"]]
        if others:
            skipped.append(
                [p["name"], p["state"], "dest-already-occupied (%s)" % others[0]]
            )
            flags.append(
                [
                    p["name"],
                    p["state"],
                    p["lat"],
                    p["lon"],
                    "skip: dest already has %s (Praise Cathedral rule)" % others[0],
                ]
            )
            continue
        proposed.append(rec)

    dest_n = defaultdict(list)
    for rec in proposed:
        dest_n[dest_key(rec["new_lat"], rec["new_lon"])].append(rec)
    collisions = 0
    kept = []
    for dk, group in dest_n.items():
        if len(group) > 1:
            collisions += 1
            for rec in group:
                skipped.append(
                    [
                        "%s | %s" % (rec["from_name"], rec["to_name"]),
                        rec["state"],
                        "two-to-one-dest -> (%s,%s)" % dk,
                    ]
                )
                flags.append(
                    [
                        rec["from_name"],
                        rec["state"],
                        rec["old_lat"],
                        rec["old_lon"],
                        "Praise Cathedral rule: two-to-one dest %s,%s" % dk,
                    ]
                )
        else:
            kept.append(group[0])
    proposed = kept

    print(
        "  landmark proposed %d  collisions %d  long-demoted %d  already-on %d"
        % (len(proposed), collisions, long_demoted, already_on),
        flush=True,
    )

    print("geography pass (nearest worship building, any name)…", flush=True)
    geo_far = 0
    geo_glades = 0
    geo_samples = []
    for i, p in enumerate(gaps):
        if i and i % 25000 == 0:
            print("  %d / %d" % (i, len(gaps)), flush=True)
        d, bname, span = nearest_building(p["lat"], p["lon"], geo_grid)
        if d < MIN_MOVE_M:
            continue
        if d >= GEO_GLADES_M:
            geo_glades += 1
            reason = "glades-class: ≥5km from any worship building (%.1fkm, nearest %s)" % (
                d / 1000.0,
                bname or "none-in-window",
            )
        else:
            geo_far += 1
            reason = "far-from-any-building: %.2fkm to %s" % (d / 1000.0, bname)
        flags.append([p["name"], p["state"], round(p["lat"], 5), round(p["lon"], 5), reason])
        if d >= GEO_GLADES_M and len(geo_samples) < 12:
            geo_samples.append((d, p, bname))

    flags.sort(key=lambda r: (r[1], r[0]))
    skipped.sort(key=lambda r: (r[1], r[0]))
    proposed.sort(key=lambda r: -r["move_km"])
    geo_samples.sort(key=lambda t: -t[0])

    OUT.mkdir(parents=True, exist_ok=True)
    tsv_write(
        OUT / "proposed-moves.tsv",
        [
            "from_name",
            "to_name",
            "state",
            "old_lat",
            "old_lon",
            "new_lat",
            "new_lon",
            "move_km",
            "building_m",
            "why",
        ],
        [
            [
                r["from_name"],
                r["to_name"],
                r["state"],
                "%.5f" % r["old_lat"],
                "%.5f" % r["old_lon"],
                "%.5f" % r["new_lat"],
                "%.5f" % r["new_lon"],
                r["move_km"],
                r["building_m"],
                r["why"],
            ]
            for r in proposed
        ],
    )
    tsv_write(OUT / "flags.tsv", ["name", "state", "lat", "lon", "reason"], flags)
    tsv_write(OUT / "skipped.tsv", ["names", "state", "reason"], skipped)

    def lookup(name: str) -> list:
        return [row for row in rows if row[0] == name]

    miami = lookup("Saint Marys Cathedral")
    praise_a = lookup("Church of God-praise Cathedral")
    praise_b = lookup("Praise Cathedral Church of God")
    sophia = [
        row
        for row in rows
        if row[0] == "Saint Sophia Greek Orthodox Cathedral" and row[4] == "FL"
    ]
    aug = [row for row in rows if row[0] == "Saint Augustine Cathedral" and row[4] == "CT"]

    def coord_line(row) -> str:
        return "%s @ %.5f, %.5f city=%r county=%r" % (
            row[4],
            row[2],
            row[3],
            row[6] if len(row) > 6 else "",
            row[5],
        )

    canary_lines = [
        "# Canaries",
        "",
        "## Miami — Saint Marys Cathedral",
        "",
        "- Junk GNIS node was `%.5f, %.5f` (Everglades)." % MIAMI_JUNK,
        "- Building is `%.5f, %.5f`." % MIAMI_BUILDING,
        "- **places.json now:** "
        + ("; ".join(coord_line(r) for r in miami) if miami else "NOT FOUND"),
        "- **Judgment:** already snapped — do not re-apply.",
        "",
        "## Praise Cathedral rule (must not be proposed)",
        "",
        "- Church of God-praise Cathedral: "
        + ("; ".join(coord_line(r) for r in praise_a) if praise_a else "NOT FOUND"),
        "- Praise Cathedral Church of God: "
        + ("; ".join(coord_line(r) for r in praise_b) if praise_b else "NOT FOUND"),
        "- Stack dest that was rejected: `%.5f, %.5f`." % PRAISE_DEST,
        "- **Judgment:** keep them apart; any two-to-one dest is in skipped.tsv.",
        "",
        "## Other landmark pins already on a building",
        "",
        "- FL Saint Sophia: "
        + ("; ".join(coord_line(r) for r in sophia) if sophia else "NOT FOUND"),
        "- CT Saint Augustine: "
        + ("; ".join(coord_line(r) for r in aug) if aug else "NOT FOUND"),
        "",
        "## Remaining proposed-moves (live package)",
        "",
    ]
    if not proposed:
        canary_lines.append("- None. Landmark unique-building snaps are exhausted or already applied.")
    else:
        for r in proposed:
            canary_lines.append(
                "- **%s %s:** %.5f,%.5f → %.5f,%.5f (%.2f km). Review before apply."
                % (
                    r["state"],
                    r["from_name"],
                    r["old_lat"],
                    r["old_lon"],
                    r["new_lat"],
                    r["new_lon"],
                    r["move_km"],
                )
            )
    canary_lines += ["", "## Glades-class geography samples (≥5 km from any worship building)", ""]
    if not geo_samples:
        canary_lines.append("- None flagged at ≥5 km.")
    else:
        for d, p, bname in geo_samples:
            canary_lines.append(
                "- **%s %s:** %.5f, %.5f — %.1f km from %s"
                % (p["state"], p["name"], p["lat"], p["lon"], d / 1000.0, bname or "none")
            )
    (OUT / "canaries.md").write_text("\n".join(canary_lines) + "\n", encoding="utf-8")

    counts = {
        "built": date.today().isoformat(),
        "places_n": len(rows),
        "places_sha256_12": places_sha,
        "osm_gap_n": len(gaps),
        "flagged": len(flags),
        "proposed": len(proposed),
        "skipped": len(skipped),
        "unique_dest_collisions": collisions,
        "long_move_demoted": long_demoted,
        "landmark_already_on_building": already_on,
        "geo_far_1p5_to_5km": geo_far,
        "geo_glades_5km": geo_glades,
        "wrote_places_json": False,
    }
    (OUT / "counts.json").write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")

    readme = """# OSM validate package

Built %s against `data/places.json` sha `%s` (n=%d).

## Counts
- osm_gap_n (empty city, not Muslim): %d
- flagged: %d
- proposed: %d
- skipped: %d
- unique_dest_collisions: %d
- long_move_demoted: %d
- landmark already on building: %d
- geo far (1.5–5 km): %d
- geo glades-class (≥5 km): %d

## Method
Scope = Mapped pins with empty city, excluding Muslim (MosqueIndex). OSM Geofabrik POW **area** polygons are the building layer.

**Proposed moves (tiny, landmark-only):** name matches CATHEDRAL|BASILICA|ABBEY|MINSTER|SHRINE; tokenized name (SAINT→ST, MARYS→MARY, drop THE/OF/AND/CHURCH/INC/PARISH); len(tokens)≥3; unique small area (bbox ≲800 m) in that state; pin ≥1.5 km from that area; move ≤100 km; dest not already occupied; dest unique among proposals.

**Geography flags (not moves):** pin ≥1.5 km from the nearest worship building of *any* name. ≥5 km is tagged glades-class. These are review flags. They are never auto-snapped onto a nearby church.

**Praise Cathedral rule:** two proposed moves that share new_lat,new_lon are both skipped. A dest that already has a different Mapped pin is also skipped.

## Failed methods not repeated
- Did not run the 15,017 same-name generic-church snap (First Baptist across Texas).
- Did not apply the leftover stale package (Miami/Sophia already moved; Praise stack reverted).
- Did not Nominatim/Overpass the 167k set.
- Did not write `data/places.json` / `.gz`, census, app.js, index.html, styles.css, sw.js.
- Did not bump version, commit, or push.

## What to do next
Human reviews this folder. Apply only `proposed-moves.tsv` if those rows survive the same test as Miami: unique building, not a name collision. Geography flags are a later drop-or-keep decision — not moves.
""" % (
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        places_sha,
        len(rows),
        len(gaps),
        len(flags),
        len(proposed),
        len(skipped),
        collisions,
        long_demoted,
        already_on,
        geo_far,
        geo_glades,
    )
    (OUT / "README.md").write_text(readme, encoding="utf-8")

    print("wrote %s" % OUT, flush=True)
    print(json.dumps(counts, indent=2), flush=True)
    after = hashlib.sha256(PLACES.read_bytes()).hexdigest()[:12]
    if after != places_sha:
        print("ERROR places.json changed %s → %s" % (places_sha, after), file=sys.stderr)
        return 2
    print("places.json unchanged (%s)" % places_sha, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
