#!/usr/bin/env python3
"""Draw a seeded stratified sample of the OSM scorecard and reverse-geocode it.

Does not write places.json. Output: data/raw/osm-validate-package/scorecard/sample/
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util

spec = importlib.util.spec_from_file_location(
    "scorecard", ROOT / "scripts" / "build-osm-validate-scorecard.py"
)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

SEED = 20260920
UA = "faithmap-app/1.0 (scorecard sample; markmaga.com)"
OUT = sc.OUT / "sample"
MUSLIM = 2

QUOTAS = {
    "rural_expected": 40,
    "on_building": 10,
    "hifld_dupe": 15,
    "local_snap": 20,  # 10 farthest + 10 random from rest
    "impossible": 15,
    "far_unique_review": 10,  # farthest
}


def read_tsv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def key_row(name, st, lat, lon) -> tuple:
    return (name, st, round(float(lat), 5), round(float(lon), 5))


def pick_spread(rows: list, n: int, rng: random.Random, state_key="state") -> list:
    if len(rows) <= n:
        return list(rows)
    by = defaultdict(list)
    for r in rows:
        by[r.get(state_key) or r.get("state") or "?"].append(r)
    states = sorted(by)
    rng.shuffle(states)
    for st in states:
        rng.shuffle(by[st])
    out = []
    i = 0
    while len(out) < n:
        st = states[i % len(states)]
        bucket = by[st]
        if bucket:
            out.append(bucket.pop())
        i += 1
        if i > n * 50:
            break
    return out


def nominatim_reverse(lat: float, lon: float) -> dict:
    q = urllib.parse.urlencode(
        {
            "lat": "%.6f" % lat,
            "lon": "%.6f" % lon,
            "format": "jsonv2",
            "zoom": 18,
            "addressdetails": 1,
        }
    )
    url = "https://nominatim.openstreetmap.org/reverse?" + q
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def nominatim_search(name: str, lat: float, lon: float) -> list:
    q = urllib.parse.urlencode(
        {
            "q": name,
            "format": "jsonv2",
            "limit": 3,
            "viewbox": "%s,%s,%s,%s"
            % (lon - 0.15, lat + 0.15, lon + 0.15, lat - 0.15),
            "bounded": 1,
        }
    )
    url = "https://nominatim.openstreetmap.org/search?" + q
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def hav(a, o, a2, o2):
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(a2)
    dphi = math.radians(a2 - a)
    dl = math.radians(o2 - o)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


WORSHIP = (
    "church",
    "chapel",
    "cathedral",
    "mosque",
    "synagogue",
    "temple",
    "kingdom hall",
    "parish",
    "shrine",
    "place_of_worship",
)


def judge(pin_name: str, bucket: str, rev: dict, search: list, extra: dict) -> tuple[str, str]:
    """Return (verdict, note). verdict: real|duplicate|wrong_place|waterfront|unsure"""
    disp = (rev.get("display_name") or "").lower()
    cls = (rev.get("class") or "") + ":" + (rev.get("type") or "")
    cat = (rev.get("category") or "") + ":" + (rev.get("type") or "")
    named = (rev.get("name") or "")
    tok_pin = sc.tokens(pin_name)
    tok_rev = sc.tokens(named)

    waterish = any(
        w in disp or w in cls or w in cat
        for w in ("water", "lake", "reservoir", "wetland", "ocean", "bay", "strait", "coast")
    )
    worshipish = any(w in disp or w in cls or w in cat for w in WORSHIP)
    name_hit = bool(tok_pin and tok_rev and len(tok_pin & tok_rev) >= min(2, len(tok_pin)))

    search_hit = False
    search_m = None
    plat = extra.get("lat")
    plon = extra.get("lon")
    for s in search or []:
        try:
            slat, slon = float(s["lat"]), float(s["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        d = hav(plat, plon, slat, slon)
        stok = sc.tokens(s.get("display_name") or s.get("name") or "")
        if d <= 250 and (len(tok_pin & stok) >= 1 or any(w in (s.get("type") or "") for w in ("place_of_worship", "church"))):
            search_hit = True
            search_m = round(d)
            break
        if d <= 80 and len(tok_pin & stok) >= 2:
            search_hit = True
            search_m = round(d)
            break

    if bucket == "hifld_dupe":
        if search_hit or name_hit or worshipish:
            return "duplicate", "same-church neighborhood; OSM+HIFLD pair looks like one congregation"
        return "unsure", "dupe radius but reverse did not confirm a worship name"

    if bucket == "impossible":
        if worshipish or name_hit or search_hit:
            return "waterfront", "church on/near water; NE 10m clipped the coast/lake"
        if waterish and not worshipish:
            return "wrong_place", "reverse is water/coast with no worship name"
        return "unsure", "flagged off-land/in-lake; reverse=%s" % (named or cls)

    if bucket == "local_snap":
        new_lat, new_lon = extra.get("new_lat"), extra.get("new_lon")
        # old pin
        if search_hit and search_m is not None and search_m <= 250:
            return "real", "name already at the current pin (≤250m) — snap would move a real church"
        return "unsure", "need dest check; reverse@old=%s" % (named or cls)

    if bucket == "far_unique_review":
        if search_hit and search_m is not None and search_m <= 400:
            return "real", "current pin matches a nearby same-name hit — far dest is a different town"
        return "unsure", "likely two churches, one name; reverse@old=%s" % (named or cls)

    # on_building / rural
    if name_hit or worshipish or search_hit:
        return "real", (named or cls) + ((" search %sm" % search_m) if search_m is not None else "")
    if waterish:
        return "wrong_place", "no worship name; reverse looks like water/landcover"
    if (rev.get("type") or "") in ("yes", "residential", "farmland", "forest", "meadow", "grass"):
        return "unsure", "generic landcover reverse: %s" % disp[:80]
    return "unsure", "reverse=%s %s" % (named or "(unnamed)", cls)


def main() -> int:
    rng = random.Random(SEED)
    payload = json.loads(sc.PLACES.read_text(encoding="utf-8"))
    rows = payload["p"]
    print("loading POW areas for remainder split…", flush=True)
    near, _tok, near_grid = sc.load_areas()

    marked = set()
    hifld = read_tsv(sc.OUT / "hifld-dupes.tsv")
    local = read_tsv(sc.OUT / "local-snaps.tsv")
    impossible = read_tsv(sc.OUT / "impossible.tsv")
    faru = read_tsv(sc.OUT / "far-unique-review.tsv")
    blocked = read_tsv(sc.OUT / "local-snaps-blocked.tsv")

    for r in hifld:
        marked.add(key_row(r["osm_name"], r["state"], r["osm_lat"], r["osm_lon"]))
    for r in local:
        marked.add(key_row(r["from_name"], r["state"], r["old_lat"], r["old_lon"]))
    for r in impossible:
        marked.add(key_row(r["name"], r["state"], r["lat"], r["lon"]))

    remainder = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            continue
        if int(row[1]) == MUSLIM:
            continue
        if (row[6] or "").strip():
            continue
        k = key_row(row[0], row[4], row[2], row[3])
        if k in marked:
            continue
        remainder.append(row)

    rural = []
    onb = []
    print("splitting remainder %d…" % len(remainder), flush=True)
    for i, row in enumerate(remainder):
        d, bname = sc.nearest_area(float(row[2]), float(row[3]), near, near_grid, 300)
        rec = {
            "name": row[0],
            "state": row[4],
            "lat": float(row[2]),
            "lon": float(row[3]),
            "county": row[5] or "",
            "nearest_m": round(d) if d < 1e17 else None,
            "nearest": bname,
        }
        if d <= 250:
            onb.append(rec)
        else:
            rural.append(rec)
        if i and i % 40000 == 0:
            print("  %d" % i, flush=True)
    print("remainder on_building %d rural %d" % (len(onb), len(rural)), flush=True)

    sample = []

    def add(bucket, rec, **extra):
        row = {"bucket": bucket, **rec, **extra}
        sample.append(row)

    for rec in pick_spread(rural, QUOTAS["rural_expected"], rng):
        add("rural_expected", rec)
    for rec in pick_spread(onb, QUOTAS["on_building"], rng):
        add("on_building", rec)
    for rec in pick_spread(hifld, QUOTAS["hifld_dupe"], rng):
        add(
            "hifld_dupe",
            {
                "name": rec["osm_name"],
                "state": rec["state"],
                "lat": float(rec["osm_lat"]),
                "lon": float(rec["osm_lon"]),
                "hifld_name": rec["hifld_name"],
                "meters": rec["meters"],
            },
        )

    local_sorted = sorted(local, key=lambda r: -float(r["move_km"]))
    farthest_l = local_sorted[:10]
    rest_l = local_sorted[10:]
    rng.shuffle(rest_l)
    for rec in farthest_l + rest_l[:10]:
        add(
            "local_snap",
            {
                "name": rec["from_name"],
                "state": rec["state"],
                "lat": float(rec["old_lat"]),
                "lon": float(rec["old_lon"]),
                "to_name": rec["to_name"],
                "new_lat": float(rec["new_lat"]),
                "new_lon": float(rec["new_lon"]),
                "move_km": rec["move_km"],
                "pick": "farthest" if rec in farthest_l else "random",
            },
        )

    in_lake = [r for r in impossible if r["why"] == "in-lake"]
    off = [r for r in impossible if r["why"] != "in-lake"]
    rng.shuffle(in_lake)
    rng.shuffle(off)
    for rec in in_lake[:8] + off[:7]:
        add(
            "impossible",
            {
                "name": rec["name"],
                "state": rec["state"],
                "lat": float(rec["lat"]),
                "lon": float(rec["lon"]),
                "why": rec["why"],
            },
        )

    far_sorted = sorted(faru, key=lambda r: -float(r["move_km"]))[:10]
    for rec in far_sorted:
        add(
            "far_unique_review",
            {
                "name": rec["from_name"],
                "state": rec["state"],
                "lat": float(rec["old_lat"]),
                "lon": float(rec["old_lon"]),
                "to_name": rec["to_name"],
                "new_lat": float(rec["new_lat"]),
                "new_lon": float(rec["new_lon"]),
                "move_km": rec["move_km"],
            },
        )

    print("sample n", len(sample), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)

    results = []
    for i, rec in enumerate(sample, 1):
        lat, lon = float(rec["lat"]), float(rec["lon"])
        print("[%d/%d] %s %s %s" % (i, len(sample), rec["bucket"], rec["state"], rec["name"][:40]), flush=True)
        rev = {}
        search = []
        err = ""
        try:
            rev = nominatim_reverse(lat, lon)
        except Exception as ex:
            err = "reverse: %s" % ex
        time.sleep(1.05)
        try:
            search = nominatim_search(rec["name"], lat, lon)
        except Exception as ex:
            err = (err + " search: %s" % ex).strip()
        time.sleep(1.05)
        verdict, note = judge(rec["name"], rec["bucket"], rev, search, rec)
        if err:
            verdict, note = "unsure", err
        rec_out = {
            **rec,
            "verdict": verdict,
            "note": note,
            "osm_reverse": (rev.get("name") or "")[:80],
            "osm_class": "%s:%s" % (rev.get("category") or rev.get("class") or "", rev.get("type") or ""),
            "osm_display": (rev.get("display_name") or "")[:140],
        }
        results.append(rec_out)

    fields = [
        "bucket",
        "pick",
        "name",
        "state",
        "lat",
        "lon",
        "verdict",
        "note",
        "osm_reverse",
        "osm_class",
        "osm_display",
        "to_name",
        "new_lat",
        "new_lon",
        "move_km",
        "hifld_name",
        "meters",
        "why",
        "county",
        "nearest_m",
        "nearest",
    ]
    with (OUT / "sample.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)
    (OUT / "sample.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    from collections import Counter

    by_b = defaultdict(Counter)
    for r in results:
        by_b[r["bucket"]][r["verdict"]] += 1
    summary = {
        "seed": SEED,
        "n": len(results),
        "by_bucket": {k: dict(v) for k, v in by_b.items()},
        "places_sha": hashlib.sha256(sc.PLACES.read_bytes()).hexdigest()[:12],
    }
    (OUT / "sample-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
