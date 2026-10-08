"""Step 2 done-check over a directory of fetched pages. Writes nothing.

The series list comes from fetch.py's _fetch_log.json, never from the HTML files
on disk: a series whose fetch failed has no page, and a page with no log entry
is stale from an earlier run. Both fail, as does a missing or empty log.

For every series page: the parser's block count must equal an independent count
of card-image ids in the same HTML, and no image id may repeat within a page.
Also prints per-site totals, category counts, and the fields the parser could
not find, so a layout change shows up as a number, not a silence.

usage: python donecheck.py <pages_dir> [site ...]
"""
import collections
import json
import sys
from pathlib import Path

from bandai import independent_count, parse_page

# A block missing any of these fails its page: equal counts with blank fields is
# what a renamed class looks like (control: cardName -> cardTitle).
REQUIRED = ["number", "rarity", "category", "name", "image_src"]
# Reported, not failed: the site itself omits it on some blocks
# (en ST14-010_r1 has no getInfo div at all, 2026-10-08).
REPORTED = ["source_text"]


def _logged_pages(site_dir, failures):
    log_path = site_dir / "_fetch_log.json"
    if not log_path.exists():
        failures.append("no _fetch_log.json: a failed series cannot be told from a missing one")
        return []
    log = json.loads(log_path.read_text("utf-8"))
    if not log:
        failures.append("_fetch_log.json lists no series")
    pages, logged = [], set()
    for e in log:
        sid = e["series_id"]
        logged.add(sid)
        page = site_dir / f"{sid}.html"
        if e.get("status") != 200:
            failures.append(f"{sid}: fetch failed ({e.get('error', e.get('status'))})")
        elif not page.exists():
            failures.append(f"{sid}: logged as fetched but no page on disk")
        else:
            pages.append(page)
    for p in site_dir.glob("*.html"):
        if not p.name.startswith("_") and p.stem not in logged:
            failures.append(f"{p.stem}: page on disk with no entry in this run's log (stale)")
    return sorted(pages)


def check_site(site_dir):
    failures, totals = [], collections.Counter()
    pages = _logged_pages(site_dir, failures)
    ids_by_page, cats, missing = {}, collections.Counter(), collections.Counter()
    for p in pages:
        html = p.read_text("utf-8", "replace")
        blocks = parse_page(html)
        ind = independent_count(html)
        ids = [b["image_id"] for b in blocks]
        if len(blocks) != ind:
            failures.append(f"{p.stem}: parsed {len(blocks)} != independent {ind}")
        if len(set(ids)) != len(ids):
            failures.append(f"{p.stem}: {len(ids) - len(set(ids))} repeated image ids")
        if not blocks:
            failures.append(f"{p.stem}: zero blocks")
        ids_by_page[p.stem] = ids
        totals["blocks"] += len(blocks)
        page_missing = collections.Counter()
        for b in blocks:
            cats[b["category"]] += 1
            for f in REQUIRED + REPORTED:
                if not b[f]:
                    missing[f] += 1
                    if f in REQUIRED:
                        page_missing[f] += 1
        for f, n in page_missing.items():
            failures.append(f"{p.stem}: {n} blocks missing {f}")
    unique = {i for ids in ids_by_page.values() for i in ids}
    return {
        "pages": len(pages),
        "blocks": totals["blocks"],
        "unique_image_ids": len(unique),
        "listed_on_2plus_pages": totals["blocks"] - len(unique),
        "categories": dict(cats),
        "missing": dict(missing),
        "failures": failures,
        "per_page": {k: len(v) for k, v in ids_by_page.items()},
    }


def main(argv):
    root = Path(argv[1])
    sites = argv[2:] or sorted(d.name for d in root.iterdir() if d.is_dir())
    bad = 0
    for s in sites:
        r = check_site(root / s)
        bad += len(r["failures"])
        print(f"{s}: {r['pages']} pages, {r['blocks']} blocks, {r['unique_image_ids']} unique image ids, "
              f"{r['listed_on_2plus_pages']} extra listings; categories {r['categories']}; missing {r['missing']}")
        for f in r["failures"]:
            print(f"  FAIL {f}")
    print("PASS" if bad == 0 else f"FAIL ({bad})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
