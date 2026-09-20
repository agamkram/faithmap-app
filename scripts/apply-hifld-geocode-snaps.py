#!/usr/bin/env python3
"""Snap weak HIFLD geocodes onto a unique same-name OSM building ≤500 m.

Does not drop Poland-Baptist-class pins (real church, mailbox far from OSM).
Skips Glendale-Baptist→Wesleyan collisions (not unique / not same tokens).

Usage:
    scripts/apply-hifld-geocode-snaps.py --dry
    scripts/apply-hifld-geocode-snaps.py
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "data" / "places.json"
PLACES_GZ = ROOT / "data" / "places.json.gz"
SUMMARY = ROOT / "data" / "summary.json"
OSM = ROOT / "data" / "raw" / "osm-pow-us.jsonl"
WEAK = ROOT / "data" / "raw" / "hifld-geocode-package" / "weak-geocodes.tsv"
OUT = ROOT / "data" / "raw" / "hifld-geocode-package"

REL = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
MAX_M = 500
MIN_MOVE_M = 15
MIN_TOK = 2
STOP = {
    "THE",
    "OF",
    "AND",
    "A",
    "AN",
    "CHURCH",
    "INC",
    "PARISH",
    "CHAPEL",
    "INCORPORATED",
    "CORP",
}
ADMIN_PIN = re.compile(
    r"\b(CONFERENCE|SYNOD|DIOCESE|DISTRICT COUNCIL|GENERAL COUNCIL)\b",
    re.I,
)


def r5(v) -> float:
    return round(float(v), 5)


def tokens(name: str) -> frozenset[str]:
    n = (name or "").upper().replace(".", " ")
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = re.sub(r"\bSAINT\b", "ST", n)
    n = re.sub(r"\bMOUNT\b", "MT", n)
    n = re.sub(r"\bFIRST\b", "1ST", n)
    n = re.sub(r"\bASSEMBLIES\b", "ASSEMBLY", n)
    n = re.sub(r"\bMARYS\b", "MARY", n)
    return frozenset(w for w in n.split() if w and w not in STOP)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def name_match(pin: frozenset[str], osm: frozenset[str]) -> bool:
    if len(pin) < MIN_TOK or len(osm) < MIN_TOK:
        return False
    if jaccard(pin, osm) >= 0.8:
        return True
    if pin <= osm or osm <= pin:
        return True
    return False


def hav_m(a, o, a2, o2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(a2)
    dphi = math.radians(a2 - a)
    dl = math.radians(o2 - o)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def rel_base(rel: str) -> str:
    return (rel or "").split("_", 1)[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    if not WEAK.exists():
        raise SystemExit("missing %s — run build-hifld-geocode-package.py" % WEAK)

    with WEAK.open(encoding="utf-8") as fh:
        weak = list(csv.DictReader(fh, delimiter="\t"))
    print("weak pins %d" % len(weak), flush=True)

    grid: dict[tuple, list[dict]] = defaultdict(list)
    with OSM.open(encoding="utf-8") as fh:
        for line in fh:
            a = json.loads(line)
            try:
                lat = float(a["lat"])
                lon = float(a["lon"])
            except (TypeError, ValueError, KeyError):
                continue
            tok = tokens(a.get("name") or "")
            if len(tok) < MIN_TOK:
                continue
            st = (a.get("state") or "").strip().upper()
            rec = {
                "name": a.get("name") or "",
                "lat": lat,
                "lon": lon,
                "rel": rel_base(a.get("rel") or ""),
                "tok": tok,
            }
            grid[(st, int(math.floor(lat * 50)), int(math.floor(lon * 50)))].append(rec)
    print("osm named %d" % sum(len(v) for v in grid.values()), flush=True)

    proposed = []
    skipped = Counter()
    for p in weak:
        st = p["state"]
        lat, lon = float(p["lat"]), float(p["lon"])
        pin_tok = tokens(p["name"])
        if len(pin_tok) < MIN_TOK:
            skipped["few_tokens"] += 1
            continue
        if ADMIN_PIN.search(p["name"] or ""):
            skipped["admin_name"] += 1
            continue
        hits = []
        ci, cj = int(math.floor(lat * 50)), int(math.floor(lon * 50))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for o in grid.get((st, ci + di, cj + dj), ()):
                    if o["rel"] and o["rel"] != p["rel"]:
                        continue
                    m = hav_m(lat, lon, o["lat"], o["lon"])
                    if m > MAX_M:
                        continue
                    if not name_match(pin_tok, o["tok"]):
                        continue
                    hits.append((m, o))
        if not hits:
            skipped["no_match"] += 1
            continue
        hits.sort(key=lambda x: x[0])
        if len(hits) > 1:
            skipped["not_unique"] += 1
            continue
        m, o = hits[0]
        if m < MIN_MOVE_M:
            skipped["already_there"] += 1
            continue
        proposed.append(
            {
                "i": int(p["i"]),
                "name": p["name"],
                "rel": p["rel"],
                "state": st,
                "city": p["city"],
                "from_lat": lat,
                "from_lon": lon,
                "to_lat": r5(o["lat"]),
                "to_lon": r5(o["lon"]),
                "osm_name": o["name"],
                "meters": int(round(m)),
                "jaccard": round(jaccard(pin_tok, o["tok"]), 3),
                "addr_type": p["addr_type"],
            }
        )

    dest_count = Counter((r["to_lat"], r["to_lon"], r["state"]) for r in proposed)
    unique = []
    for r in proposed:
        if dest_count[(r["to_lat"], r["to_lon"], r["state"])] > 1:
            skipped["dest_shared"] += 1
            continue
        unique.append(r)
    print(
        "snap %d  skipped %s" % (len(unique), dict(skipped)),
        flush=True,
    )
    print("  by rel", dict(Counter(r["rel"] for r in unique)), flush=True)
    print(
        "  median m %s  max m %s"
        % (
            sorted(r["meters"] for r in unique)[len(unique) // 2] if unique else 0,
            max((r["meters"] for r in unique), default=0),
        ),
        flush=True,
    )

    report = {
        "date": date.today().isoformat(),
        "rule": "weak HIFLD ADDR_TYPE; unique same-name OSM ≤500 m; dest unique",
        "weak_n": len(weak),
        "snapped": len(unique),
        "skipped": dict(skipped),
        "by_rel": dict(Counter(r["rel"] for r in unique)),
        "dry": args.dry,
        "sample": unique[:20],
    }
    (OUT / "snaps.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    fields = [
        "i",
        "name",
        "rel",
        "state",
        "city",
        "from_lat",
        "from_lon",
        "to_lat",
        "to_lon",
        "osm_name",
        "meters",
        "jaccard",
        "addr_type",
    ]
    with (OUT / "snaps.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(unique)

    if args.dry:
        print("dry — wrote %s" % (OUT / "snaps.tsv"), flush=True)
        return 0

    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    rows = payload["p"]
    snap_at = {r["i"]: r for r in unique}

    empty_grid: dict[tuple, list[tuple[int, list]]] = defaultdict(list)
    filled_grid: dict[tuple, list[tuple[int, list]]] = defaultdict(list)
    for j, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            continue
        st = row[4]
        try:
            la, lo = float(row[2]), float(row[3])
        except (TypeError, ValueError):
            continue
        cell = (st, int(math.floor(la * 50)), int(math.floor(lo * 50)))
        if (row[6] or "").strip():
            filled_grid[cell].append((j, row))
        else:
            empty_grid[cell].append((j, row))

    kept_snaps = []
    dest_occupied = 0
    for r in unique:
        ci, cj = int(math.floor(r["to_lat"] * 50)), int(math.floor(r["to_lon"] * 50))
        occupied = False
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for j, row in filled_grid.get((r["state"], ci + di, cj + dj), ()):
                    if j == r["i"]:
                        continue
                    if hav_m(float(row[2]), float(row[3]), r["to_lat"], r["to_lon"]) <= 30:
                        occupied = True
                        break
                if occupied:
                    break
            if occupied:
                break
        if occupied:
            dest_occupied += 1
            continue
        kept_snaps.append(r)
    unique = kept_snaps
    snap_at = {r["i"]: r for r in unique}
    print("dest occupied skip %d  still snapping %d" % (dest_occupied, len(unique)), flush=True)

    osm_drop = set()
    for r in unique:
        ci, cj = int(math.floor(r["to_lat"] * 50)), int(math.floor(r["to_lon"] * 50))
        dest_tok = tokens(r["osm_name"])
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for j, row in empty_grid.get((r["state"], ci + di, cj + dj), ()):
                    if j in snap_at:
                        continue
                    m = hav_m(float(row[2]), float(row[3]), r["to_lat"], r["to_lon"])
                    if m > 50:
                        continue
                    if name_match(tokens(row[0]), dest_tok):
                        osm_drop.add(j)

    for i, r in snap_at.items():
        row = rows[i]
        row[2] = r["to_lat"]
        row[3] = r["to_lon"]

    kept = [row for i, row in enumerate(rows) if i not in osm_drop]
    by = Counter()
    for row in kept:
        if isinstance(row, list) and len(row) > 1 and 0 <= int(row[1]) < 6:
            by[REL[int(row[1])]] += 1
    meta = payload.get("meta") or {}
    meta["n"] = len(kept)
    meta["by"] = {k: int(by.get(k, 0)) for k in REL}
    meta["built"] = date.today().isoformat()
    meta["hifld_geocode_snap"] = {
        "date": date.today().isoformat(),
        "snapped": len(unique),
        "osm_twins_dropped": len(osm_drop),
        "rule": "unique same-name OSM ≤500 m",
        "by": dict(Counter(r["rel"] for r in unique)),
    }
    src = meta.get("source") or ""
    note = " · HIFLD mailbox→OSM building (%d)" % len(unique)
    if "mailbox→OSM" not in src:
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
        summary["hifld_geocode_snap"] = meta["hifld_geocode_snap"]
        if "mailbox→OSM" not in (summary.get("source") or ""):
            summary["source"] = (summary.get("source") or "") + note
        SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    report["osm_twins_dropped"] = len(osm_drop)
    report["dry"] = False
    (OUT / "snaps.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "wrote places n=%d snapped=%d osm_twins_dropped=%d"
        % (len(kept), len(unique), len(osm_drop)),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
