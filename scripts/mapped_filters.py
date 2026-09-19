#!/usr/bin/env python3
"""Shared Mapped keep/drop rules (Sprinkles+Chapel refine package)."""
from __future__ import annotations

import re

OSM_DROP_RE = re.compile(
    r"\b("
    r"elementary school|high school|middle school|kindergarten|daycare|day care|"
    r"bible society|"
    r"ymca|ywca|"
    r"council of churches|committee on publication|"
    r"bible camp|youth camp|campmeeting|"
    r"wayside cross|wayside shrine"
    r")\b",
    re.I,
)

OSM_ENTITY_DROP_RE = re.compile(
    r"("
    r"\b(cemetery|graveyard|mausoleum)\b|"
    r"\b(foundation|endowment(?:\s+fund)?)\b|"
    r"\b(llc|l\.l\.c)\b|"
    r"\b(hermitage|retreat (?:center|centre)|camp and retreat)\b|"
    r"\b(charitable trust|realty)\b"
    r")",
    re.I,
)

SCHOOL_ENTITY_RE = re.compile(r"\b(school|academy|seminary)\b", re.I)
CONG_CUE_RE = re.compile(
    r"\b(church|chapel|cathedral|parish|mosque|masjid|synagogue|shul|"
    r"mandir|gurdwara|temple|kingdom hall|baptist|methodist|lutheran|"
    r"presbyterian|episcopal|catholic|pentecostal|orthodox|adventist|"
    r"christian science society)\b",
    re.I,
)
WORSHIP_BUILDING_RE = re.compile(
    r"\b(church|chapel|cathedral|parish|synagogue|mosque|masjid|"
    r"mandir|gurdwara|temple|kingdom hall)\b",
    re.I,
)

# Stack keep-score penalties / bonuses
STACK_PENALTY_RE = re.compile(
    r"\b("
    r"corp sole|corporation sole|pastor of|"
    r"stake\b|gemach|yeshiva corp|kollel corp|"
    r"ministr(?:y|ies)\s+inc|ministr(?:y|ies)\s+incorporated|"
    r"foundation|endowment|llc|l\.l\.c|realty|charitable trust|"
    r"housing|association|council of"
    r")\b",
    re.I,
)
STACK_BONUS_RE = re.compile(
    r"\b(church|chapel|cathedral|parish|mosque|masjid|synagogue|shul|"
    r"mandir|gurdwara|temple|kingdom hall|congregation)\b",
    re.I,
)


def osm_should_drop(name: str) -> str | None:
    """Return a reason code to DROP, or None to KEEP."""
    n = (name or "").strip()
    if not n:
        return "unnamed_weak"
    if OSM_DROP_RE.search(n):
        return "osm_drop_re"
    if SCHOOL_ENTITY_RE.search(n) and not CONG_CUE_RE.search(n):
        return "school_entity"
    if SCHOOL_ENTITY_RE.search(n) and re.search(r"\bchapel\b", n, re.I):
        if not re.search(r"\b(church|parish|cathedral)\b", n, re.I):
            return "school_campus_chapel"
    if re.search(r"\b(cemetery|graveyard|mausoleum)\b", n, re.I):
        if CONG_CUE_RE.search(n) and WORSHIP_BUILDING_RE.search(n):
            return None  # Memorial Park Baptist Church
        return "cemetery_entity"
    if OSM_ENTITY_DROP_RE.search(n):
        # Worship building + admin token → still drop admin shells
        # (UMC Foundation of New England, Hillel Foundation).
        # Keep only when clearly a named congregation with cong cue and
        # the entity token is not the primary identity.
        if WORSHIP_BUILDING_RE.search(n) and not re.search(
            r"\b(foundation|endowment|llc|hermitage|retreat|trust|realty)\b", n, re.I
        ):
            return None
        return "entity_admin"
    return None


def stack_keep_score(row: list) -> tuple:
    """Higher = keep. Prefer worship-building names over mailing/corp shells."""
    n = row[0] or ""
    c = (row[5] or "").strip()
    y = (row[6] or "").strip()
    bad_c = (not c) or c.upper() in ("NOT AVAILABLE", "N/A")
    penalty = 5 if STACK_PENALTY_RE.search(n) else 0
    bonus = 3 if STACK_BONUS_RE.search(n) else 0
    # Prefer modest real names over mega "Pastor of … Corp Sole" titles
    length_pen = 2 if len(n) > 70 else 0
    return (
        bonus - penalty - length_pen,
        0 if bad_c else 2,
        1 if y else 0,
        len(n),
    )
