#!/usr/bin/env python3
"""Full OSM-gap scorecard. Package only — does not write places.json.

Buckets (exclusive, first match):
  name_junk       osm_should_drop leftovers
  hifld_dupe      similar name, same religion, 250 m–1 km from a city-filled pin
  on_building     ≤250 m from any Geofabrik POW area (any name)
  local_snap      unique same-name small area within 25 km (not statewide)
  impossible      not on Natural Earth land, or inside a NE lake
  rural_expected  remainder on land (OSM node, no building drawn)

Does not repeat statewide same-name snaps (the 15k / 1,171 km failure).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".pydeps"))
sys.path.insert(0, str(ROOT / "scripts"))
import shapefile  # noqa: E402
from shapely.geometry import Point, shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
from shapely.prepared import prep  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402
from mapped_filters import osm_should_drop  # noqa: E402

PLACES = ROOT / "data" / "places.json"
RAW = ROOT / "data" / "raw" / "geofabrik-pofw"
NE = ROOT / "data" / "raw" / "natural-earth"
OUT = ROOT / "data" / "raw" / "osm-validate-package" / "scorecard"

MUSLIM = 2
STOP = {"THE", "OF", "AND", "A", "AN", "CHURCH", "INC", "PARISH"}
MIN_MOVE_M = 1500.0
ON_BUILDING_M = 250.0
DUPE_MIN_M = 250.0
DUPE_MAX_M = 1000.0
LOCAL_MAX_M = 25000.0
FAR_UNIQUE_MAX_M = 100000.0
MAX_BUILDING_SNAP_M = 800.0
MAX_BUILDING_NEAR_M = 5000.0
MIN_TOKENS = 3
CELL_NEAR = 0.02
CELL_DUPE = 0.01

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


def area_point(sh) -> tuple[float, float, float] | None:
    pts = sh.points or []
    if len(pts) < 3:
        return None
    parts = list(sh.parts or [0])
    end = parts[1] if len(parts) > 1 else len(pts)
    ring = pts[0:end]
    if len(ring) < 3:
        return None
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return sum(ys) / len(ys), sum(xs) / len(xs), bbox_span_m(xs, ys)


def cell_key(lat: float, lon: float, cell: float) -> tuple[int, int]:
    return (int(math.floor(lat / cell)), int(math.floor(lon / cell)))


def dest_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(float(lat), 5), round(float(lon), 5))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def tsv_write(path: Path, headers: list[str], rows: list[list]) -> None:
    def cell(v) -> str:
        return str(v).replace("\t", " ").replace("\n", " ").replace("\r", "")

    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(cell(c) for c in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_areas() -> tuple[list, dict, dict]:
    """(near_pts, tok_areas, occupied later). near_pts: lat,lon,name,span"""
    near: list[tuple[float, float, str, float]] = []
    tok_areas: dict[tuple[str, frozenset[str]], list] = defaultdict(list)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for slug, st in SLUG_STATE:
        folder = RAW / slug
        shp = folder / "gis_osm_pofw_a_free_1.shp"
        if not shp.exists():
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
            if span <= MAX_BUILDING_NEAR_M:
                idx = len(near)
                near.append((lat, lon, name, span))
                grid[cell_key(lat, lon, CELL_NEAR)].append(idx)
            tok = tokens(name)
            if len(tok) >= 2 and span <= MAX_BUILDING_SNAP_M:
                tok_areas[(st, tok)].append((lat, lon, span, name))
        print("  areas %s" % st, flush=True)
    print("  near buildings %d  named small %d keys" % (len(near), len(tok_areas)), flush=True)
    return near, tok_areas, grid


def nearest_area(lat: float, lon: float, near: list, grid: dict, max_m: float) -> tuple[float, str]:
    n_cells = int(math.ceil((max_m / 1000.0) / (CELL_NEAR * 111.0))) + 1
    ci, cj = cell_key(lat, lon, CELL_NEAR)
    best = 1e18
    best_name = ""
    for di in range(-n_cells, n_cells + 1):
        for dj in range(-n_cells, n_cells + 1):
            for idx in grid.get((ci + di, cj + dj), ()):
                alat, alon, aname, _span = near[idx]
                d = haversine_m(lat, lon, alat, alon)
                if d < best:
                    best = d
                    best_name = aname
    return best, best_name


def load_land_lakes():
    land_path = NE / "ne_10m_land.geojson"
    lake_path = NE / "ne_10m_lakes.geojson"
    land_fc = json.loads(land_path.read_text(encoding="utf-8"))
    land = unary_union([shape(f["geometry"]) for f in land_fc["features"] if f.get("geometry")])
    land_p = prep(land)
    lake_fc = json.loads(lake_path.read_text(encoding="utf-8"))
    lakes = []
    for f in lake_fc["features"]:
        g = f.get("geometry")
        if not g:
            continue
        geom = shape(g)
        minx, miny, maxx, maxy = geom.bounds
        if maxx < -180 or minx > -66 or maxy < 18 or miny > 72:
            continue
        lakes.append(geom)
    tree = STRtree(lakes) if lakes else None
    print("  land ok  lakes in US bbox %d" % len(lakes), flush=True)
    return land_p, tree, lakes


def in_lake(lat: float, lon: float, tree, lakes) -> bool:
    if tree is None:
        return False
    pt = Point(lon, lat)
    hits = tree.query(pt)
    for i in hits:
        geom = lakes[int(i)] if not hasattr(i, "geom_type") else i
        # shapely 2 STRtree.query returns indexes
        if hasattr(i, "geom_type"):
            if i.contains(pt):
                return True
        elif lakes[int(i)].contains(pt):
            return True
    return False


def main() -> int:
    raw_bytes = PLACES.read_bytes()
    sha = hashlib.sha256(raw_bytes).hexdigest()[:12]
    payload = json.loads(raw_bytes.decode("utf-8"))
    rows = payload.get("p") or []
    print("places %d sha %s" % (len(rows), sha), flush=True)

    occupied: dict[tuple[float, float], list[str]] = defaultdict(list)
    hifld = []  # lat, lon, name, rel, state, tokens
    hifld_grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    gaps = []
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            continue
        occupied[dest_key(row[2], row[3])].append(row[0] or "")
        city = (row[6] or "").strip()
        rec = {
            "i": i,
            "name": row[0] or "",
            "r": int(row[1]),
            "lat": float(row[2]),
            "lon": float(row[3]),
            "state": row[4] or "",
            "county": row[5] or "",
            "city": city,
            "tok": tokens(row[0] or ""),
        }
        if rec["r"] == MUSLIM:
            continue
        if city:
            idx = len(hifld)
            hifld.append(rec)
            hifld_grid[cell_key(rec["lat"], rec["lon"], CELL_DUPE)].append(idx)
        else:
            gaps.append(rec)
    print("osm-gap %d  hifld-like (has city) %d" % (len(gaps), len(hifld)), flush=True)

    print("loading Geofabrik POW areas…", flush=True)
    near, tok_areas, near_grid = load_areas()
    print("loading Natural Earth land/lakes…", flush=True)
    land_p, lake_tree, lakes = load_land_lakes()

    buckets = Counter()
    junk_rows = []
    dupe_rows = []
    local_rows = []
    local_blocked = []
    impossible_rows = []
    far_unique = []
    on_b_n = 0
    rural_n = 0

    print("classifying…", flush=True)
    local_raw = []  # hold then dest-unique filter

    for gi, p in enumerate(gaps):
        if gi and gi % 40000 == 0:
            print("  %d / %d" % (gi, len(gaps)), flush=True)

        drop = osm_should_drop(p["name"])
        if drop:
            buckets["name_junk"] += 1
            junk_rows.append([p["name"], p["state"], "%.5f" % p["lat"], "%.5f" % p["lon"], drop])
            continue

        # HIFLD dupe
        dupe = None
        ci, cj = cell_key(p["lat"], p["lon"], CELL_DUPE)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for idx in hifld_grid.get((ci + di, cj + dj), ()):
                    h = hifld[idx]
                    if h["r"] != p["r"]:
                        continue
                    d = haversine_m(p["lat"], p["lon"], h["lat"], h["lon"])
                    if d < DUPE_MIN_M or d > DUPE_MAX_M:
                        continue
                    jac = jaccard(p["tok"], h["tok"])
                    if jac < 0.7 and p["tok"] != h["tok"]:
                        continue
                    if dupe is None or d < dupe[0]:
                        dupe = (d, h, jac)
        if dupe:
            buckets["hifld_dupe"] += 1
            d, h, jac = dupe
            dupe_rows.append(
                [
                    p["name"],
                    h["name"],
                    p["state"],
                    "%.5f" % p["lat"],
                    "%.5f" % p["lon"],
                    "%.5f" % h["lat"],
                    "%.5f" % h["lon"],
                    round(d),
                    round(jac, 2),
                ]
            )
            continue

        d_b, bname = nearest_area(p["lat"], p["lon"], near, near_grid, ON_BUILDING_M + 50)
        if d_b <= ON_BUILDING_M:
            buckets["on_building"] += 1
            on_b_n += 1
            continue

        # local unique-name within 25 km
        cands = tok_areas.get((p["state"], p["tok"])) or []
        if len(p["tok"]) >= MIN_TOKENS and cands:
            local = []
            far = []
            for lat, lon, span, aname in cands:
                d = haversine_m(p["lat"], p["lon"], lat, lon)
                if d < MIN_MOVE_M:
                    continue
                if d <= LOCAL_MAX_M:
                    local.append((d, lat, lon, span, aname))
                elif d <= FAR_UNIQUE_MAX_M:
                    far.append((d, lat, lon, span, aname))
            if len(local) == 1:
                d, lat, lon, span, aname = local[0]
                local_raw.append(
                    {
                        "from_name": p["name"],
                        "to_name": aname,
                        "state": p["state"],
                        "old_lat": round(p["lat"], 5),
                        "old_lon": round(p["lon"], 5),
                        "new_lat": round(lat, 5),
                        "new_lon": round(lon, 5),
                        "move_km": round(d / 1000.0, 2),
                        "building_m": round(span, 1),
                        "pin": p,
                    }
                )
                continue
            if len(cands) == 1 and far and not local:
                d, lat, lon, span, aname = min(far, key=lambda t: t[0])
                far_unique.append(
                    [
                        p["name"],
                        aname,
                        p["state"],
                        "%.5f" % p["lat"],
                        "%.5f" % p["lon"],
                        "%.5f" % lat,
                        "%.5f" % lon,
                        round(d / 1000.0, 2),
                        "unique-in-state 25-100km — DO NOT AUTO-SNAP",
                    ]
                )

        pt = Point(p["lon"], p["lat"])
        on_land = land_p.contains(pt) or land_p.intersects(pt)
        lake = in_lake(p["lat"], p["lon"], lake_tree, lakes)
        if (not on_land) or lake:
            buckets["impossible"] += 1
            why = "in-lake" if lake else "not-on-land"
            impossible_rows.append(
                [p["name"], p["state"], "%.5f" % p["lat"], "%.5f" % p["lon"], why]
            )
            continue

        buckets["rural_expected"] += 1
        rural_n += 1

    # dest uniqueness + dest occupied for local snaps
    dest_n = defaultdict(list)
    for rec in local_raw:
        dest_n[dest_key(rec["new_lat"], rec["new_lon"])].append(rec)
    for dk, group in dest_n.items():
        if len(group) > 1:
            for rec in group:
                buckets["local_snap_blocked"] += 1
                buckets["rural_expected"] += 1
                rural_n += 1
                local_blocked.append(
                    [
                        rec["from_name"],
                        rec["to_name"],
                        rec["state"],
                        rec["move_km"],
                        "two-to-one-dest %s,%s" % dk,
                    ]
                )
            continue
        rec = group[0]
        others = [n for n in occupied.get(dk, []) if n != rec["from_name"]]
        if others:
            buckets["local_snap_blocked"] += 1
            buckets["rural_expected"] += 1
            rural_n += 1
            local_blocked.append(
                [
                    rec["from_name"],
                    rec["to_name"],
                    rec["state"],
                    rec["move_km"],
                    "dest-already-occupied (%s)" % others[0],
                ]
            )
            continue
        buckets["local_snap"] += 1
        local_rows.append(
            [
                rec["from_name"],
                rec["to_name"],
                rec["state"],
                "%.5f" % rec["old_lat"],
                "%.5f" % rec["old_lon"],
                "%.5f" % rec["new_lat"],
                "%.5f" % rec["new_lon"],
                rec["move_km"],
                rec["building_m"],
                "unique same-name building within 25km",
            ]
        )

    # exclusive primary: local_snap_blocked is overlay on rural
    primary = {
        "name_junk": buckets["name_junk"],
        "hifld_dupe": buckets["hifld_dupe"],
        "on_building": buckets["on_building"],
        "local_snap": buckets["local_snap"],
        "impossible": buckets["impossible"],
        "rural_expected": buckets["rural_expected"],
    }
    summed = sum(primary.values())
    counts = {
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "places_n": len(rows),
        "places_sha256_12": sha,
        "osm_gap_n": len(gaps),
        "primary": primary,
        "primary_sum": summed,
        "local_snap_blocked": buckets["local_snap_blocked"],
        "far_unique_review_25_100km": len(far_unique),
        "wrote_places_json": False,
        "notes": {
            "impossible": "Natural Earth 10m land + lakes. Inland wilderness (Everglades sawgrass) still counts as land.",
            "local_snap": "Capped at 25 km so statewide First-Baptist collisions cannot apply.",
            "rural_expected": "On land, not junk/dupe/building/local-snap. Includes real rural GNIS and undetected inland-wrong pins.",
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "counts.json").write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
    tsv_write(
        OUT / "name-junk.tsv",
        ["name", "state", "lat", "lon", "reason"],
        junk_rows,
    )
    tsv_write(
        OUT / "hifld-dupes.tsv",
        [
            "osm_name",
            "hifld_name",
            "state",
            "osm_lat",
            "osm_lon",
            "hifld_lat",
            "hifld_lon",
            "meters",
            "name_jaccard",
        ],
        dupe_rows,
    )
    local_rows.sort(key=lambda r: -float(r[7]))
    tsv_write(
        OUT / "local-snaps.tsv",
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
        local_rows,
    )
    tsv_write(
        OUT / "local-snaps-blocked.tsv",
        ["from_name", "to_name", "state", "move_km", "reason"],
        local_blocked,
    )
    tsv_write(
        OUT / "impossible.tsv",
        ["name", "state", "lat", "lon", "why"],
        impossible_rows,
    )
    far_unique.sort(key=lambda r: -float(r[7]))
    tsv_write(
        OUT / "far-unique-review.tsv",
        [
            "from_name",
            "to_name",
            "state",
            "old_lat",
            "old_lon",
            "new_lat",
            "new_lon",
            "move_km",
            "note",
        ],
        far_unique,
    )

    readme = """# OSM-gap full-set scorecard

Built %s against places.json sha `%s`. n_gap=%d. Primary buckets sum %d.

## Primary (exclusive)

| Bucket | n | Meaning |
|---|---:|---|
| name_junk | %d | `osm_should_drop` leftover |
| hifld_dupe | %d | similar name, same religion, 250 m–1 km from a city-filled pin |
| on_building | %d | ≤250 m from any POW building polygon |
| local_snap | %d | unique same-name small area **within 25 km** |
| impossible | %d | ocean (off NE land) or inside a NE lake |
| rural_expected | %d | remainder on land |

local_snap dest collisions blocked: %d (counted in rural_expected, listed in local-snaps-blocked.tsv)
far-unique-review (unique in state, 25–100 km): %d — **do not auto-snap** (statewide collision risk)

## What this is not
- Not a statewide First-Baptist snap.
- Not an Everglades-only hunt. Impossible = ocean/lake at 1:10m. Inland wrong GNIS still sits in rural_expected.
- Did not write places.json.

## Next (only after review)
- Drop or keep **name_junk** and **hifld_dupe** (duplicates).
- Apply **local-snaps.tsv** only if dest uniqueness still holds on inspection.
- **impossible.tsv** are drop candidates after a glance (waterfront false positives).
- rural_expected is the leftover live OSM set — not a kill list.
""" % (
        counts["built"],
        sha,
        len(gaps),
        summed,
        primary["name_junk"],
        primary["hifld_dupe"],
        primary["on_building"],
        primary["local_snap"],
        primary["impossible"],
        primary["rural_expected"],
        buckets["local_snap_blocked"],
        len(far_unique),
    )
    (OUT / "README.md").write_text(readme, encoding="utf-8")

    print(json.dumps(counts, indent=2), flush=True)
    after = hashlib.sha256(PLACES.read_bytes()).hexdigest()[:12]
    if after != sha:
        print("ERROR places.json changed", file=sys.stderr)
        return 2
    print("places.json unchanged (%s)" % sha, flush=True)
    print("wrote %s" % OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
