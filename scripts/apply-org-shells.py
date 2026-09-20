#!/usr/bin/env python3
"""Drop org shells that are not a house of worship.

Keeps nondenominational churches whose legal name is “Ministries”.
Does not touch MosqueIndex Muslim pins, or Buddhist/Sikh “Association”
temples.

Usage:
    scripts/apply-org-shells.py --dry
    scripts/apply-org-shells.py
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
OUT = ROOT / "data" / "raw" / "org-shell-package"

REL = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]

JW_ADMIN = re.compile(
    r"\b(ministr(?:y|ies)|association|alliance|federation|"
    r"foundation|endowment|publishers)\b",
    re.I,
)
JW_KEEP = re.compile(
    r"\b(synagogue|shul|chabad|lubavitch|temple|congregation|cong\b|"
    r"beit|beth|havurah|chavurah|center|conservative|reform|orthodox|"
    r"reconstructionist|young israel|chapel|tabernacle|"
    r"kehilat|kehilla|yeshiva|kollel)\b",
    re.I,
)
MIKVAH_ERUV = re.compile(r"\b(mikvah|mikveh|eruv)\b", re.I)
CH_ADMIN = re.compile(r"\b(foundation|federation|endowment|publishers)\b", re.I)
CH_KEEP = re.compile(
    r"\b(church(?:es)?|chapel|cathedral|parish|synagogue|tabernacle|"
    r"fellowship|assembly|congregation|mosque|mandir|gurdwara|"
    r"temple|kingdom hall)\b",
    re.I,
)
CATH_D = re.compile(r"\bcatholic daughters\b", re.I)


def classify(name: str, rel: str) -> str | None:
    n = name or ""
    if CATH_D.search(n):
        return "catholic_daughters"
    if rel == "jewish":
        if MIKVAH_ERUV.search(n):
            return "jewish_mikvah_eruv"
        if JW_ADMIN.search(n) and not JW_KEEP.search(n):
            return "jewish_org"
        return None
    if rel == "christian" and CH_ADMIN.search(n) and not CH_KEEP.search(n):
        return "christian_foundation"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    rows = payload["p"]
    drop = []
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            continue
        rel_i = int(row[1])
        rel = REL[rel_i] if 0 <= rel_i < 6 else "?"
        why = classify(row[0] or "", rel)
        if not why:
            continue
        drop.append(
            {
                "i": i,
                "name": row[0],
                "rel": rel,
                "state": row[4],
                "city": row[6] or "",
                "lat": row[2],
                "lon": row[3],
                "why": why,
            }
        )

    by_why = Counter(d["why"] for d in drop)
    by_rel = Counter(d["rel"] for d in drop)
    print("drop %d  %s" % (len(drop), dict(by_why)), flush=True)
    print("  by rel", dict(by_rel), flush=True)
    canary = [
        d["name"]
        for d in drop
        if "alliance jewish" in d["name"].lower()
        or "messiahs harvest" in d["name"].lower()
    ]
    print("canary", canary, flush=True)

    fields = ["i", "name", "rel", "state", "city", "lat", "lon", "why"]
    with (OUT / "dropped.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(drop)
    report = {
        "date": date.today().isoformat(),
        "rule": "org shell, no worship building word; not all Ministries",
        "dropped": len(drop),
        "by_why": dict(by_why),
        "by_rel": dict(by_rel),
        "canary": canary,
        "dry": args.dry,
        "sample": drop[:25],
    }
    (OUT / "dropped.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    if args.dry:
        print("dry — wrote %s" % (OUT / "dropped.tsv"), flush=True)
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
    meta["org_shell_drop"] = {
        "date": date.today().isoformat(),
        "dropped": len(drop_i),
        "rule": "ministries/association/federation without a worship building word; Catholic Daughters; Jewish mikvah/eruv",
        "by_why": dict(by_why),
        "by": dict(by_rel),
    }
    src = meta.get("source") or ""
    note = " − org shells (%d)" % len(drop_i)
    if "org shells" not in src:
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
        summary["org_shell_drop"] = meta["org_shell_drop"]
        if "org shells" not in (summary.get("source") or ""):
            summary["source"] = (summary.get("source") or "") + note
        SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("wrote places n=%d dropped=%d" % (len(kept), len(drop_i)), flush=True)
    print("after", dict(meta["by"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
