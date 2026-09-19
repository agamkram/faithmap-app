#!/usr/bin/env python3
"""Pull HIFLD All Places of Worship and bake a compact JSON for the app.

Source is IRS 501(c)(3) mailing addresses geocoded with HERE — mapped
buildings, not a census of congregations. Religion is NTEE when present,
then name patterns. Non-Christian names win so "Temple Beth El" is Jewish.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "places.json"
SUMMARY = DATA / "summary.json"

LAYER = (
    "https://services.arcgis.com/XG15cJAlne2vxtgt/ArcGIS/rest/services/"
    "All_Places_Of_Worship__HiFLD_Open_/FeatureServer/42/query"
)
PAGE = 2000
UA = "worship-app/1.0 (local build; markmaga.com)"

RELIGIONS = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
REL_INDEX = {k: i for i, k in enumerate(RELIGIONS)}

NTEE = {
    "X20": "christian",
    "X21": "christian",
    "X22": "christian",
    "X24": "christian",
    "X30": "jewish",
    "X40": "muslim",
    "X50": "buddhist",
    "X70": "hindu",
}

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
}

SKIP_RE = re.compile(
    r"\b(SCHOOL|ACADEMY|COLLEGE|UNIVERSITY|SEMINARY|ELEMENTARY|"
    r"HIGH SCHOOL|KINDERGARTEN|DAYCARE|DAY CARE|CAMPGROUND|"
    r"BIBLE SOCIETY)\b"
)
MUSLIM_RE = re.compile(
    r"\b(MOSQUE|MASJID|MASJED|MUSALLA|MUSALLAH|ISLAMIC|MUSLIM|JAMIA|"
    r"JAMAAT|ISLAM)\b"
)
JEWISH_RE = re.compile(
    r"\b(SYNAGOGUE|SYNAGOG|SHUL|CHABAD|JEWISH|HEBREW CONGREGATION|"
    r"TEMPLE BETH|CONGREGATION BETH|KEHILLA|KEHILAT|YOUNG ISRAEL|"
    r"CHAVURAH|B'NAI|BNAI|HADASSAH|SEPHARDIC|ASHKENAZI|"
    r"TEMPLE SINAI|TEMPLE ISRAEL|TEMPLE EMANU|TEMPLE SHALOM|"
    r"TEMPLE ADATH|AHAVATH|ANSHE|SHAAREY|SHAARE|TPHILOH|TEFILAH|"
    r"TORAH|MIKVAH|MIKVEH)\b"
)
SIKH_RE = re.compile(r"\b(GURDWARA|GURUDWARA|SIKH)\b")
HINDU_RE = re.compile(
    r"\b(HINDU|MANDIR|SWAMINARAYAN|ISKCON|VENKATESWARA|BALAJI|"
    r"SANATAN|JAIN TEMPLE|HARE KRISHNA)\b"
)
BUDDHIST_RE = re.compile(
    r"\b(BUDDHIST|BUDDHA|ZEN CENTER|ZEN TEMPLE|SOKA GAKKAI|"
    r"WAT THAI|WAT BUDDHA|DHARMA CENTER|DHARMA HALL|WAT |WATT SAMAKI)\b"
)
CHRISTIAN_RE = re.compile(
    r"\b(CHURCH|CHAPEL|CATHEDRAL|PARISH|BAPTIST|METHODIST|LUTHERAN|"
    r"PRESBYTERIAN|EPISCOPAL|CATHOLIC|PENTECOSTAL|ADVENTIST|ORTHODOX|"
    r"NAZARENE|WESLEYAN|MENNONITE|QUAKER|JEHOVAH|LATTER.?DAY|"
    r"ASSEMBLY OF GOD|ASSEMBLIES OF GOD|CHURCH OF CHRIST|CHURCH OF GOD|"
    r"MINISTRIES|MINISTRY|TABERNACLE|FELLOWSHIP|COVENANT|EVANGELICAL|"
    r"ANGLICAN|REFORMED|HOLINESS|APOSTOLIC|DISCIPLES OF CHRIST|"
    r"UNITARIAN|FRIENDS MEETING|KINGDOM HALL|LDS|CHRISTIAN|"
    r"GOSPEL|DIOCESE|ARCHDIOCESE|SDA|SEVENTH.?DAY|ADVENT|"
    r"CALVARY|MESSIAH|CONGREGATIONAL|FULL GOSPEL|"
    r"HOUSE OF PRAYER|HOUSE OF GOD|BIBLE CHURCH|BIBLE CHAPEL|"
    r"FAITH CENTER|FAITH COMMUNITY)\b"
)

SMALL = {"of", "the", "and", "or", "de", "la", "el", "da", "du", "van", "st"}


def title_name(raw: str) -> str:
    parts = []
    for i, w in enumerate((raw or "").split()):
        low = w.lower()
        if i and low in SMALL:
            parts.append(low)
        else:
            parts.append(w[:1].upper() + w[1:].lower() if w else w)
    return " ".join(parts).strip()


def classify(name: str, ntee: str) -> str | None:
    n = (name or "").upper()
    if SKIP_RE.search(n):
        return None
    if MUSLIM_RE.search(n):
        return "muslim"
    if SIKH_RE.search(n):
        return "sikh"
    if JEWISH_RE.search(n):
        return "jewish"
    if HINDU_RE.search(n):
        return "hindu"
    if BUDDHIST_RE.search(n):
        return "buddhist"
    code = (ntee or "").upper().strip()[:3]
    if code in NTEE:
        return NTEE[code]
    if CHRISTIAN_RE.search(n):
        return "christian"
    return None


def in_us(lat: float, lon: float, st: str) -> bool:
    if st not in US_STATES:
        return False
    if st == "HI":
        return 18.5 <= lat <= 22.5 and -161 <= lon <= -154
    if st == "AK":
        return 51 <= lat <= 72 and -180 <= lon <= -129
    return 24.3 <= lat <= 49.6 and -125.0 <= lon <= -66.4


def fetch(qs: dict) -> dict:
    url = LAYER + "?" + urllib.parse.urlencode(qs)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last_err = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                return json.loads(res.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            last_err = err
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("HIFLD query failed: %s" % last_err)


RAW = DATA / "raw" / "hifld.jsonl"


def pull_all() -> list[dict]:
    if RAW.exists() and RAW.stat().st_size > 1_000_000:
        rows = []
        with RAW.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        print("  cached raw %d" % len(rows), flush=True)
        return rows
    RAW.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    rows = []
    with RAW.open("w", encoding="utf-8") as fh:
        while True:
            payload = fetch(
                {
                    "where": "1=1",
                    "outFields": "NAME,STATE,CITY,NTEE_CD,SUBREGION,X,Y,SCORE,ADDR_TYPE",
                    "returnGeometry": "false",
                    "resultRecordCount": PAGE,
                    "resultOffset": offset,
                    "f": "json",
                }
            )
            feats = payload.get("features") or []
            if not feats:
                break
            for feat in feats:
                attr = feat.get("attributes") or {}
                rows.append(attr)
                fh.write(json.dumps(attr, separators=(",", ":")) + "\n")
            print("  fetched %d" % len(rows), flush=True)
            if not payload.get("exceededTransferLimit") and len(feats) < PAGE:
                break
            offset += len(feats)
            time.sleep(0.05)
    return rows


def bake(rows: list[dict]) -> tuple[list[dict], dict]:
    places = []
    by = {k: 0 for k in RELIGIONS}
    skipped = 0
    unclassified = 0
    samples = []
    for attr in rows:
        name = (attr.get("NAME") or "").strip()
        st = (attr.get("STATE") or "").strip().upper()
        try:
            lat = float(attr.get("Y"))
            lon = float(attr.get("X"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not in_us(lat, lon, st):
            skipped += 1
            continue
        rel = classify(name, attr.get("NTEE_CD") or "")
        if rel is None:
            unclassified += 1
            if len(samples) < 40:
                samples.append(name)
            continue
        city = title_name(attr.get("CITY") or "")
        county = title_name(attr.get("SUBREGION") or "")
        places.append(
            [
                title_name(name),
                REL_INDEX[rel],
                round(lat, 5),
                round(lon, 5),
                st,
                county,
                city,
            ]
        )
        by[rel] += 1
    meta = {
        "built": date.today().isoformat(),
        "source": "HIFLD All Places of Worship — IRS 501(c)(3) EO BMF, HERE geocode",
        "n": len(places),
        "by": by,
        "skipped": skipped,
        "unclassified": unclassified,
        "unclassified_sample": samples,
    }
    return places, meta


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    print("pulling HIFLD…", flush=True)
    rows = pull_all()
    print("raw rows %d" % len(rows), flush=True)
    places, meta = bake(rows)
    payload = {"meta": meta, "k": RELIGIONS, "p": places}
    raw_json = json.dumps(payload, separators=(",", ":"))
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(raw_json, encoding="utf-8")
    tmp.replace(OUT)
    gz_path = OUT.with_suffix(".json.gz")
    with gzip.open(gz_path, "wt", encoding="utf-8", compresslevel=6) as gz:
        gz.write(raw_json)
    SUMMARY.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("wrote %s (%d places)" % (OUT, len(places)), flush=True)
    for k in RELIGIONS:
        print("  %s %d" % (k, meta["by"][k]), flush=True)
    print("  unclassified %d  skipped %d" % (meta["unclassified"], meta["skipped"]))
    size = OUT.stat().st_size
    gz_size = gz_path.stat().st_size
    print("  file %.1f MB  gzip %.1f MB" % (size / 1e6, gz_size / 1e6), flush=True)
    print("assigning counties from polygons…", flush=True)
    import subprocess

    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "assign-counties.py")])
    if rc != 0:
        raise RuntimeError("assign-counties failed (%d)" % rc)

    # MosqueIndex muslim overlay (OSM + Google Maps places) — keeps Mapped
    # Muslim near the US Mosque Survey / Religion Census count.
    print("reconciling mosques via MosqueIndex…", flush=True)
    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "reconcile-mosques.py")])
    if rc != 0:
        raise RuntimeError("reconcile-mosques failed (%d)" % rc)

    # OSM places-of-worship gap-fill for all six religions (add-only).
    print("reconciling OSM worship gaps…", flush=True)
    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "reconcile-osm-worship.py")])
    if rc != 0:
        raise RuntimeError("reconcile-osm-worship failed (%d)" % rc)


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print("build-places failed: %s" % err, file=sys.stderr)
        sys.exit(1)
