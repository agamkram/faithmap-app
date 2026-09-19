#!/usr/bin/env python3
"""Orleans Parish pilot: add OSM Christian places missing from Mapped (HIFLD).

Pulls OpenStreetMap amenity=place_of_worship in an Orleans-area bbox, keeps
Christian (and church-named untagged) candidates, matches existing Mapped
Christian pins within 250 m, and appends the gaps. County/state labels are
corrected afterward by assign-counties.py.

Usage:
    scripts/pilot-orleans-osm.py
    scripts/pilot-orleans-osm.py --dry
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PLACES = DATA / "places.json"
PLACES_GZ = DATA / "places.json.gz"
SUMMARY = DATA / "summary.json"
RAW = DATA / "raw" / "osm-orleans-pow.jsonl"
REPORT = DATA / "raw" / "orleans-osm-pilot-report.json"

TILES = [
    (29.865, -90.140, 29.970, -90.000),
    (29.865, -90.000, 29.970, -89.865),
    (29.970, -90.140, 30.075, -90.000),
    (29.970, -90.000, 30.075, -89.865),
]
FQ = (29.953, -90.0735, 29.9645, -90.055)
ENDPOINTS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
MATCH_M = 250.0
SKIP_REL = {
    "muslim",
    "jewish",
    "buddhist",
    "hindu",
    "sikh",
    "bahai",
    "unitarian_universalist",
    "shinto",
    "jain",
    "taoist",
    "pagan",
}
OTHER_WORDS = (
    "mosque",
    "masjid",
    "islamic",
    "muslim",
    "synagogue",
    "shul",
    "chabad",
    "temple beth",
)
CHRISTIAN = 0
UA = "faithmap-app/1.0 (orleans osm pilot; markmaga.com)"


def fetch_bbox(bbox: tuple[float, float, float, float]) -> dict:
    s, w, n, e = bbox
    query = f"""
[out:json][timeout:90];
(
  node["amenity"="place_of_worship"]({s},{w},{n},{e});
  way["amenity"="place_of_worship"]({s},{w},{n},{e});
  relation["amenity"="place_of_worship"]({s},{w},{n},{e});
);
out tags center;
"""
    data = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for ep in ENDPOINTS:
        for attempt in range(3):
            try:
                req = urllib.request.Request(ep, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=100) as res:
                    return json.loads(res.read().decode())
            except Exception as err:
                last = err
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(last)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def cell(lat: float, lon: float) -> tuple[int, int]:
    return (int(math.floor(lat * 100)), int(math.floor(lon * 100)))


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


def is_christian_candidate(row: dict) -> bool:
    rel = (row.get("religion") or "").lower()
    if rel in SKIP_REL:
        return False
    name = (row.get("name") or "").lower()
    if any(w in name for w in OTHER_WORDS):
        return False
    if rel == "christian":
        return True
    if rel == "":
        keys = (
            "church",
            "chapel",
            "cathedral",
            "parish",
            "baptist",
            "methodist",
            "lutheran",
            "episcopal",
            "catholic",
            "pentecostal",
            "adventist",
            "orthodox",
            "ministry",
            "ministries",
            "tabernacle",
            "fellowship",
            "congregation",
        )
        return bool(row.get("name")) and any(k in name for k in keys)
    return False


def pull_osm(force: bool) -> list[dict]:
    if RAW.exists() and not force and RAW.stat().st_size > 1000:
        rows = []
        with RAW.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        print("  cached %d OSM rows" % len(rows), flush=True)
        return rows

    RAW.parent.mkdir(parents=True, exist_ok=True)
    seen: set[tuple] = set()
    rows: list[dict] = []
    for i, bbox in enumerate(TILES, 1):
        print("  tile %d/%d" % (i, len(TILES)), flush=True)
        payload = fetch_bbox(bbox)
        for el in payload.get("elements") or []:
            t = el.get("tags") or {}
            lat = el.get("lat") or (el.get("center") or {}).get("lat")
            lon = el.get("lon") or (el.get("center") or {}).get("lon")
            if lat is None or lon is None:
                continue
            key = (el.get("type"), el.get("id"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "osm": "%s/%s" % (el.get("type"), el.get("id")),
                    "name": (t.get("name") or t.get("name:en") or "").strip(),
                    "religion": (t.get("religion") or "").strip().lower(),
                    "denomination": (t.get("denomination") or "").strip().lower(),
                    "lat": float(lat),
                    "lon": float(lon),
                }
            )
        print("    unique %d" % len(rows), flush=True)
        time.sleep(0.5)
    with RAW.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    # Idempotent: skip if pilot already applied
    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    meta = payload.get("meta") or {}
    if meta.get("orleans_osm_pilot") and not args.force and not args.dry:
        print("orleans OSM pilot already applied — use --force to redo", flush=True)
        return 0

    print("pulling OSM Orleans-area POW…", flush=True)
    rows = pull_osm(args.force)
    cand = [r for r in rows if is_christian_candidate(r) and r.get("name")]
    print(
        "OSM %d  christian candidates %d  tags %s"
        % (len(rows), len(cand), Counter(r["religion"] or "?" for r in rows).most_common(8)),
        flush=True,
    )

    places = payload["p"]
    religions = payload.get("k") or []
    mapped = [(p[2], p[3], p[0]) for p in places if p[1] == CHRISTIAN]
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (lat, lon, _) in enumerate(mapped):
        grid.setdefault(cell(lat, lon), []).append(i)

    def nearest(lat: float, lon: float) -> float:
        best = 1e18
        ci, cj = cell(lat, lon)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for idx in grid.get((ci + di, cj + dj), ()):
                    mlat, mlon, _ = mapped[idx]
                    d = haversine_m(lat, lon, mlat, mlon)
                    if d < best:
                        best = d
        return best

    gaps = []
    matched = 0
    for r in cand:
        d = nearest(r["lat"], r["lon"])
        if d <= MATCH_M:
            matched += 1
        else:
            gaps.append(r)

    fq_gaps = [
        r
        for r in gaps
        if FQ[0] <= r["lat"] <= FQ[2] and FQ[1] <= r["lon"] <= FQ[3]
    ]
    print(
        "matched@%.0fm %d  gaps %d  FQ gaps %d"
        % (MATCH_M, matched, len(gaps), len(fq_gaps)),
        flush=True,
    )
    for r in sorted(fq_gaps, key=lambda x: x["name"]):
        print("  FQ + %s" % r["name"], flush=True)

    report = {
        "built": date.today().isoformat(),
        "osm_n": len(rows),
        "candidates": len(cand),
        "matched": matched,
        "gaps": len(gaps),
        "fq_gaps": [{"name": r["name"], "osm": r["osm"]} for r in fq_gaps],
    }
    RAW.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.dry:
        print("dry run — no places write", flush=True)
        return 0

    for g in gaps:
        places.append(
            [
                title_name(g["name"]),
                CHRISTIAN,
                round(g["lat"], 5),
                round(g["lon"], 5),
                "LA",
                "",
                "",
            ]
        )
    by = dict(meta.get("by") or {})
    by["christian"] = int(by.get("christian") or 0) + len(gaps)
    meta["by"] = by
    meta["n"] = len(places)
    meta["built"] = date.today().isoformat()
    src = meta.get("source") or ""
    if "Orleans Parish OSM" not in src:
        meta["source"] = src + " + Orleans-area OSM Christian gaps (%d)" % len(gaps)
    meta["orleans_osm_pilot"] = {
        "date": date.today().isoformat(),
        "added": len(gaps),
        "matched": matched,
        "fq_added": len(fq_gaps),
        "match_m": MATCH_M,
    }
    out = {"meta": meta, "k": religions, "p": places}
    raw = json.dumps(out, separators=(",", ":"))
    tmp = PLACES.with_suffix(".json.tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(PLACES)
    with gzip.open(PLACES_GZ, "wt", encoding="utf-8", compresslevel=6) as gz:
        gz.write(raw)
    SUMMARY.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("wrote places christian %d (+%d)" % (by["christian"], len(gaps)), flush=True)

    print("assigning counties…", flush=True)
    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "assign-counties.py")])
    return rc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as err:
        print("pilot-orleans-osm failed: %s" % err, file=sys.stderr)
        raise
