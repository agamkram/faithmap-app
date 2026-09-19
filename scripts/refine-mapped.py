#!/usr/bin/env python3
"""Refine Mapped: name DROP filters + exact lat/lon/religion stack collapse.

From data/raw/mapped-refine/ (Sprinkles + Chapel). Census untouched.
MosqueIndex muslim pins are not re-fetched; name filters may still drop
obvious non-worship shells (rare for muslim).

Usage:
    scripts/refine-mapped.py --dry
    scripts/refine-mapped.py
"""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from mapped_filters import osm_should_drop, stack_keep_score  # noqa: E402

DATA = ROOT / "data"
PLACES = DATA / "places.json"
PLACES_GZ = DATA / "places.json.gz"
SUMMARY = DATA / "summary.json"
REPORT = DATA / "raw" / "mapped-refine-report.json"

RELIGIONS = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
MUSLIM = 2


def write_places(places: list, meta: dict, religions: list) -> None:
    out = {"meta": meta, "k": religions, "p": places}
    raw = json.dumps(out, separators=(",", ":"))
    tmp = PLACES.with_suffix(".json.tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(PLACES)
    with gzip.open(PLACES_GZ, "wt", encoding="utf-8", compresslevel=6) as gz:
        gz.write(raw)
    SUMMARY.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    payload = json.loads(PLACES.read_text(encoding="utf-8"))
    places = list(payload["p"])
    meta = dict(payload.get("meta") or {})
    religions = payload.get("k") or RELIGIONS
    before = Counter(religions[r[1]] for r in places)
    print("before:", dict(before), "n=", len(places), flush=True)

    # --- Name DROP (skip muslim — MosqueIndex path stays intact) ---
    name_drop_by = Counter()
    name_drop_samples = []
    kept_after_name = []
    for row in places:
        if row[1] == MUSLIM:
            kept_after_name.append(row)
            continue
        reason = osm_should_drop(row[0] or "")
        if reason:
            name_drop_by[reason] += 1
            name_drop_by["by_rel:" + religions[row[1]]] += 1
            if len(name_drop_samples) < 40:
                name_drop_samples.append(
                    {"name": row[0], "rel": religions[row[1]], "state": row[4], "reason": reason}
                )
            continue
        kept_after_name.append(row)

    print(
        "name DROP %d  %s"
        % (sum(v for k, v in name_drop_by.items() if not k.startswith("by_rel:")), dict(name_drop_by)),
        flush=True,
    )

    # --- Exact lat/lon/rel stack collapse ---
    stacks: dict[tuple, list[int]] = defaultdict(list)
    for i, row in enumerate(kept_after_name):
        key = (round(float(row[2]), 5), round(float(row[3]), 5), int(row[1]))
        stacks[key].append(i)

    stack_extras = 0
    stack_clusters = 0
    drop_idx: set[int] = set()
    stack_samples = []
    for key, idxs in stacks.items():
        if len(idxs) < 2:
            continue
        stack_clusters += 1
        stack_extras += len(idxs) - 1
        ranked = sorted(idxs, key=lambda i: stack_keep_score(kept_after_name[i]), reverse=True)
        keep = ranked[0]
        for i in ranked[1:]:
            drop_idx.add(i)
        if len(stack_samples) < 25:
            stack_samples.append(
                {
                    "lat": key[0],
                    "lon": key[1],
                    "rel": religions[key[2]],
                    "n": len(idxs),
                    "keep": kept_after_name[keep][0],
                    "drop": [kept_after_name[i][0] for i in ranked[1:6]],
                }
            )

    print(
        "stacks %d  extras %d  collapsing to one each (−%d)"
        % (stack_clusters, stack_extras, len(drop_idx)),
        flush=True,
    )

    final = [row for i, row in enumerate(kept_after_name) if i not in drop_idx]
    after = Counter(religions[r[1]] for r in final)

    report = {
        "built": date.today().isoformat(),
        "before": dict(before),
        "after": dict(after),
        "name_drop": dict(name_drop_by),
        "name_drop_n": sum(v for k, v in name_drop_by.items() if not k.startswith("by_rel:")),
        "name_drop_samples": name_drop_samples,
        "stack_clusters": stack_clusters,
        "stack_extras_before": stack_extras,
        "stack_removed": len(drop_idx),
        "stack_samples": stack_samples,
        "n_before": len(places),
        "n_after": len(final),
        "muslim_before": before.get("muslim", 0),
        "muslim_after": after.get("muslim", 0),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("after:", dict(after), "n=", len(final), flush=True)
    print(
        "muslim %d → %d (target ~2771)"
        % (before.get("muslim", 0), after.get("muslim", 0)),
        flush=True,
    )
    print("wrote report %s" % REPORT, flush=True)

    if args.dry:
        print("dry run — places unchanged", flush=True)
        return 0

    meta["by"] = {k: after.get(k, 0) for k in religions}
    meta["n"] = len(final)
    meta["built"] = date.today().isoformat()
    meta["mapped_refine"] = {
        "date": date.today().isoformat(),
        "name_drop": report["name_drop_n"],
        "stack_removed": len(drop_idx),
        "n_before": len(places),
        "n_after": len(final),
    }
    write_places(final, meta, religions)
    print("assigning counties…", flush=True)
    return subprocess.call([sys.executable, str(ROOT / "scripts" / "assign-counties.py")])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as err:
        print("refine-mapped failed: %s" % err, file=sys.stderr)
        raise
