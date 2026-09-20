#!/usr/bin/env python3
"""Messianic / Yeshua pins tagged Jewish: churches, not synagogues.

Reclassify congregations to Christian. Drop councils, publishers, and
Jews for Jesus-style outreach orgs. Does not touch Chabad / Cong Bais / Khal.

Usage:
    scripts/apply-messianic-reclass.py --dry
    scripts/apply-messianic-reclass.py
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "data" / "places.json"
PLACES_GZ = ROOT / "data" / "places.json.gz"
SUMMARY = ROOT / "data" / "summary.json"
OUT = ROOT / "data" / "raw" / "messianic-package"

REL = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
CHRISTIAN = 0
JEWISH = 1

MES = re.compile(
    r"\b(messianic|jews for jesus|chosen people|yeshua|yahshua|"
    r"moshiach|mashiach|messiah|mesianica|mesianic)\b",
    re.I,
)
WORSHIP = re.compile(
    r"\b(congregation|synagogue|synagog|fellowship|beit|beth|"
    r"kehilat|kehillat|kehillah|temple|assembly|community|"
    r"center|church|shul|cong\b|tabernacle|chapel)\b",
    re.I,
)
ORG = re.compile(
    r"\b(council|publishers|publishing|movement international|"
    r"perspectives|journey to healing|jews for jesus|"
    r"communities)\b",
    re.I,
)


def action(name: str) -> str | None:
    n = name or ""
    if not MES.search(n):
        return None
    if ORG.search(n) and not WORSHIP.search(n):
        return "drop"
    if WORSHIP.search(n) or MES.search(n):
        return "christian"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    rows = payload["p"]
    moves = []
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            continue
        if int(row[1]) != JEWISH:
            continue
        act = action(row[0] or "")
        if not act:
            continue
        moves.append(
            {
                "i": i,
                "name": row[0],
                "state": row[4],
                "city": row[6] or "",
                "lat": row[2],
                "lon": row[3],
                "action": act,
            }
        )

    by = Counter(m["action"] for m in moves)
    print("n %d  %s" % (len(moves), dict(by)), flush=True)
    for m in moves:
        if m["action"] == "drop":
            print("  drop", m["name"], "|", m["state"], m["city"] or "(osm)")
    print("reclass", by.get("christian", 0), flush=True)

    fields = ["i", "name", "state", "city", "lat", "lon", "action"]
    with (OUT / "moves.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(moves)
    report = {
        "date": date.today().isoformat(),
        "rule": "Jewish + Messianic/Yeshua: congregation → Christian, org → drop",
        "n": len(moves),
        "by": dict(by),
        "dry": args.dry,
        "drops": [m["name"] for m in moves if m["action"] == "drop"],
    }
    (OUT / "moves.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.dry:
        print("dry — wrote %s" % (OUT / "moves.tsv"), flush=True)
        return 0

    drop_i = {m["i"] for m in moves if m["action"] == "drop"}
    reclass_i = {m["i"] for m in moves if m["action"] == "christian"}
    for i in reclass_i:
        rows[i][1] = CHRISTIAN
    kept = [row for i, row in enumerate(rows) if i not in drop_i]
    counts = Counter()
    for row in kept:
        if isinstance(row, list) and len(row) > 1 and 0 <= int(row[1]) < 6:
            counts[REL[int(row[1])]] += 1
    meta = payload.get("meta") or {}
    meta["n"] = len(kept)
    meta["by"] = {k: int(counts.get(k, 0)) for k in REL}
    meta["built"] = date.today().isoformat()
    meta["messianic_reclass"] = {
        "date": date.today().isoformat(),
        "to_christian": len(reclass_i),
        "dropped": len(drop_i),
        "rule": "Messianic/Yeshua tagged Jewish: congregations → Christian, orgs dropped",
    }
    src = meta.get("source") or ""
    note = " · Messianic Jewish → Christian (%d) − orgs (%d)" % (
        len(reclass_i),
        len(drop_i),
    )
    if "Messianic Jewish" not in src:
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
        summary["messianic_reclass"] = meta["messianic_reclass"]
        if "Messianic Jewish" not in (summary.get("source") or ""):
            summary["source"] = (summary.get("source") or "") + note
        SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("wrote places n=%d" % len(kept), flush=True)
    print("after", dict(meta["by"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
