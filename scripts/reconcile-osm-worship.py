#!/usr/bin/env python3
"""National OSM gap-fill via Geofabrik places-of-worship shapefiles.

Downloads each US state's Geofabrik free shapefile package, extracts the
gis_osm_pofw* layers (points + area centroids), maps fclass → FaithMap's six
religions, and appends pins not within MATCH_M of an existing same-religion
Mapped place. Then near-dedupe + assign-counties.

Usage:
    scripts/reconcile-osm-worship.py
    scripts/reconcile-osm-worship.py --dry
    scripts/reconcile-osm-worship.py --force   # redownload shapefiles
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import re
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".pydeps"))
import shapefile  # noqa: E402

DATA = ROOT / "data"
RAW = DATA / "raw" / "geofabrik-pofw"
PLACES = DATA / "places.json"
PLACES_GZ = DATA / "places.json.gz"
SUMMARY = DATA / "summary.json"
ALL_CACHE = DATA / "raw" / "osm-pow-us.jsonl"
REPORT = DATA / "raw" / "osm-worship-reconcile-report.json"

RELIGIONS = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
REL_INDEX = {k: i for i, k in enumerate(RELIGIONS)}
MATCH_M = 250.0
DEDUPE_M = 100.0
UA = "faithmap-app/1.0 (geofabrik pofw; markmaga.com)"

# Geofabrik path → state abbrev (CA/TX-sized areas use regional extracts)
DOWNLOADS = [
    ("alabama", "AL"),
    ("alaska", "AK"),
    ("arizona", "AZ"),
    ("arkansas", "AR"),
    ("california/norcal", "CA"),
    ("california/socal", "CA"),
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


FCLASS_MAP = {
    "christian": "christian",
    "christian_catholic": "christian",
    "christian_evangelical": "christian",
    "christian_methodist": "christian",
    "christian_lutheran": "christian",
    "christian_orthodox": "christian",
    "christian_protestant": "christian",
    "christian_anglican": "christian",
    "christian_baptist": "christian",
    "jewish": "jewish",
    "muslim": "muslim",
    "hindu": "hindu",
    "buddhist": "buddhist",
    "sikh": "sikh",
}

BAD_KEEP = re.compile(
    r"\b(ENDOWMENT|INSURANCE|FOUNDATION|COMMITTEE|ASSOCIATION|COUNCIL|"
    r"HOUSING|LLC|TRUST|FUND)\b",
    re.I,
)
NAME_FALLBACK = [
    ("muslim", re.compile(r"\b(mosque|masjid|islamic|muslim)\b", re.I)),
    ("sikh", re.compile(r"\b(gurdwara|sikh)\b", re.I)),
    ("jewish", re.compile(r"\b(synagogue|shul|chabad|jewish|temple beth)\b", re.I)),
    ("hindu", re.compile(r"\b(hindu|mandir|swaminarayan|iskcon)\b", re.I)),
    ("buddhist", re.compile(r"\b(buddhist|buddha|zen center|soka gakkai)\b", re.I)),
    (
        "christian",
        re.compile(
            r"\b(church|chapel|cathedral|parish|baptist|methodist|lutheran|"
            r"presbyterian|episcopal|catholic|pentecostal|christian|"
            r"ministr(?:y|ies)|tabernacle|kingdom hall)\b",
            re.I,
        ),
    ),
]


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


def classify(fclass: str, name: str) -> str | None:
    fc = (fclass or "").strip().lower()
    if fc in FCLASS_MAP:
        return FCLASS_MAP[fc]
    if fc.startswith("christian"):
        return "christian"
    for rel, rx in NAME_FALLBACK:
        if rx.search(name or ""):
            return rel
    return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def cell(lat: float, lon: float) -> tuple[int, int]:
    return (int(math.floor(lat * 100)), int(math.floor(lon * 100)))


def download_state(slug: str, force: bool) -> Path:
    """Download zip via curl (follows redirects) and extract pofw layers."""
    safe = slug.replace("/", "_")
    out_dir = RAW / safe
    marker = out_dir / "_ok"
    if marker.exists() and not force:
        return out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    url = "https://download.geofabrik.de/north-america/us/%s-latest-free.shp.zip" % slug
    zip_path = RAW / ("%s-free.shp.zip" % safe)
    print("  download %s…" % slug, flush=True)
    last = None
    for attempt in range(4):
        try:
            # curl handles Geofabrik redirects better than urllib here.
            cmd = [
                "curl",
                "-fsSL",
                "-A",
                UA,
                "--max-time",
                "900",
                "-o",
                str(zip_path),
                url,
            ]
            subprocess.check_call(cmd)
            magic = zip_path.read_bytes()[:4]
            if magic != b"PK\x03\x04":
                raise RuntimeError(
                    "not a zip (%r): %s"
                    % (magic, zip_path.read_text(errors="ignore")[:120])
                )
            break
        except Exception as err:
            last = err
            zip_path.unlink(missing_ok=True)
            time.sleep(4 * (attempt + 1))
    else:
        raise RuntimeError("download %s failed: %s" % (slug, last))

    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            base = name.split("/")[-1]
            if base.startswith("gis_osm_pofw"):
                (out_dir / base).write_bytes(zf.read(name))
    zip_path.unlink(missing_ok=True)
    marker.write_text(date.today().isoformat(), encoding="utf-8")
    return out_dir


def centroid(shape) -> tuple[float, float] | None:
    pts = shape.points or []
    if not pts:
        return None
    # Point
    if shape.shapeType in (1, 11, 21) or len(pts) == 1:
        return float(pts[0][1]), float(pts[0][0])  # lat, lon — shapefile is lon,lat
    # Polygon / multipoint: average ring (Geofabrik lon/lat order)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return sum(ys) / len(ys), sum(xs) / len(xs)


def read_pofw_dir(folder: Path, state: str) -> list[dict]:
    rows = []
    for stem in ("gis_osm_pofw_free_1", "gis_osm_pofw_a_free_1"):
        shp = folder / (stem + ".shp")
        if not shp.exists():
            continue
        sf = shapefile.Reader(str(folder / stem))
        fields = [f[0] for f in sf.fields[1:]]
        fi = fields.index("fclass")
        ni = fields.index("name")
        oi = fields.index("osm_id")
        n = len(sf)
        for i in range(n):
            try:
                rec = sf.record(i)
                shape = sf.shape(i)
            except Exception:
                continue
            name = (rec[ni] or "").strip()
            if not name:
                continue
            rel = classify(rec[fi], name)
            if not rel:
                continue
            try:
                ll = centroid(shape)
            except Exception:
                continue
            if not ll:
                continue
            lat, lon = ll
            if not (18.0 <= lat <= 72.0 and -180.0 <= lon <= -66.0):
                continue
            rows.append(
                {
                    "osm": "geofabrik/%s/%s" % (state, rec[oi]),
                    "name": name,
                    "rel": rel,
                    "lat": lat,
                    "lon": lon,
                    "state": state,
                    "fclass": rec[fi],
                }
            )
    return rows


def pull_all(force: bool) -> list[dict]:
    if ALL_CACHE.exists() and not force and ALL_CACHE.stat().st_size > 100_000:
        rows = []
        with ALL_CACHE.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        print("  cached national %d" % len(rows), flush=True)
        return rows

    RAW.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    rows: list[dict] = []
    items = list(DOWNLOADS)
    for i, (slug, st) in enumerate(items, 1):
        print("[%d/%d] %s (%s)" % (i, len(items), st, slug), flush=True)
        folder = download_state(slug, force)
        part = read_pofw_dir(folder, st)
        n_new = 0
        for r in part:
            if r["osm"] in seen:
                continue
            seen.add(r["osm"])
            rows.append(r)
            n_new += 1
        print("  +%d  national %d" % (n_new, len(rows)), flush=True)
        time.sleep(1.0)

    ALL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with ALL_CACHE.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    return rows


def near_dedupe(places: list) -> tuple[list, int]:
    def norm(s: str) -> str:
        s = (s or "").upper()
        s = re.sub(r"[^A-Z0-9 ]", " ", s)
        s = re.sub(
            r"\b(THE|INC|INCORPORATED|OF|AND|CHURCH|CHAPEL|CATHEDRAL|BASILICA|"
            r"PARISH|SAINT|ST|BAPTIST|METHODIST|CATHOLIC|EPISCOPAL|LUTHERAN|"
            r"PRESBYTERIAN|PENTECOSTAL|ORTHODOX|FELLOWSHIP|COMMUNITY|CENTER|"
            r"CENTRE|MOSQUE|MASJID|SYNAGOGUE|TEMPLE|MANDIR|GURDWARA)\b",
            " ",
            s,
        )
        return re.sub(r"\s+", " ", s).strip()

    def score(row: list) -> tuple:
        n = row[0] or ""
        c = (row[5] or "").strip()
        bad_c = (not c) or c.upper() in ("NOT AVAILABLE", "N/A")
        penalty = 3 if BAD_KEEP.search(n) else 0
        return (-penalty, 0 if bad_c else 2, 1 if (row[6] or "").strip() else 0, len(n))

    parent: dict[int, int] = {}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    by_rel: dict[int, list[int]] = defaultdict(list)
    for i, row in enumerate(places):
        parent[i] = i
        by_rel[row[1]].append(i)

    for idxs in by_rel.values():
        grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i in idxs:
            row = places[i]
            grid[cell(row[2], row[3])].append(i)
        for i in idxs:
            a = places[i]
            na = norm(a[0])
            if len(na) < 4:
                continue
            ci, cj = cell(a[2], a[3])
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for j in grid.get((ci + di, cj + dj), ()):
                        if j <= i:
                            continue
                        b = places[j]
                        if norm(b[0]) != na:
                            continue
                        if haversine_m(a[2], a[3], b[2], b[3]) > DEDUPE_M:
                            continue
                        union(i, j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(places)):
        clusters[find(i)].append(i)

    drop: set[int] = set()
    for members in clusters.values():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda i: score(places[i]), reverse=True)
        for i in members_sorted[1:]:
            drop.add(i)

    kept = [row for i, row in enumerate(places) if i not in drop]
    return kept, len(drop)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    places = list(payload["p"])
    meta = dict(payload.get("meta") or {})
    religions = payload.get("k") or RELIGIONS
    before = Counter(religions[r[1]] for r in places)
    print("before:", dict(before), flush=True)

    print("pulling Geofabrik US places of worship…", flush=True)
    osm = pull_all(args.force)
    by_rel_osm = Counter(r["rel"] for r in osm)
    print("OSM classified %d  %s" % (len(osm), dict(by_rel_osm)), flush=True)

    grids: list[dict] = [defaultdict(list) for _ in RELIGIONS]
    mapped: list[list[tuple]] = [[] for _ in RELIGIONS]
    for row in places:
        r = row[1]
        if r < 0 or r >= len(RELIGIONS):
            continue
        idx = len(mapped[r])
        mapped[r].append((row[2], row[3], row[0]))
        grids[r][cell(row[2], row[3])].append(idx)

    def nearest(rel_i: int, lat: float, lon: float) -> float:
        best = 1e18
        g = grids[rel_i]
        pts = mapped[rel_i]
        ci, cj = cell(lat, lon)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for idx in g.get((ci + di, cj + dj), ()):
                    mlat, mlon, _ = pts[idx]
                    d = haversine_m(lat, lon, mlat, mlon)
                    if d < best:
                        best = d
        return best

    gaps = []
    matched = 0
    matched_by = Counter()
    gaps_by = Counter()
    for r in osm:
        ri = REL_INDEX[r["rel"]]
        d = nearest(ri, r["lat"], r["lon"])
        if d <= MATCH_M:
            matched += 1
            matched_by[r["rel"]] += 1
        else:
            gaps.append(r)
            gaps_by[r["rel"]] += 1

    print(
        "matched@%.0fm %d  gaps %d  %s"
        % (MATCH_M, matched, len(gaps), dict(gaps_by)),
        flush=True,
    )

    report = {
        "built": date.today().isoformat(),
        "source": "Geofabrik US free shapefiles gis_osm_pofw*",
        "osm_n": len(osm),
        "osm_by": dict(by_rel_osm),
        "matched": matched,
        "matched_by": dict(matched_by),
        "gaps": len(gaps),
        "gaps_by": dict(gaps_by),
        "before": dict(before),
        "match_m": MATCH_M,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.dry:
        print("dry run — wrote %s" % REPORT, flush=True)
        return 0

    for g in gaps:
        places.append(
            [
                title_name(g["name"]),
                REL_INDEX[g["rel"]],
                round(g["lat"], 5),
                round(g["lon"], 5),
                g["state"],
                "",
                "",
            ]
        )

    print("near-dedupe…", flush=True)
    places, dropped = near_dedupe(places)
    print("  dropped %d near-dups" % dropped, flush=True)

    after = Counter(religions[r[1]] for r in places)
    meta["by"] = {k: after.get(k, 0) for k in religions}
    meta["n"] = len(places)
    meta["built"] = date.today().isoformat()
    src = meta.get("source") or ""
    note = " + Geofabrik OSM POW gaps (%d, −%d dups)" % (len(gaps), dropped)
    if "Geofabrik OSM POW" not in src:
        meta["source"] = src + note
    meta["osm_worship_reconcile"] = {
        "date": date.today().isoformat(),
        "source": "Geofabrik gis_osm_pofw",
        "added_before_dedupe": len(gaps),
        "dedupe_removed": dropped,
        "matched": matched,
        "gaps_by": dict(gaps_by),
        "match_m": MATCH_M,
        "dedupe_m": DEDUPE_M,
    }
    report["after"] = dict(after)
    report["dedupe_removed"] = dropped
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    out = {"meta": meta, "k": religions, "p": places}
    raw = json.dumps(out, separators=(",", ":"))
    tmp = PLACES.with_suffix(".json.tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(PLACES)
    with gzip.open(PLACES_GZ, "wt", encoding="utf-8", compresslevel=6) as gz:
        gz.write(raw)
    SUMMARY.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print("after:", dict(after), flush=True)
    print("assigning counties…", flush=True)
    return subprocess.call([sys.executable, str(ROOT / "scripts" / "assign-counties.py")])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as err:
        print("reconcile-osm-worship failed: %s" % err, file=sys.stderr)
        raise
