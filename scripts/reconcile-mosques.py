#!/usr/bin/env python3
"""Reconcile Mapped Muslim pins to MosqueIndex (OSM + Google Maps places).

MosqueIndex publishes ~2,700–3,100 US mosques assembled from OpenStreetMap
and Google Maps place data — the same verification path the US Mosque Survey
used. HIFLD/IRS mailing addresses under-count (~2,200) and often sit far from
the actual prayer site, so a naive "add gaps" merge overshoots ~4,000.

Default mode (--replace-muslim) swaps Mapped Muslim pins for the MosqueIndex
list (~2,743), which sits next to Census 2,771 / Survey 2,769. Other religions
stay on HIFLD.

Usage:
    scripts/reconcile-mosques.py                # replace muslim + write
    scripts/reconcile-mosques.py --dry          # fetch + report only
    scripts/reconcile-mosques.py --add-gaps     # append unmatched only (overshoots)
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
PLACES = DATA / "places.json"
PLACES_GZ = DATA / "places.json.gz"
SUMMARY = DATA / "summary.json"
CACHE = RAW / "mosqueindex-points.jsonl"
REPORT = RAW / "mosque-reconcile-report.json"

SITEMAP = "https://mosqueindex.com/sitemap.xml"
UA = "faithmap-app/1.0 (mosque reconcile; markmaga.com)"
MUSLIM = 2  # RELIGIONS index in places.json
MATCH_M = 250.0  # treat as same place within this distance

# Next.js RSC embeds points as {\"lat\":…,\"lon\":…,\"name\":…,\"slug\":…}
POINT_RE = re.compile(
    r'\{\\"lat\\":(-?\d+\.?\d*),\\"lon\\":(-?\d+\.?\d*),\\"name\\":\\"((?:\\\\.|[^\\"\\\\])*)\\",\\"slug\\":\\"((?:\\\\.|[^\\"\\\\])*)\\"\}'
)
STATE_SLUG = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district-of-columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new-hampshire": "NH",
    "new-jersey": "NJ",
    "new-mexico": "NM",
    "new-york": "NY",
    "north-carolina": "NC",
    "north-dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode-island": "RI",
    "south-carolina": "SC",
    "south-dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west-virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.read().decode("utf-8", "ignore")
        except (urllib.error.URLError, TimeoutError) as err:
            last = err
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("fetch failed %s: %s" % (url, last))


def unescape(s: str) -> str:
    try:
        return bytes(s, "utf-8").decode("unicode_escape")
    except Exception:
        return s.replace('\\"', '"').replace("\\\\", "\\")


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


def state_urls() -> list[tuple[str, str]]:
    xml = fetch(SITEMAP)
    locs = re.findall(r"<loc>(https://mosqueindex\.com/mosques/[^<]+)</loc>", xml)
    out = []
    for url in locs:
        path = url.rstrip("/").split("/mosques/", 1)[1]
        if "/" in path:
            continue
        st = STATE_SLUG.get(path.lower())
        if st:
            out.append((st, url))
    out.sort(key=lambda x: x[0])
    return out


def parse_points(html: str, state: str) -> list[dict]:
    rows = []
    for m in POINT_RE.finditer(html):
        lat = float(m.group(1))
        lon = float(m.group(2))
        name = unescape(m.group(3)).strip()
        slug = unescape(m.group(4)).strip()
        if not name or not slug:
            continue
        # CONUS + AK + HI (MosqueIndex is US-only).
        if not (
            (24.0 <= lat <= 49.5 and -125.0 <= lon <= -66.0)
            or (51.0 <= lat <= 72.0 and -180.0 <= lon <= -129.0)  # AK
            or (18.5 <= lat <= 22.5 and -161.0 <= lon <= -154.0)  # HI
        ):
            continue
        rows.append(
            {
                "name": name,
                "slug": slug,
                "lat": lat,
                "lon": lon,
                "state": state,
                "source": "mosqueindex",
            }
        )
    return rows


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def cell(lat: float, lon: float) -> tuple[int, int]:
    # ~0.01 deg ≈ 1.1 km grid for neighbor lookup
    return (int(math.floor(lat * 100)), int(math.floor(lon * 100)))


def load_mapped_muslim(places: list) -> list[tuple[float, float, str]]:
    out = []
    for p in places:
        if p[1] != MUSLIM:
            continue
        out.append((float(p[2]), float(p[3]), p[0]))
    return out


def nearest(
    lat: float, lon: float, grid: dict, mapped: list
) -> tuple[float, str | None]:
    best = 1e18
    best_name = None
    ci, cj = cell(lat, lon)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for idx in grid.get((ci + di, cj + dj), ()):
                mlat, mlon, mname = mapped[idx]
                d = haversine_m(lat, lon, mlat, mlon)
                if d < best:
                    best = d
                    best_name = mname
    return best, best_name


def pull_all(force: bool) -> list[dict]:
    if CACHE.exists() and not force and CACHE.stat().st_size > 10_000:
        rows = []
        with CACHE.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        print("  cached %d mosqueindex points" % len(rows), flush=True)
        return rows

    RAW.mkdir(parents=True, exist_ok=True)
    urls = state_urls()
    print("  state pages %d" % len(urls), flush=True)
    by_slug: dict[str, dict] = {}
    for i, (st, url) in enumerate(urls, 1):
        print("  [%d/%d] %s" % (i, len(urls), st), flush=True)
        html = fetch(url)
        pts = parse_points(html, st)
        for p in pts:
            by_slug[p["slug"]] = p
        print("    +%d unique %d" % (len(pts), len(by_slug)), flush=True)
        time.sleep(0.35)

    rows = sorted(by_slug.values(), key=lambda r: (r["state"], r["name"].lower()))
    with CACHE.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    return rows


def rows_from_external(external: list[dict]) -> list:
    rows = []
    for g in external:
        rows.append(
            [
                title_name(g["name"]),
                MUSLIM,
                round(float(g["lat"]), 5),
                round(float(g["lon"]), 5),
                g["state"],
                "",
                "",
            ]
        )
    return rows


def replace_muslim(external: list[dict], places: list, meta: dict) -> tuple[list, dict]:
    kept = [p for p in places if p[1] != MUSLIM]
    added_rows = rows_from_external(external)
    out = kept + added_rows
    by = dict(meta.get("by") or {})
    old_muslim = int(by.get("muslim") or 0)
    by["muslim"] = len(added_rows)
    meta["by"] = by
    meta["n"] = len(out)
    meta["built"] = date.today().isoformat()
    meta["source"] = (
        "HIFLD All Places of Worship (non-Muslim) + MosqueIndex "
        "(OSM + Google Maps place data) for Muslim"
    )
    meta["mosque_reconcile"] = {
        "mode": "replace-muslim",
        "removed_hifld_muslim": old_muslim,
        "added_mosqueindex": len(added_rows),
        "match_m": MATCH_M,
        "external": "MosqueIndex (OSM + Google Maps place data)",
        "date": date.today().isoformat(),
    }
    return out, meta


def add_gaps(gaps: list[dict], places: list, meta: dict) -> tuple[list, dict]:
    added_rows = rows_from_external(gaps)
    places = places + added_rows
    by = dict(meta.get("by") or {})
    by["muslim"] = int(by.get("muslim") or 0) + len(added_rows)
    meta["by"] = by
    meta["n"] = len(places)
    meta["built"] = date.today().isoformat()
    src = meta.get("source") or "HIFLD"
    if "MosqueIndex" not in src:
        meta["source"] = src + " + MosqueIndex gaps (%d)" % len(added_rows)
    meta["mosque_reconcile"] = {
        "mode": "add-gaps",
        "added": len(added_rows),
        "match_m": MATCH_M,
        "external": "MosqueIndex (OSM + Google Maps place data)",
        "date": date.today().isoformat(),
    }
    return places, meta


def write_places(places: list, meta: dict, religions: list) -> None:
    payload = {"meta": meta, "k": religions, "p": places}
    raw = json.dumps(payload, separators=(",", ":"))
    tmp = PLACES.with_suffix(".json.tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(PLACES)
    with gzip.open(PLACES_GZ, "wt", encoding="utf-8", compresslevel=6) as gz:
        gz.write(raw)
    SUMMARY.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="report only, do not write places")
    ap.add_argument("--force", action="store_true", help="refetch MosqueIndex pages")
    ap.add_argument(
        "--add-gaps",
        action="store_true",
        help="append unmatched MosqueIndex rows (overshoots census; debug only)",
    )
    args = ap.parse_args()

    print("pulling MosqueIndex…", flush=True)
    external = pull_all(args.force)
    print("external %d" % len(external), flush=True)

    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    places = payload["p"]
    meta = payload.get("meta") or {}
    religions = payload.get("k") or []
    mapped = load_mapped_muslim(places)
    print("mapped muslim %d" % len(mapped), flush=True)

    grid: dict[tuple[int, int], list[int]] = {}
    for i, (lat, lon, _) in enumerate(mapped):
        grid.setdefault(cell(lat, lon), []).append(i)

    matched = 0
    gaps = []
    by_state_gap: dict[str, int] = {}
    for row in external:
        d, _ = nearest(row["lat"], row["lon"], grid, mapped)
        if d <= MATCH_M:
            matched += 1
            continue
        gaps.append(row)
        by_state_gap[row["state"]] = by_state_gap.get(row["state"], 0) + 1

    by_state_ext: dict[str, int] = {}
    for row in external:
        by_state_ext[row["state"]] = by_state_ext.get(row["state"], 0) + 1

    mode = "add-gaps" if args.add_gaps else "replace-muslim"
    report = {
        "built": date.today().isoformat(),
        "mode": mode,
        "external_n": len(external),
        "mapped_muslim": len(mapped),
        "matched_within_m": MATCH_M,
        "matched": matched,
        "gaps_if_add": len(gaps),
        "replace_muslim_n": len(external),
        "census_muslim": 2771,
        "mosque_survey_2020": 2769,
        "external_by_state": dict(sorted(by_state_ext.items(), key=lambda kv: -kv[1])),
        "gaps_by_state": dict(sorted(by_state_gap.items(), key=lambda kv: -kv[1])),
        "gap_sample": [
            {"name": g["name"], "state": g["state"], "slug": g["slug"]} for g in gaps[:40]
        ],
    }
    RAW.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        "matched@%.0fm %d  MosqueIndex %d  HIFLD muslim %d  (census 2771 / survey 2769)"
        % (MATCH_M, matched, len(external), len(mapped)),
        flush=True,
    )
    print("mode %s → muslim count %d" % (mode, len(external) if mode == "replace-muslim" else len(mapped) + len(gaps)), flush=True)
    top = list(report["external_by_state"].items())[:12]
    for st, n in top:
        print("  MI %s %d" % (st, n), flush=True)

    if args.dry:
        print("dry run — wrote %s only" % REPORT, flush=True)
        return 0

    if args.add_gaps:
        places, meta = add_gaps(gaps, places, meta)
    else:
        places, meta = replace_muslim(external, places, meta)
    write_places(places, meta, religions)
    print("wrote %s muslim now %d" % (PLACES, meta["by"]["muslim"]), flush=True)
    print("assigning counties…", flush=True)
    import subprocess

    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "assign-counties.py")])
    if rc != 0:
        print("assign-counties failed (%d)" % rc, file=sys.stderr)
        return rc
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as err:
        print("reconcile-mosques failed: %s" % err, file=sys.stderr)
        raise
