#!/usr/bin/env python3
"""Drop OSM-gap twins of HIFLD pins when the names match and they are ≤500 m.

Keeps the city-filled HIFLD pin. Does not touch Hill Station-style pairs
(weaker name, ~1 km, two churches on the same road).

Usage:
    scripts/apply-hifld-dupes.py --dry
    scripts/apply-hifld-dupes.py
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "data" / "places.json"
PLACES_GZ = ROOT / "data" / "places.json.gz"
SUMMARY = ROOT / "data" / "summary.json"
DUPES = ROOT / "data" / "raw" / "osm-validate-package" / "scorecard" / "hifld-dupes.tsv"
REPORT = ROOT / "data" / "raw" / "osm-validate-package" / "scorecard" / "hifld-dupes-applied.json"

MAX_M = 500
MIN_J = 1.0
REL = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]


def r5(v) -> float:
    return round(float(v), 5)


def key(st, lat, lon) -> tuple:
    return (st, r5(lat), r5(lon))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    pairs = []
    with DUPES.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            j = float(row["name_jaccard"])
            m = int(float(row["meters"]))
            if j < MIN_J or m > MAX_M:
                continue
            pairs.append(row)
    print("tight pairs %d (jaccard>=%.1f, ≤%dm)" % (len(pairs), MIN_J, MAX_M), flush=True)

    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    rows = payload["p"]
    osm_at: dict[tuple, list[int]] = defaultdict(list)
    hifld_at: dict[tuple, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            continue
        k = key(row[4], row[2], row[3])
        city = (row[6] or "").strip()
        if city:
            hifld_at[k].append(i)
        else:
            osm_at[k].append(i)

    drop = []
    missed = []
    for p in pairs:
        ok = key(p["state"], p["osm_lat"], p["osm_lon"])
        hk = key(p["state"], p["hifld_lat"], p["hifld_lon"])
        oidxs = osm_at.get(ok) or []
        hidxs = hifld_at.get(hk) or []
        if not oidxs:
            missed.append({**p, "why": "osm-pin-not-found"})
            continue
        if not hidxs:
            missed.append({**p, "why": "hifld-pin-not-found"})
            continue
        # Prefer the OSM row whose name matches the TSV osm_name.
        chosen = [i for i in oidxs if rows[i][0] == p["osm_name"]]
        if not chosen:
            chosen = oidxs
        for i in chosen:
            drop.append(
                {
                    "i": i,
                    "name": rows[i][0],
                    "state": p["state"],
                    "lat": rows[i][2],
                    "lon": rows[i][3],
                    "keep_name": p["hifld_name"],
                    "keep_lat": float(p["hifld_lat"]),
                    "keep_lon": float(p["hifld_lon"]),
                    "meters": int(float(p["meters"])),
                    "rel": REL[int(rows[i][1])] if 0 <= int(rows[i][1]) < 6 else "?",
                }
            )

    drop_i = sorted(set(d["i"] for d in drop), reverse=True)
    print("drop %d unique OSM pins  missed %d" % (len(drop_i), len(missed)), flush=True)
    by_rel = Counter(d["rel"] for d in drop if d["i"] in set(drop_i))
    # recount unique
    uniq = {d["i"]: d for d in drop}
    by_rel = Counter(v["rel"] for v in uniq.values())
    print("  by rel", dict(by_rel), flush=True)

    report = {
        "date": date.today().isoformat(),
        "rule": "jaccard>=1.0 and meters<=500; drop empty-city OSM, keep city-filled HIFLD",
        "tight_pairs": len(pairs),
        "dropped": len(drop_i),
        "missed": len(missed),
        "by_rel": dict(by_rel),
        "dry": args.dry,
        "excluded_example": "Hill Station Missionary Baptist vs Hill Station Baptist (995m, j=0.75) — two churches",
        "dropped_sample": [
            {
                "name": uniq[i]["name"],
                "state": uniq[i]["state"],
                "meters": uniq[i]["meters"],
                "keep": uniq[i]["keep_name"],
            }
            for i in list(uniq)[:25]
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.dry:
        print("dry — wrote %s" % REPORT, flush=True)
        return 0

    drop_set = set(drop_i)
    kept = [row for i, row in enumerate(rows) if i not in drop_set]
    by = Counter()
    for row in kept:
        if isinstance(row, list) and len(row) > 1 and 0 <= int(row[1]) < 6:
            by[REL[int(row[1])]] += 1
    meta = payload.get("meta") or {}
    meta["n"] = len(kept)
    meta["by"] = {k: int(by.get(k, 0)) for k in REL}
    meta["built"] = date.today().isoformat()
    meta["hifld_osm_dupe_drop"] = {
        "date": date.today().isoformat(),
        "dropped": len(drop_set),
        "rule": "exact name tokens, 250–500 m, keep HIFLD (has city)",
        "by": dict(by_rel),
    }
    src = meta.get("source") or ""
    note = " − HIFLD/OSM twins (%d, exact name ≤500 m)" % len(drop_set)
    if "HIFLD/OSM twins" not in src:
        meta["source"] = src + note
    payload["meta"] = meta
    payload["p"] = kept
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
        summary["hifld_osm_dupe_drop"] = meta["hifld_osm_dupe_drop"]
        if "HIFLD/OSM twins" not in (summary.get("source") or ""):
            summary["source"] = (summary.get("source") or "") + note
        SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("wrote places n=%d dropped=%d" % (len(kept), len(drop_set)), flush=True)
    print("after", dict(meta["by"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
