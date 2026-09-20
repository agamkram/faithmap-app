#!/usr/bin/env python3
"""Hindu temple-only OSM leftovers: snap mailboxes onto the building, add
real mandirs that were never mapped. Skips academies, foundations, Hindu
Living, and census-as-quota fills.

Usage:
    scripts/apply-hindu-osm-gaps.py --dry
    scripts/apply-hindu-osm-gaps.py
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "data" / "places.json"
PLACES_GZ = ROOT / "data" / "places.json.gz"
SUMMARY = ROOT / "data" / "summary.json"
OUT = ROOT / "data" / "raw" / "hindu-osm-package"

REL = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
HINDU = 3

# HIFLD pin → OSM building, same congregation, unique dest, 250–500 m
# (the national mailbox snap used 250 m and missed these).
SNAPS = [
    {
        "hifld_name": "Tulsi Mandir Inc",
        "state": "NY",
        "from": (40.6857, -73.8354),
        "to": (40.6852581, -73.8315686),
        "osm_name": "Tulsi Mandir",
        "why": "HIFLD mailbox 327 m from OSM building at 103-24 111th St",
    },
    {
        "hifld_name": "Sri Venkateswara Temple and Cultural Center Inc",
        "state": "MI",
        "from": (42.4827, -83.4956),
        "to": (42.48261693333333, -83.49922125),
        "osm_name": "Sri Venkateswara Temple & Cultural Center",
        "why": "HIFLD 297 m from OSM building at 26233 Taft Rd, Novi",
    },
]

# Independent web: real mandirs, nearest mapped Hindu > 2 km.
ADDS = [
    {
        "name": "Shri Krishna Vrundavana Temple",
        "lat": 37.3306198,
        "lon": -121.9068358,
        "state": "CA",
        "county": "Santa Clara",
        "city": "San Jose",
        "fips": "06085",
        "why": "Udupi Puthige Matha mandir, 43 Sunol St; not on map (nearest Hindu 2.1 km)",
    },
    {
        "name": "Nithyanandeshwara Hindu Temple",
        "lat": 34.0769954,
        "lon": -117.6906618,
        "state": "CA",
        "county": "San Bernardino",
        "city": "Montclair",
        "fips": "06071",
        "why": "OSM name Kailasa; latemple.org at 9720 Central Ave, Montclair (nearest Hindu 6.8 km)",
    },
]


def hav_m(a, o, a2, o2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(a2)
    dphi = math.radians(a2 - a)
    dl = math.radians(o2 - o)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def find_hifld(rows, spec):
    lat0, lon0 = spec["from"]
    hits = []
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            continue
        if int(row[1]) != HINDU or row[4] != spec["state"]:
            continue
        if (row[0] or "") != spec["hifld_name"]:
            continue
        d = hav_m(float(row[2]), float(row[3]), lat0, lon0)
        if d < 80:
            hits.append((d, i, row))
    hits.sort()
    return hits[0] if hits else None


def dest_occupied(rows, lat, lon, skip_i, max_m=30.0) -> bool:
    for i, row in enumerate(rows):
        if i == skip_i or not isinstance(row, list) or len(row) < 7:
            continue
        if int(row[1]) != HINDU:
            continue
        if hav_m(float(row[2]), float(row[3]), lat, lon) <= max_m:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    rows = payload["p"]

    snap_ops = []
    for spec in SNAPS:
        hit = find_hifld(rows, spec)
        if not hit:
            print("SNAP MISS", spec["hifld_name"], flush=True)
            continue
        _d, i, row = hit
        to_lat, to_lon = spec["to"]
        if dest_occupied(rows, to_lat, to_lon, i):
            print("SNAP DEST OCCUPIED", spec["hifld_name"], flush=True)
            continue
        snap_ops.append(
            {
                "i": i,
                "name": row[0],
                "state": row[4],
                "city": row[6],
                "from": [float(row[2]), float(row[3])],
                "to": [to_lat, to_lon],
                "moved_m": round(hav_m(float(row[2]), float(row[3]), to_lat, to_lon)),
                "why": spec["why"],
            }
        )

    add_ops = []
    for spec in ADDS:
        if dest_occupied(rows, spec["lat"], spec["lon"], None):
            print("ADD DEST OCCUPIED", spec["name"], flush=True)
            continue
        add_ops.append(spec)

    report = {
        "date": date.today().isoformat(),
        "snaps": snap_ops,
        "adds": [
            {k: v for k, v in a.items() if k != "why"} | {"why": a["why"]}
            for a in add_ops
        ],
        "skipped": [
            "Vedic Devotional and Educational Academy (school)",
            "Sringeri Vidya Bharati Foundation (foundation, 13 km from Arsha Vidya)",
            "Nithyanandeshwara Houston OSM (2.5 km from mapped Nithyananda Houston — not a 500 m snap)",
            "Hindu Living wholesale (orgs + schools)",
            "BAPS / ISKCON directories (403 / DNS)",
        ],
        "census_hindu": 1847,
        "haf_temples_claim": 1000,
        "dry": args.dry,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("snaps", len(snap_ops), "adds", len(add_ops), flush=True)
    for s in snap_ops:
        print("  SNAP %s %s → %d m" % (s["name"], s["city"], s["moved_m"]), flush=True)
    for a in add_ops:
        print("  ADD %s %s, %s" % (a["name"], a["city"], a["state"]), flush=True)

    if args.dry:
        return 0

    for s in snap_ops:
        rows[s["i"]][2] = s["to"][0]
        rows[s["i"]][3] = s["to"][1]
    for a in add_ops:
        rows.append(
            [a["name"], HINDU, a["lat"], a["lon"], a["state"], a["county"], a["city"], a["fips"]]
        )

    by = Counter()
    for row in rows:
        if isinstance(row, list) and len(row) > 1 and 0 <= int(row[1]) < 6:
            by[REL[int(row[1])]] += 1
    meta = payload.get("meta") or {}
    meta["n"] = len(rows)
    meta["by"] = {k: int(by.get(k, 0)) for k in REL}
    meta["built"] = date.today().isoformat()
    meta["hindu_osm_gaps"] = {
        "date": date.today().isoformat(),
        "snapped": len(snap_ops),
        "added": len(add_ops),
        "rule": "OSM Hindu leftover: snap same-name ≤500 m onto building; add verified mandirs >2 km from mapped Hindu",
    }
    src = meta.get("source") or ""
    note = " · Hindu OSM leftover (snap %d, add %d)" % (len(snap_ops), len(add_ops))
    if "Hindu OSM leftover" not in src:
        meta["source"] = src + note
    payload["meta"] = meta
    payload["p"] = rows
    text = json.dumps(payload, separators=(",", ":"))
    tmp = PLACES.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(PLACES)
    PLACES_GZ.write_bytes(gzip.compress(text.encode("utf-8"), 6))
    if SUMMARY.exists():
        summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
        summary["n"] = meta["n"]
        summary["by"] = meta["by"]
        summary["built"] = meta["built"]
        summary["hindu_osm_gaps"] = meta["hindu_osm_gaps"]
        if "Hindu OSM leftover" not in (summary.get("source") or ""):
            summary["source"] = (summary.get("source") or "") + note
        SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("wrote places n=%d hindu=%d" % (len(rows), by["hindu"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
