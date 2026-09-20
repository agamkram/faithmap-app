#!/usr/bin/env python3
"""Rank counties where Mapped undercounts the 2020 Religion Census.

Joins on county FIPS (point-in-polygon), not name strings — independent cities
like Baltimore City vs Baltimore County share a display name in places.json.

Use this to hunt real missing houses of worship (HIFLD/OSM gaps), not to force
Mapped toward Census totals. Writes JSON + TSV under data/raw/.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "data" / "places.json"
COUNTIES = ROOT / "geo" / "counties.geojson"
XLSX = ROOT / "data" / "raw" / "2020_USRC_Group_Detail.xlsx"
OUT_JSON = ROOT / "data" / "raw" / "county-undercount-report.json"
OUT_TSV = ROOT / "data" / "raw" / "county-undercount.tsv"

RELIGIONS = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
REL_INDEX = {r: i for i, r in enumerate(RELIGIONS)}

LDS_NOTE_STATES = {"UT"}

STATE_ABBR = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "District of Columbia": "DC",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}


def classify(name: str) -> str | None:
    """Same grouping as scripts/build-census.py."""
    n = (name or "").upper()
    if not n or n == "TOTALS":
        return None
    if re.search(r"\bSIKH|GURDWARA\b", n):
        return "sikh"
    if re.search(r"\bISLAM|MUSLIM|MOSQUE\b", n):
        return "muslim"
    if re.search(r"\bHINDU|HINDUISM|VEDANTA|SWAMINARAYAN\b", n):
        return "hindu"
    if re.search(r"\bBUDDH|THERAVADA|MAHAYANA|VAJARAYANA|VAJRAYANA\b", n):
        return "buddhist"
    if re.search(
        r"\bJEWISH|JUDAISM|CHABAD|RECONSTRUCTIONIST JUDAISM|"
        r"REFORM JUDAISM|CONSERVATIVE JUDAISM|ORTHODOX JUDAISM|"
        r"INDEPENDENT JUDAISM\b",
        n,
    ):
        return "jewish"
    if re.search(
        r"\bBAHA.?I|JAIN\b|SHINTO|TAOISM|\bTAO\b|ZOROASTR|"
        r"ETHICAL UNION|SPIRITUALIST\b",
        n,
    ):
        return None
    return "christian"


def load_census_by_fips() -> tuple[dict[str, list[int]], dict[str, tuple[str, str]]]:
    try:
        import openpyxl
    except ImportError:
        print("pip install openpyxl", file=sys.stderr)
        raise SystemExit(1)
    if not XLSX.exists():
        print("missing", XLSX, file=sys.stderr)
        raise SystemExit(1)
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb["2020 Group by County"]
    by: dict[str, list[int]] = defaultdict(lambda: [0] * len(RELIGIONS))
    labels: dict[str, tuple[str, str]] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        fips, state_name, county_name, code, gname, cong = row[:6]
        if not gname or str(code).lower() == "totals":
            continue
        rel = classify(str(gname))
        if rel is None:
            continue
        n = int(cong or 0)
        if n <= 0 or fips is None:
            continue
        fid = str(int(fips)).zfill(5) if not isinstance(fips, str) else str(fips).zfill(5)
        by[fid][REL_INDEX[rel]] += n
        if fid not in labels:
            st = STATE_ABBR.get(str(state_name or ""), "")
            labels[fid] = (st, str(county_name or ""))
    wb.close()
    return by, labels


def assign_places_by_fips(
    rows: list,
) -> tuple[dict[str, list[int]], dict[str, tuple[str, str]], int]:
    try:
        from shapely.geometry import Point, shape
        from shapely.strtree import STRtree
    except ImportError:
        print("pip install shapely", file=sys.stderr)
        raise SystemExit(1)
    try:
        from shapely import make_valid
    except ImportError:
        make_valid = None

    def clean_polygon_coords(coords):
        """Drop degenerate rings (<4 positions). Some county holes are corrupt."""
        if not coords:
            return None
        outer = coords[0]
        if not outer or len(outer) < 4:
            return None
        holes = [h for h in coords[1:] if h and len(h) >= 4]
        return [outer] + holes

    def geom_from_feature(ft):
        raw = ft.get("geometry") or {}
        gtype = raw.get("type")
        coords = raw.get("coordinates")
        if gtype == "Polygon":
            cleaned = clean_polygon_coords(coords)
            if not cleaned:
                return None
            return shape({"type": "Polygon", "coordinates": cleaned})
        if gtype == "MultiPolygon":
            parts = []
            for poly in coords or []:
                cleaned = clean_polygon_coords(poly)
                if cleaned:
                    parts.append(cleaned)
            if not parts:
                return None
            return shape({"type": "MultiPolygon", "coordinates": parts})
        return shape(raw)

    geo = json.loads(COUNTIES.read_text(encoding="utf-8"))
    polys = []
    meta: list[tuple[str, str, str]] = []
    for ft in geo.get("features") or []:
        props = ft.get("properties") or {}
        fid = str(props.get("id") or "").zfill(5)
        st = (props.get("s") or "").strip().upper()
        cname = (props.get("c") or "").strip()
        if not fid or fid == "00000" or not st or not cname:
            continue
        try:
            geom = geom_from_feature(ft)
            if geom is None:
                continue
            if make_valid is not None and not geom.is_valid:
                geom = make_valid(geom)
            if geom.is_empty:
                continue
            geoms = []
            if geom.geom_type == "Polygon":
                geoms = [geom]
            elif geom.geom_type == "MultiPolygon":
                geoms = list(geom.geoms)
            elif hasattr(geom, "geoms"):
                for g in geom.geoms:
                    if g.geom_type == "Polygon":
                        geoms.append(g)
                    elif g.geom_type == "MultiPolygon":
                        geoms.extend(list(g.geoms))
            for g in geoms:
                if g.is_empty or g.area <= 0:
                    continue
                polys.append(g)
                meta.append((fid, st, cname))
        except Exception:
            continue

    tree = STRtree(polys)
    mapped: dict[str, list[int]] = defaultdict(lambda: [0] * len(RELIGIONS))
    fips_label: dict[str, tuple[str, str]] = {}
    missed = 0

    for r in rows:
        rel = int(r[1])
        if not (0 <= rel < len(RELIGIONS)):
            continue
        lat, lon = float(r[2]), float(r[3])
        pt = Point(lon, lat)
        hits = tree.query(pt)
        found = None
        for idx in hits:
            i = int(idx)
            poly = polys[i]
            if poly.contains(pt) or poly.touches(pt):
                found = meta[i]
                break
        if found is None:
            buf = pt.buffer(0.02)
            hits2 = tree.query(buf)
            best_i = None
            best_d = 1e9
            for idx in hits2:
                i = int(idx)
                d = polys[i].distance(pt)
                if d < best_d:
                    best_d = d
                    best_i = i
            if best_i is not None and best_d < 0.05:
                found = meta[best_i]
        if found is None:
            missed += 1
            continue
        fid, st, cname = found
        mapped[fid][rel] += 1
        fips_label[fid] = (st, cname)
    return mapped, fips_label, missed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rel",
        default="christian",
        choices=RELIGIONS + ["all"],
        help="Religion to rank (default christian)",
    )
    ap.add_argument("--top", type=int, default=40, help="Print top N undercounts")
    ap.add_argument("--min-gap", type=int, default=1, help="Min Census−Mapped gap")
    ap.add_argument(
        "--exclude-lds-note",
        action="store_true",
        help="Drop Utah christian rows tagged lds-wards-vs-buildings from printed top",
    )
    args = ap.parse_args()

    places = json.loads(PLACES.read_text(encoding="utf-8"))
    print("assigning places → FIPS…", flush=True)
    mapped, place_labels, missed = assign_places_by_fips(places["p"])
    print("loading USRC by FIPS…", flush=True)
    census_by, census_labels = load_census_by_fips()

    under: list[dict] = []
    over: list[dict] = []
    us_mapped = [0] * len(RELIGIONS)
    us_census = [0] * len(RELIGIONS)

    for fid in set(mapped) | set(census_by):
        m = mapped.get(fid, [0] * len(RELIGIONS))
        c = census_by.get(fid, [0] * len(RELIGIONS))
        for i in range(len(RELIGIONS)):
            us_mapped[i] += m[i]
            us_census[i] += c[i]

        if fid in census_labels:
            st, co = census_labels[fid]
        elif fid in place_labels:
            st, co = place_labels[fid]
        else:
            st, co = "?", "?"

        for i, rel in enumerate(RELIGIONS):
            gap = c[i] - m[i]
            note = ""
            if st in LDS_NOTE_STATES and rel == "christian" and gap > 50:
                note = "lds-wards-vs-buildings"
            row = {
                "fips": fid,
                "state": st,
                "county": co,
                "rel": rel,
                "mapped": m[i],
                "census": c[i],
                "gap": gap,
                "pct": round(100.0 * gap / c[i], 1) if c[i] else None,
                "note": note,
            }
            if gap > 0:
                under.append(row)
            elif gap < 0:
                over.append(row)

    under.sort(key=lambda x: (-x["gap"], x["fips"], x["rel"]))
    over.sort(key=lambda x: (x["gap"], x["fips"], x["rel"]))

    def filt(rows: list[dict]) -> list[dict]:
        out = rows
        if args.rel != "all":
            out = [r for r in out if r["rel"] == args.rel]
        if args.min_gap > 1:
            out = [r for r in out if abs(r["gap"]) >= args.min_gap]
        return out

    under_f = filt(under)
    over_f = filt(over)
    print_rows = under_f
    if args.exclude_lds_note:
        print_rows = [r for r in under_f if r["note"] != "lds-wards-vs-buildings"]

    report = {
        "built": date.today().isoformat(),
        "note": (
            "Positive gap = Census − Mapped by FIPS. Hunt directories/OSM/Places "
            "in undercount counties; do not force Mapped to Census. Utah gaps are "
            "mostly LDS ward counts vs building pins."
        ),
        "join": "county FIPS via geo/counties.geojson + USRC Group Detail",
        "places_n": places["meta"]["n"],
        "places_missed_polygon": missed,
        "us_mapped": dict(zip(RELIGIONS, us_mapped)),
        "us_census": dict(zip(RELIGIONS, us_census)),
        "us_gap": {rel: us_census[i] - us_mapped[i] for i, rel in enumerate(RELIGIONS)},
        "undercount_gap_sum_by_rel": {
            rel: sum(r["gap"] for r in under if r["rel"] == rel) for rel in RELIGIONS
        },
        "filter": {"rel": args.rel, "min_gap": args.min_gap},
        "top_undercount": under_f[:200],
        "top_overcount": over_f[:50],
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    with OUT_TSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(
            ["fips", "state", "county", "rel", "mapped", "census", "gap", "pct", "note"]
        )
        for r in under_f:
            w.writerow(
                [
                    r["fips"],
                    r["state"],
                    r["county"],
                    r["rel"],
                    r["mapped"],
                    r["census"],
                    r["gap"],
                    r["pct"],
                    r["note"],
                ]
            )

    print("US Mapped vs Census (Census−Mapped, FIPS join):")
    for i, rel in enumerate(RELIGIONS):
        print(
            "  %-10s mapped=%7d  census=%7d  gap=%+7d"
            % (rel, us_mapped[i], us_census[i], us_census[i] - us_mapped[i])
        )
    print("places missed polygon: %d / %d" % (missed, places["meta"]["n"]))
    print("wrote", OUT_JSON)
    print("wrote", OUT_TSV)
    print()
    label = args.rel + (" (excl. Utah LDS note)" if args.exclude_lds_note else "")
    print("Top %d undercounts (%s):" % (args.top, label))
    print(
        "%-4s %-5s %-6s %-28s %7s %7s %6s %7s %s"
        % ("#", "FIPS", "ST", "COUNTY", "MAPPED", "CENSUS", "GAP", "%MISS", "NOTE")
    )
    for i, r in enumerate(print_rows[: args.top], 1):
        print(
            "%-4d %-5s %-6s %-28s %7d %7d %+6d %6s%% %s"
            % (
                i,
                r["fips"],
                r["state"],
                (r["county"] or "")[:28],
                r["mapped"],
                r["census"],
                r["gap"],
                r["pct"] if r["pct"] is not None else "—",
                r["note"],
            )
        )

    mad = next(
        (r for r in under if r["fips"] == "21151" and r["rel"] == "christian"),
        None,
    )
    if mad:
        print()
        print(
            "check Madison KY (21151): mapped=%d census=%d gap=%+d"
            % (mad["mapped"], mad["census"], mad["gap"])
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
