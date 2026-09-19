#!/usr/bin/env python3
"""Assign each Mapped place to a county via lat/lon against geo/counties.geojson.

Overwrites places[].c (and .s when the polygon disagrees) so Census mode can
join on real geography instead of HIFLD SUBREGION strings (often NOT AVAILABLE).
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "data" / "places.json"
PLACES_GZ = ROOT / "data" / "places.json.gz"
SUMMARY = ROOT / "data" / "summary.json"
COUNTIES = ROOT / "geo" / "counties.geojson"


def main() -> int:
    try:
        from shapely.geometry import Point, shape
        from shapely.strtree import STRtree
    except ImportError:
        print("pip install shapely", file=sys.stderr)
        return 1

    try:
        from shapely import make_valid
    except ImportError:
        make_valid = None

    geo = json.loads(COUNTIES.read_text(encoding="utf-8"))
    polys = []
    meta = []
    skipped_geom = 0
    for ft in geo.get("features") or []:
        props = ft.get("properties") or {}
        st = (props.get("s") or "").strip().upper()
        cname = (props.get("c") or "").strip()
        if not st or not cname:
            continue
        try:
            geom = shape(ft["geometry"])
            if make_valid is not None and not geom.is_valid:
                geom = make_valid(geom)
            if geom.is_empty:
                skipped_geom += 1
                continue
            # STRtree needs surfaces; explode GeometryCollection to polys.
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
            else:
                skipped_geom += 1
                continue
            for g in geoms:
                if g.is_empty or g.area <= 0:
                    continue
                polys.append(g)
                meta.append((st, cname))
        except Exception:
            skipped_geom += 1
            continue

    if not polys:
        print("no usable county polygons", file=sys.stderr)
        return 1
    print("county polygons %d (skipped geom %d)" % (len(polys), skipped_geom), flush=True)
    tree = STRtree(polys)
    # shapely 2: tree.query returns indexes
    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    rows = payload.get("p") or []
    changed_c = 0
    changed_s = 0
    missed = 0
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            missed += 1
            continue
        lat = float(row[2])
        lon = float(row[3])
        pt = Point(lon, lat)
        hits = tree.query(pt)
        found = None
        for idx in hits:
            poly = polys[int(idx)]
            if poly.contains(pt) or poly.touches(pt):
                found = meta[int(idx)]
                break
        if found is None:
            # fallback: nearest among query hits / tiny buffer
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
        st, cname = found
        if row[5] != cname:
            changed_c += 1
            row[5] = cname
        if row[4] != st:
            changed_s += 1
            row[4] = st

    text = json.dumps(payload, separators=(",", ":"))
    PLACES.write_text(text, encoding="utf-8")
    PLACES_GZ.write_bytes(gzip.compress(text.encode("utf-8"), 6))

    if SUMMARY.exists():
        summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
        summary["county_assign"] = {
            "changed_county": changed_c,
            "changed_state": changed_s,
            "missed": missed,
            "n": len(rows),
        }
        SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(
        "assigned %d places; county changes %d; state changes %d; missed %d"
        % (len(rows), changed_c, changed_s, missed)
    )
    return 0 if missed < len(rows) * 0.01 else 1


if __name__ == "__main__":
    raise SystemExit(main())
