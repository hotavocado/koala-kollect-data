"""tcgcsv cross-check of en release dates. A warning, never a gate, and never a source.

tcgcsv is TCGplayer, so its group dates are North American releases. They agree
with Bandai en on all 58 en products with a code (survey 2026-10-08) and are a
FALSE JOIN for asia-en, jp and tc, whose dates differ from en on every shared
code. So only en is compared, and a disagreement is printed for a person to
read; the date in data/ always comes from the site's own product index.
"""
import json
import re
import urllib.request
from pathlib import Path

GROUPS = "https://tcgcsv.com/tcgplayer/68/groups"


def norm(code):
    """OP01, OP-01 and op-01 compare equal; OP14-EB04 stays compound."""
    return re.sub(r"([A-Z]+)-?0*([0-9]+)", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}", code.upper())


def compare(products, groups):
    """(compared, [warning]) for en products with a code and a date against tcgcsv groups.

    Groups whose abbreviation carries a suffix (ST-01 PRE, OP10 RE, OP05 ANN)
    are pre-release and event editions, separate products, and are skipped.
    """
    published = {}
    for g in groups:
        if " " in g["abbreviation"].strip():
            continue
        published[norm(g["abbreviation"])] = g["publishedOn"][:10]
    compared, warnings = 0, []
    for p in products:
        if p["site"] != "en" or "code" not in p or "release_date" not in p:
            continue
        theirs = published.get(norm(p["code"]))
        if theirs is None:
            continue
        compared += 1
        if theirs != p["release_date"]:
            warnings.append(f"{p['key']} {p['code']}: Bandai en {p['release_date']}, tcgcsv {theirs}")
    return compared, warnings


def check(root, fetch=None):
    """Lines for check.py to print. Never raises: an unreachable tcgcsv is a warning too."""
    path = Path(root) / "data" / "products" / "en.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines()]
    try:
        groups = (fetch or _fetch)()
    except Exception as e:
        return [f"warn tcgcsv en cross-check skipped: {type(e).__name__}: {e}"]
    compared, warnings = compare(rows, groups)
    lines = [f"warn tcgcsv en release date: {w}" for w in warnings]
    lines.append(f"{'warn' if warnings else 'ok  '} tcgcsv en cross-check: {compared} compared, {len(warnings)} disagree")
    return lines


def _fetch():
    req = urllib.request.Request(GROUPS, headers={"User-Agent": "koala-kollect-check/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["results"]
