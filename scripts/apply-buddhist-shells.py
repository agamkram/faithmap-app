#!/usr/bin/env python3
"""Drop Buddhist HIFLD shells that are not a house of worship.

Does not add BuddhaNet (apartment sanghas / meetup groups).
Does not drop Vietnamese Buddhist Association-style legal names of wats,
Chan “Institute” temples, or Zen centers.

Usage:
    scripts/apply-buddhist-shells.py --dry
    scripts/apply-buddhist-shells.py
"""
from __future__ import annotations

import argparse
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
OUT = ROOT / "data" / "raw" / "buddhist-shell-package"

REL = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
BUDDHIST = 4

DROP = re.compile(
    r"\b(peace fellowship|charitable relief|relief fund|relief mission|"
    r"humanitarian|publishers|publishing|"
    r"research center|"
    r"federation of .{0,40}temples|"
    r"study group|sitting group|meditation group)\b",
    re.I,
)
KEEP = re.compile(r"\b(temple|wat\b|monastery|zen center|vihara|pagoda)\b", re.I)


def why(name: str) -> str | None:
    n = name or ""
    if KEEP.search(n):
        return None
    m = DROP.search(n)
    if not m:
        return None
    token = m.group(0).lower()
    if "fellowship" in token:
        return "peace_fellowship"
    if "relief" in token or "humanitarian" in token:
        return "relief"
    if "publish" in token:
        return "publishers"
    if "research" in token:
        return "research_center"
    if "federation" in token:
        return "temple_federation"
    return "home_group"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    rows = payload["p"]
    drop = []
    by_why = Counter()
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            continue
        if int(row[1]) != BUDDHIST:
            continue
        w = why(row[0] or "")
        if not w:
            continue
        by_why[w] += 1
        drop.append(
            {
                "i": i,
                "name": row[0],
                "city": row[6],
                "state": row[4],
                "why": w,
            }
        )

    report = {
        "date": date.today().isoformat(),
        "dropped": len(drop),
        "by_why": dict(by_why),
        "census_buddhist": 1984,
        "buddhanet_us_claim": 2687,
        "mapped_before": sum(1 for r in rows if isinstance(r, list) and r[1] == BUDDHIST),
        "dry": args.dry,
        "sample": drop,
        "independent_web": [
            "Buddhist Peace Fellowship: activist org, PO box #1324 Domingo Ave — not a temple",
            "Federation of Korean Buddhist Temples: umbrella of 22 temples, not a door",
            "Cambodian Charitable Relief Fund: charity EIN, not a wat",
            "Institute of Chung Hwa Buddhist Culture: KEEP — it is Chan Meditation Center, Elmhurst",
        ],
        "not_done": [
            "BuddhaNet ingest",
            "drop Association legal names of real wats",
            "cut Buddhist to census 1984",
        ],
    }
    (OUT / "dropped.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("drop", len(drop), dict(by_why), flush=True)
    for d in drop:
        print("  [%s] %s | %s, %s" % (d["why"], d["name"], d["city"], d["state"]), flush=True)

    if args.dry:
        return 0

    drop_i = {d["i"] for d in drop}
    kept = [row for i, row in enumerate(rows) if i not in drop_i]
    by = Counter()
    for row in kept:
        if isinstance(row, list) and len(row) > 1 and 0 <= int(row[1]) < 6:
            by[REL[int(row[1])]] += 1
    meta = payload.get("meta") or {}
    meta["n"] = len(kept)
    meta["by"] = {k: int(by.get(k, 0)) for k in REL}
    meta["built"] = date.today().isoformat()
    meta["buddhist_shell_drop"] = {
        "date": date.today().isoformat(),
        "dropped": len(drop_i),
        "rule": "peace fellowship / relief / research center / temple federation / home meditation group; keep Association wats and Chan institutes",
        "by_why": dict(by_why),
    }
    src = meta.get("source") or ""
    note = " − Buddhist shells (%d)" % len(drop_i)
    if "Buddhist shells" not in src:
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
        summary["buddhist_shell_drop"] = meta["buddhist_shell_drop"]
        if "Buddhist shells" not in (summary.get("source") or ""):
            summary["source"] = (summary.get("source") or "") + note
        SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("wrote places n=%d buddhist=%d dropped=%d" % (len(kept), by["buddhist"], len(drop_i)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
