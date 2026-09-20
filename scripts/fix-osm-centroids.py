#!/usr/bin/env python3
"""Snap OSM gap-fill points onto the matching building polygon.

Geofabrik POW has two layers: nodes (often old GNIS) and areas (building
footprints). Mean-of-vertices centroids and stale GNIS nodes put churches
in wetlands. If a same-name *small* area exists in that state and the node
is ≥1.5 km away, move the Mapped pin to a point on the building.

Usage:
    scripts/fix-osm-centroids.py --dry
    scripts/fix-osm-centroids.py
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".pydeps"))
import shapefile  # noqa: E402

PLACES = ROOT / "data" / "places.json"
PLACES_GZ = ROOT / "data" / "places.json.gz"
SUMMARY = ROOT / "data" / "summary.json"
RAW = ROOT / "data" / "raw" / "geofabrik-pofw"
REPORT = ROOT / "data" / "raw" / "osm-centroid-fix-report.json"

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

STOP = {"THE", "OF", "AND", "A", "AN", "CHURCH", "INC", "PARISH"}
MIN_MOVE_M = 1500.0
MAX_BUILDING_M = 800.0
# Same-name churches repeat inside a state. Only snap names that are
# almost certainly one building (cathedrals), not "First Baptist".
LANDMARK = re.compile(
    r"\b(CATHEDRAL|BASILICA|ABBEY|MINSTER|SHRINE)\b",
    re.I,
)


def tokens(name: str) -> frozenset[str]:
    n = (name or "").upper().replace(".", " ")
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = re.sub(r"\bSAINT\b", "ST", n)
    n = re.sub(r"\bMARYS\b", "MARY", n)
    return frozenset(w for w in n.split() if w and w not in STOP)


def title_name(raw: str) -> str:
    small = {"of", "the", "and", "or", "de", "la", "el", "da", "du", "van", "st"}
    parts = []
    for i, w in enumerate((raw or "").split()):
        low = w.lower()
        if i and low in small:
            parts.append(low)
        else:
            parts.append(w[:1].upper() + w[1:].lower() if w else w)
    return " ".join(parts).strip()


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


def read_layer(folder: Path, stem: str):
    shp = folder / (stem + ".shp")
    if not shp.exists():
        return
    sf = shapefile.Reader(str(folder / stem))
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
        yield name, sh


def collect_snaps() -> list[dict]:
    areas: dict[tuple[str, frozenset[str]], list[tuple[float, float, float, str]]] = defaultdict(
        list
    )
    for slug, st in SLUG_STATE:
        folder = RAW / slug
        if not folder.exists():
            continue
        for name, sh in read_layer(folder, "gis_osm_pofw_a_free_1"):
            tok = tokens(name)
            if len(tok) < 2:
                continue
            ap = area_point(sh)
            if not ap:
                continue
            lat, lon, span = ap
            if span > MAX_BUILDING_M:
                continue
            areas[(st, tok)].append((lat, lon, span, name))

    snaps = []
    seen = set()
    for slug, st in SLUG_STATE:
        folder = RAW / slug
        if not folder.exists():
            continue
        for name, sh in read_layer(folder, "gis_osm_pofw_free_1"):
            pts = sh.points or []
            if not pts:
                continue
            tok = tokens(name)
            cands = areas.get((st, tok))
            if not cands or len(cands) != 1:
                continue
            if len(tok) < 3:
                continue
            lat, lon, span, aname = cands[0]
            plat, plon = float(pts[0][1]), float(pts[0][0])
            d0 = haversine_m(plat, plon, lat, lon)
            if d0 < MIN_MOVE_M:
                continue
            if not (LANDMARK.search(name) or LANDMARK.search(aname)):
                continue
            key = (st, round(plat, 5), round(plon, 5), title_name(name))
            if key in seen:
                continue
            seen.add(key)
            snaps.append(
                {
                    "state": st,
                    "from_name": title_name(name),
                    "to_name": title_name(aname),
                    "old_lat": round(plat, 5),
                    "old_lon": round(plon, 5),
                    "new_lat": round(lat, 5),
                    "new_lon": round(lon, 5),
                    "move_km": round(d0 / 1000.0, 2),
                    "building_m": round(span, 1),
                }
            )
    snaps.sort(key=lambda s: -s["move_km"])
    dest = defaultdict(int)
    for s in snaps:
        dest[(s["new_lat"], s["new_lon"])] += 1
    snaps = [s for s in snaps if dest[(s["new_lat"], s["new_lon"])] == 1]
    return snaps


def apply_snaps(snaps: list[dict], dry: bool) -> dict:
    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    rows = payload.get("p") or []
    by_key = defaultdict(list)
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            continue
        by_key[(row[4], round(float(row[2]), 5), round(float(row[3]), 5), row[0])].append(i)

    applied = []
    missed = []
    for s in snaps:
        key = (s["state"], s["old_lat"], s["old_lon"], s["from_name"])
        idxs = by_key.get(key) or []
        if not idxs:
            missed.append(s)
            continue
        for i in idxs:
            rows[i][2] = s["new_lat"]
            rows[i][3] = s["new_lon"]
        applied.append({**s, "n": len(idxs)})

    report = {
        "built": date.today().isoformat(),
        "snaps_found": len(snaps),
        "applied": len(applied),
        "not_in_places": len(missed),
        "dry": dry,
        "moves": applied[:80],
        "canary": [
            a
            for a in applied
            if "mary" in a["from_name"].lower() and "cathedral" in a["from_name"].lower()
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if dry:
        return report

    payload["meta"] = payload.get("meta") or {}
    payload["meta"]["built"] = date.today().isoformat()
    payload["meta"]["osm_centroid_fix"] = {
        "date": date.today().isoformat(),
        "applied": len(applied),
        "found": len(snaps),
    }
    text = json.dumps(payload, separators=(",", ":"))
    tmp = PLACES.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(PLACES)
    PLACES_GZ.write_bytes(gzip.compress(text.encode("utf-8"), 6))

    if SUMMARY.exists():
        summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
        summary["osm_centroid_fix"] = payload["meta"]["osm_centroid_fix"]
        SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if not RAW.exists():
        print("missing %s" % RAW, file=sys.stderr)
        return 1
    print("scanning Geofabrik POW points vs buildings…", flush=True)
    snaps = collect_snaps()
    print("snap candidates %d" % len(snaps), flush=True)
    for s in snaps[:15]:
        print(
            "  %s %6.1f km  %s → %s"
            % (s["state"], s["move_km"], s["from_name"][:36], s["to_name"][:36]),
            flush=True,
        )
    report = apply_snaps(snaps, dry=args.dry)
    print(
        "applied %d  not-in-places %d  dry=%s"
        % (report["applied"], report["not_in_places"], args.dry),
        flush=True,
    )
    if report.get("canary"):
        print("canary", report["canary"], flush=True)
    if args.dry:
        print("wrote %s (dry)" % REPORT, flush=True)
        return 0
    print("re-assigning counties…", flush=True)
    rc = subprocess.call(
        [sys.executable, str(ROOT / "scripts" / "assign-counties.py")]
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
