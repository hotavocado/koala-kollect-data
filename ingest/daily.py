"""Step 4: the daily run. Fetch every site, detect new series, ingest, decide.

usage: python daily.py <pageset_dir> <data_repo> [site ...] --out DIR [--dry-run] [--fake-option SITE:ID:LABEL]

Run by .github/workflows/daily.yml. It writes the data repo through run.py and
leaves committing to the workflow, which commits only when this exits 0 and
says changed=true.

Exits 1, and the workflow commits nothing, when any page or the cn list failed
to fetch, when run.py stops (RunError), or when any page was refused by the
removal guard. A refusal is how a layout change or a truncated page shows up,
so a run that refused a page is not a run to commit.

changed=true only when the run added, changed or removed a record, or the cn id
snapshot moved. Every row's last_seen_at widens on every fetch, so the tree
always differs; that is not a change (86674: a no-change day rewrites 70,494
lines that differ only in seen-times).

New series are found by diffing each site's series dropdown against the
committed product list, before the run, so a new series whose page is refused
is still reported. The notice is written to --out for the workflow to open as an
issue labelled new-series (upper 86676).
"""
import argparse
import json
import os
import sys
from pathlib import Path

import cn
import fetch
import run
from bandai import series_options

SITES = run.SITES
TITLE_MAX = 256


def committed_products(repo, site):
    path = Path(repo) / "data" / "products" / f"{site}.jsonl"
    return set(run.read_jsonl(path))


def new_options(index_html, committed, site):
    """Dropdown options with no committed product, as (series_id, label), in page order."""
    return [(sid, label) for sid, label in series_options(index_html) if f"{site}:{sid}" not in committed]


def notice(found):
    """(title, body) for the new-series issue, or None. found: {site: [(series_id, label)]}."""
    items = [(site, sid, label) for site in SITES for sid, label in found.get(site, [])]
    if not items:
        return None
    names = sorted({label for _, _, label in items})
    title = "New series on the official card list: " + "; ".join(names)
    if len(title) > TITLE_MAX:  # GitHub refuses an issue title over 256 characters
        title = f"New series on the official card list: {len(names)} new, first {names[0]}"[:TITLE_MAX]
    lines = ["The daily ingest found series in the card list dropdown that are not in the data yet:", ""]
    for site, sid, label in items:
        lines.append(f"- {site}: {label} (series {sid}) {fetch.cardlist_url(site, sid)}")
    lines += ["", "The ingest adds each one on its next clean run of that page. This issue is the notice; "
              "nothing needs doing unless the page keeps getting refused."]
    return title, "\n".join(lines) + "\n"


def decide(fetch_failures, result):
    """(failures, changed). failures empty means the run may commit."""
    failures = list(fetch_failures)
    if result is None:
        return failures, False
    changed = False
    for site, r in result["sites"].items():
        c = r["counts"]
        for arm, n in r["refusals"].items():
            if n:
                failures.append(f"{site}: {n} page(s) refused ({arm}): "
                                + ", ".join(sorted(s for s, a in r["page_refusals"].items() if a == arm)))
        if c["added"] or c["changed"] or c["removed"]:
            changed = True
    if any(result["cards"].values()):
        changed = True
    cn_diff = result.get("cn") or {}
    if cn_diff.get("added") or cn_diff.get("gone") or cn_diff.get("renumbered"):
        changed = True
    return failures, changed


def fetch_all(pageset, sites):
    """Fetch every site and the cn list. Returns (failures, cn_rows)."""
    failures = []
    for site in sites:
        try:
            log = fetch.fetch_site(site, pageset)
        except Exception as e:  # the index or dropdown itself failed
            failures.append(f"{site}: fetch stopped: {type(e).__name__}: {e}")
            continue
        for entry in log:
            if "error" in entry:
                failures.append(f"{site}:{entry['series_id']} fetch failed: {entry['error']}")
    try:
        cn_rows = cn.list_all()
    except Exception as e:
        failures.append(f"cn: list failed: {type(e).__name__}: {e}")
        cn_rows = None
    return failures, cn_rows


def parse_fake(spec):
    site, sid, label = spec.split(":", 2)
    if site not in SITES or not sid.isdigit() or not label:
        raise ValueError(f"--fake-option wants SITE:SERIES_ID:LABEL with SITE in {SITES}, got {spec!r}")
    return site, sid, label


def daily(pageset, repo, out, sites=SITES, dry_run=False, fake=None, fetcher=None, runner=None):
    """Returns (exit code, summary). Writes summary.json, and notice files when there is a notice."""
    pageset, repo, out = Path(pageset), Path(repo), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    # A notice left by an earlier run in the same --out must not read as this run's.
    for stale in ("notice_title.txt", "notice_body.md", "summary.json"):
        (out / stale).unlink(missing_ok=True)
    if fake and not dry_run:
        raise ValueError("--fake-option is a control and needs --dry-run, so a fake series never reaches Mike")
    fetch_failures, cn_rows = (fetcher or fetch_all)(pageset, sites)

    found = {}
    for site in sites:
        index = pageset / site / "_index.html"
        if index.exists():
            found[site] = new_options(index.read_text("utf-8", "replace"), committed_products(repo, site), site)
    if fake:
        site, sid, label = fake
        found.setdefault(site, []).append((sid, label))
    note = notice(found)
    if note:
        (out / "notice_title.txt").write_text(note[0] + "\n", "utf-8")
        (out / "notice_body.md").write_text(note[1], "utf-8")

    result, stopped = None, None
    if not fetch_failures:
        try:
            result = (runner or run.run)(pageset, repo, sites, cn_rows)
        except run.RunError as e:
            stopped = f"run stopped, nothing written: {e}"
        except Exception as e:  # e.g. model.product_fields on an unmapped product kind
            # Kept on this path, not raised, so the notice and the outputs are still
            # written: a new kind of product is exactly when the notice matters.
            stopped = f"run crashed, nothing committed: {type(e).__name__}: {e}"
    failures, changed = decide(fetch_failures, result)
    if stopped:
        failures.append(stopped)
    if fetch_failures:
        failures.append("not ingested: a fetch failed")
    summary = {"ok": not failures, "changed": changed and not failures, "dry_run": dry_run,
               "failures": failures, "new_series": {s: v for s, v in found.items() if v},
               "notice_title": note[0] if note else None}
    if result:
        summary["sites"] = {s: r["run"] for s, r in result["sites"].items()}
        summary["cards"] = result["cards"]
        summary["cn"] = result["cn"]
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return (0 if not failures else 1), summary


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pageset")
    ap.add_argument("repo")
    ap.add_argument("sites", nargs="*", default=SITES)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fake-option", default="", help="control: SITE:SERIES_ID:LABEL, dry runs only")
    a = ap.parse_args(argv[1:])
    fake = parse_fake(a.fake_option) if a.fake_option else None
    code, s = daily(a.pageset, a.repo, a.out, a.sites, dry_run=a.dry_run, fake=fake)
    for site, r in s.get("sites", {}).items():
        print(f"{site}: pages {r['pages_fetched']} (unchanged {r['pages_unchanged']}), blocks {r['blocks_parsed']}, "
              f"added {r['added']}, changed {r['changed']}, removed {r['removed']}, refusals {r['refusals']}")
    if "cn" in s:
        print(f"cards: {s['cards']}  cn: {s['cn']}")
    if s["notice_title"]:
        print("NEW SERIES, issue to open:")
        print("  title: " + s["notice_title"])
        print("  " + (Path(a.out) / "notice_body.md").read_text("utf-8").replace("\n", "\n  "))
    for f in s["failures"]:
        print(f"FAILED: {f}")
    print(f"ok={s['ok']} changed={s['changed']} dry_run={s['dry_run']}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"changed={'true' if s['changed'] else 'false'}\n")
            fh.write(f"new_series={'true' if s['notice_title'] else 'false'}\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
