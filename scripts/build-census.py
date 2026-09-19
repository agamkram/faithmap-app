#!/usr/bin/env python3
"""Bake county × religion congregation counts from 2020 US Religion Census.

Output: data/census.json — compact arrays for FaithMap Census mode.
Source: ASARB 2020 Group Detail Excel (no addresses; counts only).
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "census.json"
OUT_GZ = ROOT / "data" / "census.json.gz"
XLSX_URL = (
    "https://www.usreligioncensus.org/sites/default/files/2023-06/"
    "2020_USRC_Group_Detail.xlsx"
)
XLSX = ROOT / "data" / "raw" / "2020_USRC_Group_Detail.xlsx"

REL_IDS = ["christian", "jewish", "muslim", "hindu", "buddhist", "sikh"]
REL_INDEX = {r: i for i, r in enumerate(REL_IDS)}

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


def county_key(state_name: str, county_name: str) -> str | None:
    abbr = STATE_ABBR.get(state_name or "")
    if not abbr:
        return None
    c = (county_name or "").strip()
    c = re.sub(
        r"\s+(County|Parish|Borough|Census Area|Municipality|"
        r"City and Borough|City and County of)\s*$",
        "",
        c,
        flags=re.I,
    )
    c = re.sub(r"^City and County of\s+", "", c, flags=re.I)
    c = re.sub(r"\s+", " ", c).strip()
    if not c:
        return None
    return abbr + "|" + c.upper()


def ensure_xlsx() -> Path:
    XLSX.parent.mkdir(parents=True, exist_ok=True)
    if XLSX.exists() and XLSX.stat().st_size > 10000:
        return XLSX
    print("downloading USRC group detail…", flush=True)
    urllib.request.urlretrieve(XLSX_URL, XLSX)
    return XLSX


def main() -> int:
    try:
        import openpyxl
    except ImportError:
        print("pip install openpyxl", file=sys.stderr)
        return 1

    path = ensure_xlsx()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["2020 Group by County"]

    by_county: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    by_state: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    us = [0, 0, 0, 0, 0, 0]
    skipped = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        fips, state_name, county_name, code, gname, cong = row[:6]
        if not gname or str(code).lower() == "totals":
            continue
        rel = classify(str(gname))
        if rel is None:
            skipped += 1
            continue
        n = int(cong or 0)
        if n <= 0:
            continue
        key = county_key(str(state_name or ""), str(county_name or ""))
        if not key:
            skipped += 1
            continue
        i = REL_INDEX[rel]
        by_county[key][i] += n
        st = key.split("|", 1)[0]
        by_state[st][i] += n
        us[i] += n

    # Compact: only counties with any count; keys sorted
    counties = []
    for key in sorted(by_county.keys()):
        vals = by_county[key]
        if any(vals):
            counties.append([key, vals])

    states = {k: by_state[k] for k in sorted(by_state.keys()) if any(by_state[k])}

    payload = {
        "meta": {
            "source": "2020 U.S. Religion Census (ASARB) — congregations by county",
            "built": __import__("datetime").date.today().isoformat(),
            "k": REL_IDS,
            "us": us,
            "counties": len(counties),
            "note": "No addresses. Census mode places dots via HIFLD spatial template.",
        },
        "us": us,
        "s": states,
        "c": counties,
    }
    text = json.dumps(payload, separators=(",", ":"))
    OUT.write_text(text)
    OUT_GZ.write_bytes(gzip.compress(text.encode("utf-8"), 9))
    print("us", us, "sum", sum(us))
    print("counties", len(counties), "bytes", OUT.stat().st_size, "gz", OUT_GZ.stat().st_size)
    print("skipped group-rows", skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
