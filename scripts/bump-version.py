#!/usr/bin/env python3
"""Bump the app version everywhere at once.

Usage:
    scripts/bump-version.py          # next version (v1 -> v2)
    scripts/bump-version.py 12       # set explicitly to v12
    scripts/bump-version.py --check  # verify agreement, change nothing
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
APP = ROOT / "app.js"
SW = ROOT / "sw.js"
CSS = ROOT / "styles.css"

SPOTS = {
    "index asset ?v=": (INDEX, r"\?v=(\d+)", "?v={n}"),
    "index EXPECTED": (INDEX, r'var EXPECTED = "v(\d+)"', 'var EXPECTED = "v{n}"'),
    "app APP_VERSION": (APP, r'const APP_VERSION = "v(\d+)"', 'const APP_VERSION = "v{n}"'),
    "sw CACHE": (SW, r'const CACHE = "worship-v(\d+)"', 'const CACHE = "worship-v{n}"'),
    "sw precache ?v=": (SW, r"\?v=(\d+)", "?v={n}"),
    "css --wo-css": (CSS, r"--wo-css:\s*(\d+)", "--wo-css: {n}"),
}


def read(p):
    return p.read_text(encoding="utf-8")


def found(text, pattern):
    return [m.group(1) for m in re.finditer(pattern, text)]


def survey():
    cache = {}
    result = {}
    for name, (path, pattern, _) in SPOTS.items():
        if path not in cache:
            cache[path] = read(path)
        result[name] = found(cache[path], pattern)
    return result, cache


def report(survey_result):
    all_versions = set()
    problems = []
    for name, hits in survey_result.items():
        if not hits:
            problems.append("%s: no match found" % name)
            print("  %-18s MISSING" % name)
            continue
        uniq = sorted(set(hits))
        all_versions.update(uniq)
        flag = "" if len(uniq) == 1 else "  <-- inconsistent"
        print(
            "  %-18s v%s (%d spot%s)%s"
            % (name, ",v".join(uniq), len(hits), "" if len(hits) == 1 else "s", flag)
        )
        if len(uniq) > 1:
            problems.append("%s disagrees with itself: %s" % (name, uniq))
    if len(all_versions) > 1:
        problems.append("files disagree: found %s" % sorted(all_versions))
    return all_versions, problems


def main():
    args = [a for a in sys.argv[1:] if a]
    check_only = "--check" in args
    explicit = next((a for a in args if a.isdigit()), None)

    print("current:")
    result, cache = survey()
    versions, problems = report(result)

    if check_only:
        if problems:
            print("FAIL: " + "; ".join(problems))
            sys.exit(1)
        print("ok v%s" % next(iter(versions)))
        return

    if problems and not versions:
        print("FAIL: " + "; ".join(problems))
        sys.exit(1)

    current = max(int(v) for v in versions) if versions else 0
    nxt = int(explicit) if explicit else current + 1
    print("bumping to v%d" % nxt)

    for name, (path, pattern, template) in SPOTS.items():
        text = cache[path]
        cache[path] = re.sub(pattern, lambda m, t=template: t.format(n=nxt), text)
    for path, text in cache.items():
        path.write_text(text, encoding="utf-8")
    print("done v%d" % nxt)


if __name__ == "__main__":
    main()
