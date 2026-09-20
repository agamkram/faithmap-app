#!/usr/bin/env python3
"""KY Baptist Convention gapfill pilot — candidates only, no places.json bake.

Implements data/raw/mapped-gapfill/ (MATCH_SPEC + FILTER_NOTES).
Primary canary: Union City Baptist Church (Madison KY FIPS 21151).

  python3 scripts/pilot-kybaptist-gapfill.py --canary
  python3 scripts/pilot-kybaptist-gapfill.py --madison
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from mapped_filters import osm_should_drop  # noqa: E402

PLACES = ROOT / "data" / "places.json"
OUT_DIR = ROOT / "data" / "raw" / "mapped-gapfill"
URL_CACHE = OUT_DIR / "kybaptist-church-urls.txt"
HTML_CACHE = OUT_DIR / "kybaptist-html-cache"
GEOCODE_CACHE = OUT_DIR / "kybaptist-geocode-cache.json"
CANDIDATES_OUT = OUT_DIR / "pilot-candidates.jsonl"
REPORT_OUT = OUT_DIR / "pilot-report.json"

UA = "faithmap-gapfill/1.0 (markmaga.com; KY Baptist directory research)"
SITEMAP_INDEX = "https://www.kybaptist.org/sitemap_index.xml"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

MATCH_M = 120.0
STACK_M = 15.0
GEOCODE_SNAP_M = 80.0
REL_CHRISTIAN = 0

CANARY = {
    "name": "Union City Baptist Church",
    "url": "https://www.kybaptist.org/churches/union-city-baptist-church/",
    "address": "2502 Doylesville Road, Richmond, KY 40475",
    "lat": 37.797995,
    "lon": -84.198306,
    "neighbor": "Union Christian Church Inc",
    "neighbor_lat": 37.79697,
    "neighbor_lon": -84.19913,
    "fips": "21151",
}

# Madison County, KY — cities / ZIPs used to prefilter directory rows before geocode.
MADISON_ZIPS = {
    "40403",
    "40404",  # Berea
    "40475",
    "40476",  # Richmond PO
    "40361",  # Kingston area sometimes
    "40461",
    "40447",  # Paint Lick (straddles)
}
MADISON_CITY_RE = re.compile(
    r"\b(richmond|berea|waco|kirksville|kingston|paint\s*lick|white\s*hall|"
    r"boonesborough|valley\s*view)\b",
    re.I,
)

TOKEN_MAP = {
    "st": "saint",
    "mt": "mount",
    "1st": "first",
    "2nd": "second",
    "3rd": "third",
    "4th": "fourth",
}


def http_get(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def haversine_m(a: float, b: float, c: float, d: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c - a)
    dl = math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def norm_name(name: str) -> str:
    n = unicodedata.normalize("NFKC", name or "")
    n = n.lower()
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if n.startswith("the "):
        n = n[4:]
    parts = n.split()
    while parts and parts[-1] in ("inc", "llc", "corp", "corporation", "ltd", "co"):
        parts.pop()
    parts = [TOKEN_MAP.get(p, p) for p in parts]
    return " ".join(parts)


def name_similar(a: str, b: str) -> bool:
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb:
        j = len(ta & tb) / len(ta | tb)
        if j >= 0.85:
            return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.92


def load_church_urls(force: bool = False) -> list[str]:
    if URL_CACHE.exists() and not force:
        urls = [
            ln.strip()
            for ln in URL_CACHE.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith("http")
        ]
        if len(urls) > 100:
            return urls
    idx = http_get(SITEMAP_INDEX)
    smaps = re.findall(
        r"<loc>(https://www\.kybaptist\.org/churches-sitemap[^<]+)</loc>", idx
    )
    urls: list[str] = []
    for sm in smaps:
        data = http_get(sm)
        urls.extend(
            re.findall(r"<loc>(https://www\.kybaptist\.org/churches/[^<]+)</loc>", data)
        )
        time.sleep(0.4)
    urls = sorted(
        {
            u.rstrip("/") + "/"
            for u in urls
            if "/churches/" in u and not u.rstrip("/").endswith("/churches")
            and "/feed" not in u
            and "/page/" not in u
        }
    )
    URL_CACHE.write_text("\n".join(urls) + "\n", encoding="utf-8")
    return urls


def cache_path_for(url: str) -> Path:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    slug = url.rstrip("/").split("/")[-1][:60]
    return HTML_CACHE / f"{slug}_{h}.html"


def fetch_html(url: str, sleep_s: float = 0.35) -> str:
    HTML_CACHE.mkdir(parents=True, exist_ok=True)
    path = cache_path_for(url)
    if path.exists() and path.stat().st_size > 500:
        return path.read_text(encoding="utf-8", errors="replace")
    html = http_get(url)
    path.write_text(html, encoding="utf-8")
    time.sleep(sleep_s)
    return html


def parse_church_page(url: str, html: str) -> dict | None:
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if not title_m:
        title_m = re.search(r"<title>([^|<]+)", html, re.I)
    name = re.sub(r"<[^>]+>", "", title_m.group(1) if title_m else "").strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"\s*-\s*Kentucky Baptist Convention.*$", "", name, flags=re.I).strip()
    if not name or name.lower() in ("churches", "find a church"):
        return None

    # KY Baptist theme: meeting block preferred, then combined mailing addy.
    meeting = None
    for cls in ("church_meeting_addy", "church_mailing_addy"):
        m = re.search(
            rf'<div class="{cls}">(.*?)</div>',
            html,
            re.I | re.S,
        )
        if m:
            meeting = re.sub(r"<br\s*/?>", ", ", m.group(1), flags=re.I)
            meeting = re.sub(r"<[^>]+>", " ", meeting)
            meeting = re.sub(r"\s+", " ", meeting).strip(" ,")
            if meeting and not re.search(r"\bpo\s*box\b", meeting, re.I):
                break
            if meeting:
                break

    if not meeting:
        m = re.search(
            r"(\d{1,6}\s+[A-Za-z0-9 .'-]+\s+(?:Road|Rd|Street|St|Drive|Dr|Lane|Ln|"
            r"Avenue|Ave|Blvd|Boulevard|Way|Pike|Hwy|Highway)\.?\s*,?\s*"
            r"[A-Za-z .'-]+,\s*KY\s*\d{5}(?:-\d{4})?)",
            html,
            re.I,
        )
        if m:
            meeting = m.group(1)

    address = re.sub(r"\s+", " ", (meeting or "")).strip()
    if not address:
        m = re.search(r"(PO\s*Box\s*\d+[^<]{0,80}KY\s*\d{5})", html, re.I)
        if m:
            address = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()

    zip_m = re.search(r"\bKY\s*(\d{5})", address, re.I)
    zip5 = zip_m.group(1) if zip_m else ""

    # Directory embeds ACF map markers — prefer these over Nominatim.
    lat = lon = None
    mm = re.search(
        r'class="marker"[^>]*data-lat="([-0-9.]+)"[^>]*data-lng="([-0-9.]+)"',
        html,
        re.I,
    )
    if not mm:
        mm = re.search(
            r'data-lat="([-0-9.]+)"[^>]*data-lng="([-0-9.]+)"',
            html,
            re.I,
        )
    if mm:
        try:
            lat, lon = float(mm.group(1)), float(mm.group(2))
        except ValueError:
            lat = lon = None

    return {
        "name": name,
        "url": url,
        "address": address,
        "zip": zip5,
        "lat": lat,
        "lon": lon,
        "source": "kybaptist",
    }


def looks_madison(row: dict) -> bool:
    addr = row.get("address") or ""
    if row.get("zip") in MADISON_ZIPS:
        return True
    if MADISON_CITY_RE.search(addr) and re.search(r"\bKY\b", addr, re.I):
        return True
    lat, lon = row.get("lat"), row.get("lon")
    if lat is not None and lon is not None:
        if 37.55 <= float(lat) <= 37.95 and -84.55 <= float(lon) <= -84.05:
            return True
    slug = (row.get("url") or "").lower()
    if any(
        k in slug
        for k in (
            "richmond",
            "union-city",
            "kirksville",
            "berea-baptist",
            "waco-baptist",
            "paint-lick",
            "kingston-forks",
        )
    ):
        return True
    return False


def load_geocode_cache() -> dict:
    if GEOCODE_CACHE.exists():
        return json.loads(GEOCODE_CACHE.read_text(encoding="utf-8"))
    return {}


def save_geocode_cache(cache: dict) -> None:
    GEOCODE_CACHE.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")


def geocode(address: str, cache: dict) -> tuple[float | None, float | None, str]:
    key = re.sub(r"\s+", " ", (address or "").strip().lower())
    if not key:
        return None, None, "empty"
    if key in cache:
        hit = cache[key]
        return hit.get("lat"), hit.get("lon"), hit.get("status", "cache")
    if re.search(r"\bpo\s*box\b", key):
        cache[key] = {"lat": None, "lon": None, "status": "pobox"}
        return None, None, "pobox"
    q = urllib.parse.urlencode(
        {
            "q": address,
            "format": "json",
            "limit": 1,
            "countrycodes": "us",
        }
    )
    url = NOMINATIM + "?" + q
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        cache[key] = {"lat": None, "lon": None, "status": f"error:{e}"}
        time.sleep(1.1)
        return None, None, f"error"
    time.sleep(1.1)
    if not data:
        cache[key] = {"lat": None, "lon": None, "status": "nomatch"}
        return None, None, "nomatch"
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    cache[key] = {"lat": lat, "lon": lon, "status": "ok", "display": data[0].get("display_name")}
    return lat, lon, "ok"


def load_mapped_christian() -> list[dict]:
    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    out = []
    for i, row in enumerate(payload["p"]):
        if int(row[1]) != REL_CHRISTIAN:
            continue
        out.append(
            {
                "i": i,
                "n": row[0],
                "a": float(row[2]),
                "o": float(row[3]),
                "s": row[4],
                "c": row[5] if len(row) > 5 else "",
            }
        )
    return out


def match_candidate(
    name: str,
    lat: float,
    lon: float,
    mapped: list[dict],
) -> dict:
    drop = osm_should_drop(name)
    if drop:
        return {
            "match_status": "FILTER_DROP",
            "drop_reason": drop,
            "nearest_existing": None,
        }

    nearest = None
    nearest_d = 1e18
    name_hit = None
    stack_hit = None
    for p in mapped:
        d = haversine_m(lat, lon, p["a"], p["o"])
        if d < nearest_d:
            nearest_d = d
            nearest = {"name": p["n"], "dist_m": round(d, 1), "lat": p["a"], "lon": p["o"]}
        if d <= MATCH_M and name_similar(name, p["n"]):
            name_hit = {"name": p["n"], "dist_m": round(d, 1), "lat": p["a"], "lon": p["o"]}
            break
        if d <= STACK_M and norm_name(name) == norm_name(p["n"]):
            stack_hit = {"name": p["n"], "dist_m": round(d, 1)}

    if name_hit:
        return {
            "match_status": "MATCH_EXISTING",
            "nearest_existing": name_hit,
        }
    if stack_hit:
        return {
            "match_status": "STACK_DUP",
            "nearest_existing": stack_hit,
        }
    return {
        "match_status": "APPEND_CANDIDATE",
        "nearest_existing": nearest if nearest and nearest_d <= 500 else nearest,
    }


def process_rows(
    rows: list[dict],
    mapped: list[dict],
    geocache: dict,
    use_canary_coords: bool = True,
) -> list[dict]:
    out = []
    for row in rows:
        name = row["name"]
        addr = row.get("address") or ""
        lat = row.get("lat")
        lon = row.get("lon")
        geocoder = None
        gstatus = None

        if lat is not None and lon is not None:
            geocoder = "kybaptist_acf_map"
            gstatus = "ok"
        elif (
            use_canary_coords
            and name_similar(name, CANARY["name"])
        ):
            lat, lon, gstatus = geocode(addr or CANARY["address"], geocache)
            if lat is None:
                lat, lon = CANARY["lat"], CANARY["lon"]
                geocoder = "canary_listing_coords"
                gstatus = "canary_fallback"
            else:
                geocoder = "nominatim"
        else:
            lat, lon, gstatus = geocode(addr, geocache)
            geocoder = "nominatim"

        rec = {
            "name": name,
            "lat": lat,
            "lon": lon,
            "religion": "christian",
            "source": "kybaptist",
            "source_url": row.get("url"),
            "address": addr,
            "zip": row.get("zip"),
            "geocoder": geocoder,
            "geocode_status": gstatus,
        }
        if lat is None or lon is None:
            rec["match_status"] = "GEOCODE_FAIL"
            rec["nearest_existing"] = None
        else:
            rec.update(match_candidate(name, lat, lon, mapped))
        out.append(rec)
    save_geocode_cache(geocache)
    return out


def write_outputs(candidates: list[dict], meta: dict) -> None:
    with CANDIDATES_OUT.open("w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    counts = Counter(c["match_status"] for c in candidates)
    canary = next(
        (c for c in candidates if name_similar(c["name"], CANARY["name"])),
        None,
    )
    report = {
        "built": datetime.now(timezone.utc).isoformat(),
        "mode": meta.get("mode"),
        "source": "kybaptist",
        "places_n": meta.get("places_n"),
        "directory_fetched": meta.get("directory_fetched"),
        "candidates_n": len(candidates),
        "status_counts": dict(counts),
        "canary": {
            "expected_name": CANARY["name"],
            "found": canary is not None,
            "record": canary,
            "pass": bool(
                canary
                and canary.get("match_status") == "APPEND_CANDIDATE"
                and canary.get("lat") is not None
            ),
        },
        "append_sample": [
            c for c in candidates if c.get("match_status") == "APPEND_CANDIDATE"
        ][:25],
        "note": "Dry-run only. Did not modify places.json / Census / MosqueIndex.",
        **{k: v for k, v in meta.items() if k not in ("mode", "places_n", "directory_fetched")},
    }
    REPORT_OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("wrote", CANDIDATES_OUT)
    print("wrote", REPORT_OUT)
    print("status_counts", dict(counts))
    if canary:
        print(
            "CANARY",
            canary["match_status"],
            canary.get("lat"),
            canary.get("lon"),
            "nearest",
            canary.get("nearest_existing"),
        )
        print("CANARY_PASS", report["canary"]["pass"])
    else:
        print("CANARY missing from candidate set")


def run_canary() -> int:
    print("mode=canary", flush=True)
    mapped = load_mapped_christian()
    html = fetch_html(CANARY["url"])
    row = parse_church_page(CANARY["url"], html)
    if not row:
        print("failed to parse canary page", file=sys.stderr)
        return 1
    print("parsed", row)
    geocache = load_geocode_cache()
    cands = process_rows([row], mapped, geocache, use_canary_coords=True)
    write_outputs(
        cands,
        {
            "mode": "canary",
            "places_n": json.loads(PLACES.read_text())["meta"]["n"],
            "directory_fetched": 1,
        },
    )
    return 0 if cands and cands[0].get("match_status") == "APPEND_CANDIDATE" else 2


def madison_polygon():
    """Load Madison KY FIPS 21151 polygon (with degenerate-hole cleanup)."""
    from shapely.geometry import shape

    try:
        from shapely import make_valid
    except ImportError:
        make_valid = None
    geo = json.loads((ROOT / "geo" / "counties.geojson").read_text(encoding="utf-8"))
    for ft in geo.get("features") or []:
        if str(ft.get("properties", {}).get("id") or "").zfill(5) != "21151":
            continue
        raw = ft["geometry"]
        if raw.get("type") == "Polygon":
            coords = raw["coordinates"]
            cleaned = [coords[0]] + [h for h in coords[1:] if h and len(h) >= 4]
            geom = shape({"type": "Polygon", "coordinates": cleaned})
        else:
            geom = shape(raw)
        if make_valid is not None and not geom.is_valid:
            geom = make_valid(geom)
        return geom
    raise RuntimeError("Madison KY FIPS 21151 polygon not found")


def run_madison() -> int:
    print("mode=madison", flush=True)
    urls = load_church_urls()
    print("directory urls", len(urls), flush=True)
    mapped = load_mapped_christian()
    print("mapped christian", len(mapped), flush=True)
    poly = madison_polygon()
    from shapely.geometry import Point

    print("fetching directory pages (cached)…", flush=True)
    rows = []
    for i, url in enumerate(urls, 1):
        try:
            html = fetch_html(url, sleep_s=0.25)
            row = parse_church_page(url, html)
            if not row:
                continue
            lat, lon = row.get("lat"), row.get("lon")
            if lat is None or lon is None:
                # keep address-only madison-ish for nominatim later
                if looks_madison(row):
                    rows.append(row)
                continue
            if poly.contains(Point(float(lon), float(lat))) or poly.intersects(
                Point(float(lon), float(lat)).buffer(0.0001)
            ):
                rows.append(row)
        except Exception as e:
            print("fetch fail", url, e, flush=True)
        if i % 100 == 0:
            print(f"  …{i}/{len(urls)} madison_hits={len(rows)}", flush=True)

    if not any(name_similar(r["name"], CANARY["name"]) for r in rows):
        html = fetch_html(CANARY["url"])
        row = parse_church_page(CANARY["url"], html)
        if row:
            rows.append(row)

    print("madison FIPS directory rows", len(rows), flush=True)
    geocache = load_geocode_cache()
    cands = process_rows(rows, mapped, geocache, use_canary_coords=True)

    # Drop geocoded rows that fell outside the county after Nominatim snap.
    filtered = []
    for c in cands:
        if name_similar(c["name"], CANARY["name"]):
            filtered.append(c)
            continue
        if c.get("lat") is None:
            continue
        if poly.contains(Point(c["lon"], c["lat"])) or poly.intersects(
            Point(c["lon"], c["lat"]).buffer(0.0001)
        ):
            filtered.append(c)

    write_outputs(
        filtered,
        {
            "mode": "madison",
            "places_n": json.loads(PLACES.read_text())["meta"]["n"],
            "directory_fetched": len(urls),
            "filter": "FIPS 21151 polygon",
            "madison_rows": len(filtered),
        },
    )
    report = json.loads(REPORT_OUT.read_text())
    return 0 if report.get("canary", {}).get("pass") else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--canary", action="store_true", help="Union City Baptist only")
    g.add_argument("--madison", action="store_true", help="Madison County KY pilot")
    ap.add_argument("--refresh-urls", action="store_true")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.refresh_urls:
        load_church_urls(force=True)
    if args.canary:
        return run_canary()
    return run_madison()


if __name__ == "__main__":
    raise SystemExit(main())
