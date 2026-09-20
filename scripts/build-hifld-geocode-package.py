#!/usr/bin/env python3
"""Package HIFLD pins whose HERE geocode is not a building.

ADDR_TYPE POSTAL / STREETNAME / LOCALITY / STREETINT are ZIP, street,
town, or intersection — not the door. Does not write places.json.

Usage:
    scripts/build-hifld-geocode-package.py
    scripts/build-hifld-geocode-package.py --sample-only
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "data" / "places.json"
HIFLD = ROOT / "data" / "raw" / "hifld.jsonl"
OSM = ROOT / "data" / "raw" / "osm-pow-us.jsonl"
OUT = ROOT / "data" / "raw" / "hifld-geocode-package"

REL = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
WEAK = {"POSTAL", "STREETNAME", "LOCALITY", "STREETINT"}
SEED = 20260920

WORSHIP_RE = re.compile(
    r"\b(church|chapel|cathedral|parish|synagogue|shul|chabad|"
    r"mosque|masjid|mandir|gurdwara|temple|kingdom hall|"
    r"baptist|methodist|lutheran|presbyterian|episcopal|"
    r"catholic|pentecostal|adventist)\b",
    re.I,
)
ADMIN_RE = re.compile(
    r"\b(ministr(?:y|ies)|association|alliance|federation|council|"
    r"committee|foundation|endowment|fund|trust|publishers|"
    r"housing|gemach|eruv)\b",
    re.I,
)
SMALL = {
    "the",
    "and",
    "of",
    "for",
    "inc",
    "incorporated",
    "corp",
    "church",
    "chapel",
    "temple",
    "synagogue",
    "congregation",
    "cong",
}


def r5(v) -> float:
    return round(float(v), 5)


def tokens(name: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", (name or "").lower())
    return {w for w in words if len(w) >= 3 and w not in SMALL}


def hav_m(a, o, a2, o2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(a2)
    dphi = math.radians(a2 - a)
    dl = math.radians(o2 - o)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def name_kind(name: str) -> str:
    if WORSHIP_RE.search(name or ""):
        return "worship"
    if ADMIN_RE.search(name or ""):
        return "admin"
    return "other"


def bucket(addr_type: str, kind: str) -> str:
    at = addr_type or "?"
    if at == "POSTAL":
        return "postal_" + kind
    if at == "STREETNAME":
        return "streetname"
    return "locality_or_int"


def load_hifld() -> dict[tuple, list[dict]]:
    idx: dict[tuple, list[dict]] = defaultdict(list)
    n = 0
    with HIFLD.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            a = json.loads(line)
            try:
                lat = r5(a["Y"])
                lon = r5(a["X"])
            except (TypeError, ValueError, KeyError):
                continue
            st = (a.get("STATE") or "").strip().upper()
            idx[(st, lat, lon)].append(a)
            n += 1
    print("hifld index %d" % n, flush=True)
    return idx


def load_osm_grid() -> dict[tuple, list[dict]]:
    grid: dict[tuple, list[dict]] = defaultdict(list)
    if not OSM.exists():
        print("osm-pow missing — skip nearby OSM", flush=True)
        return grid
    n = 0
    with OSM.open(encoding="utf-8") as fh:
        for line in fh:
            a = json.loads(line)
            try:
                lat = float(a["lat"])
                lon = float(a["lon"])
            except (TypeError, ValueError, KeyError):
                continue
            st = (a.get("state") or "").strip().upper()
            rec = {
                "name": a.get("name") or "",
                "lat": lat,
                "lon": lon,
                "rel": a.get("rel") or "",
                "tok": tokens(a.get("name") or ""),
            }
            if not rec["tok"]:
                continue
            grid[(st, int(lat * 50), int(lon * 50))].append(rec)
            n += 1
    print("osm grid %d" % n, flush=True)
    return grid


def nearest_osm(st: str, lat: float, lon: float, tok: set[str], grid) -> dict:
    empty = {"osm_name": "", "osm_m": "", "osm_rel": ""}
    if not tok or not grid:
        return empty
    ci, cj = int(lat * 50), int(lon * 50)
    best = None
    best_m = 1e18
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for o in grid.get((st, ci + di, cj + dj), ()):
                if not (tok & o["tok"]):
                    continue
                m = hav_m(lat, lon, o["lat"], o["lon"])
                if m < best_m:
                    best_m = m
                    best = o
    if best is None or best_m > 2000:
        return empty
    return {
        "osm_name": best["name"],
        "osm_m": int(round(best_m)),
        "osm_rel": best["rel"],
    }


def pick_spread(rows: list[dict], n: int, rng: random.Random) -> list[dict]:
    if len(rows) <= n:
        return list(rows)
    by = defaultdict(list)
    for r in rows:
        by[r["state"]].append(r)
    states = sorted(by)
    rng.shuffle(states)
    for st in states:
        rng.shuffle(by[st])
    out = []
    i = 0
    while len(out) < n and i < n * 80:
        st = states[i % len(states)]
        bucket_rows = by[st]
        if bucket_rows:
            out.append(bucket_rows.pop())
        i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-only", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    tsv_path = OUT / "weak-geocodes.tsv"
    if args.sample_only and tsv_path.exists():
        with tsv_path.open(encoding="utf-8") as fh:
            weak = list(csv.DictReader(fh, delimiter="\t"))
        print("loaded %d from tsv" % len(weak), flush=True)
    else:
        hifld = load_hifld()
        grid = load_osm_grid()
        payload = json.loads(PLACES.read_text(encoding="utf-8"))
        rows = payload["p"]
        print("places %d" % len(rows), flush=True)

        weak = []
        matched = 0
        miss = 0
        by_type = Counter()
        for i, row in enumerate(rows):
            if not isinstance(row, list) or len(row) < 7:
                continue
            if not (row[6] or "").strip():
                continue
            rel_i = int(row[1])
            if not (0 <= rel_i < 6):
                continue
            st = row[4]
            lat, lon = r5(row[2]), r5(row[3])
            cands = hifld.get((st, lat, lon)) or []
            nu = (row[0] or "").upper()
            hit = next((a for a in cands if (a.get("NAME") or "").upper() == nu), None)
            if hit is None and cands:
                hit = cands[0]
            if hit is None:
                miss += 1
                continue
            matched += 1
            at = (hit.get("ADDR_TYPE") or "").strip().upper() or "?"
            by_type[at] += 1
            if at not in WEAK:
                continue
            kind = name_kind(row[0] or "")
            tok = tokens(row[0] or "")
            osm = nearest_osm(st, float(row[2]), float(row[3]), tok, grid)
            rec = {
                "i": i,
                "name": row[0],
                "rel": REL[rel_i],
                "lat": row[2],
                "lon": row[3],
                "state": st,
                "county": row[5] or "",
                "city": row[6] or "",
                "addr_type": at,
                "score": hit.get("SCORE") or "",
                "ntee": (hit.get("NTEE_CD") or "")[:8],
                "name_kind": kind,
                "bucket": bucket(at, kind),
                **osm,
            }
            weak.append(rec)
            if len(weak) % 5000 == 0:
                print("  weak %d" % len(weak), flush=True)

        fields = [
            "i",
            "name",
            "rel",
            "lat",
            "lon",
            "state",
            "county",
            "city",
            "addr_type",
            "score",
            "ntee",
            "name_kind",
            "bucket",
            "osm_name",
            "osm_m",
            "osm_rel",
        ]
        with tsv_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
            w.writeheader()
            w.writerows(weak)

        canary = [
            r
            for r in weak
            if "alliance jewish" in (r["name"] or "").lower()
            or "messiahs harvest" in (r["name"] or "").lower()
        ]
        summary = {
            "date": date.today().isoformat(),
            "rule": "HIFLD ADDR_TYPE in POSTAL/STREETNAME/LOCALITY/STREETINT",
            "places_hifld_matched": matched,
            "places_hifld_miss": miss,
            "addr_type_all_hifld": dict(by_type),
            "weak_n": len(weak),
            "by_bucket": dict(Counter(r["bucket"] for r in weak)),
            "by_rel": dict(Counter(r["rel"] for r in weak)),
            "postal_by_rel": dict(
                Counter(r["rel"] for r in weak if r["addr_type"] == "POSTAL")
            ),
            "osm_within_500m": sum(
                1 for r in weak if r["osm_m"] != "" and int(r["osm_m"]) <= 500
            ),
            "osm_within_2000m": sum(1 for r in weak if r["osm_m"] != ""),
            "canary": canary,
            "wrote_places": False,
        }
        (OUT / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        print("matched %d miss %d weak %d" % (matched, miss, len(weak)), flush=True)
        print("buckets", summary["by_bucket"], flush=True)
        print("osm ≤500m %d  ≤2km %d" % (summary["osm_within_500m"], summary["osm_within_2000m"]))
        print("canary", [c["name"] + " " + c["addr_type"] for c in canary], flush=True)

    rng = random.Random(SEED)
    quotas = {
        "postal_worship": 15,
        "postal_admin": 12,
        "postal_other": 15,
        "streetname": 10,
        "locality_or_int": 8,
    }
    by = defaultdict(list)
    for r in weak:
        by[r["bucket"]].append(r)
    sample = []
    for b, n in quotas.items():
        sample.extend(pick_spread(by[b], n, rng))
    samp_dir = OUT / "sample"
    samp_dir.mkdir(parents=True, exist_ok=True)
    fields = list(weak[0].keys()) if weak else []
    with (samp_dir / "sample.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(sample)
    (samp_dir / "sample.json").write_text(
        json.dumps({"seed": SEED, "n": len(sample), "rows": sample}, indent=2) + "\n",
        encoding="utf-8",
    )
    print("sample %d -> %s" % (len(sample), samp_dir / "sample.tsv"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
