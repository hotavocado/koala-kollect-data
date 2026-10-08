"""Step 3: identity, upsert, ingest_run and the removal guard. Writes the data repo.

usage: python run.py <pageset_dir> <data_repo> [site ...] [--cn-list FILE]

<pageset_dir> is fetch.py's output: {site}/_index.html, {site}/{series_id}.html
and {site}/_fetch_log.json. Every timestamp written under data/ is a page's own
fetched_at, so a re-run over the same pages writes byte-identical files; only
runs/ (the audit log) carries wall-clock time.

Rules (CONTRACT.md):
- Keys are looked up by natural key first and minted only when nothing matches.
  Minting is derived from the natural key, so a lost lookup mints the same key.
- A page is refused, and changes NOTHING (no upserts, no removals), when its
  fetch failed (http_error), it parsed no blocks (zero_parse), its block count
  disagrees with the independent image count (count_mismatch), or its count fell
  more than 10% below its last clean run (count_drop). Each refusal is counted.
- A clean page stamps removed_at on its own listings that it no longer shows.
- A block with a blank required field, or a value outside a measured vocabulary,
  stops the whole run before anything is written.
- DON cards are not written here (Mike 86520: they mint from tcgcsv).
"""
import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from jsonschema import Draft202012Validator

import model
from bandai import independent_count, parse_page

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema/v1.schema.json").read_text("utf-8"))
SITES = ["en", "asia-en", "jp", "tc"]
FACTS_ORDER = ["jp", "en", "asia-en", "tc", "cn"]
ARMS = ["http_error", "zero_parse", "count_drop", "count_mismatch"]
DROP_FLOOR = 0.9
REQUIRED = ["number", "rarity", "category", "name", "image_src"]
# Per-site record types and their directory under data/ (CONTRACT.md layout).
PER_SITE = {"card_observation": "card_observations", "printing": "printings",
            "printing_locator": "printing_locators", "product": "products",
            "printing_product": "printing_products"}
SHARED = {"card": "cards", "distribution": "distributions",
          "printing_distribution": "printing_distributions", "printing_link": "printing_links"}
TYPE_OF_DIR = {v: k for k, v in {**PER_SITE, **SHARED}.items()}
SEEN = ("first_seen_at", "last_seen_at")


class RunError(Exception):
    """The run stops and writes nothing."""


def ordered(rtype, rec):
    return {k: rec[k] for k in SCHEMA["$defs"][rtype]["properties"] if k in rec}


def line(rtype, rec):
    return json.dumps(ordered(rtype, rec), ensure_ascii=False, separators=(",", ":"))


def read_jsonl(path):
    if not path.exists():
        return {}
    return {r["key"]: r for r in map(json.loads, path.read_text("utf-8").splitlines())}


def utc(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class Store:
    """Every record in the data repo, by type and key."""

    def __init__(self, repo):
        self.repo = Path(repo)
        self.recs = {t: {} for t in [*PER_SITE, *SHARED]}
        for rtype, d in PER_SITE.items():
            for f in sorted((self.repo / "data" / d).glob("*.jsonl")):
                self.recs[rtype].update(read_jsonl(f))
        for rtype, d in SHARED.items():
            self.recs[rtype].update(read_jsonl(self.repo / "data" / f"{d}.jsonl"))
        self.card_by_number = {c["number"]: k for k, c in self.recs["card"].items() if "number" in c}
        self.number_by_card = {k: n for n, k in self.card_by_number.items()}

    def card_key(self, number):
        key = self.card_by_number.get(number) or model.mint("card", number)
        self.card_by_number.setdefault(number, key)
        self.number_by_card.setdefault(key, number)
        return key

    def printing_key(self, locator_key):
        loc = self.recs["printing_locator"].get(locator_key)
        return loc["printing_key"] if loc else model.mint("prt", locator_key)

    def upsert(self, rtype, rec, t, counts):
        """Insert, or update keeping first_seen_at and widening last_seen_at.

        A change to anything but the seen-times counts as changed, so a re-run
        over the same pages, or a later run that only bumps last_seen_at, adds 0
        and changes 0.
        """
        old = self.recs[rtype].get(rec["key"])
        if old is None:
            new = dict(rec, first_seen_at=t, last_seen_at=t)
            counts["added"] += 1
        else:
            new = dict(rec, first_seen_at=min(old["first_seen_at"], t), last_seen_at=max(old["last_seen_at"], t))
            strip = lambda r: {k: v for k, v in r.items() if k not in SEEN}  # noqa: E731
            if strip(ordered(rtype, new)) != strip(old):
                counts["changed"] += 1
        self.recs[rtype][rec["key"]] = ordered(rtype, new)


def guard(site_dir, entry, prev):
    """Return (refusal arm or None, parsed blocks) for one logged series page."""
    page = site_dir / f"{entry['series_id']}.html"
    if entry.get("status") != 200 or not page.exists():
        return "http_error", []
    html = page.read_text("utf-8", "replace")
    blocks = parse_page(html)
    if not blocks:
        return "zero_parse", []
    if len(blocks) != independent_count(html):
        return "count_mismatch", blocks
    if prev and len(blocks) < DROP_FLOOR * prev["blocks"]:
        return "count_drop", blocks
    return None, blocks


def page_hash(blocks):
    """Over parsed blocks with the site-wide deploy stamp dropped, so a redeploy is not a page change."""
    plain = [dict(b, image_src=b["image_src"].split("?")[0]) for b in blocks]
    return hashlib.sha256(json.dumps(plain, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def run_site(store, site_dir, site, state):
    """Apply one site's pages to the store. Returns this site's ingest_run counts."""
    log = json.loads((site_dir / "_fetch_log.json").read_text("utf-8"))
    counts = defaultdict(int)
    refusals = {a: 0 for a in ARMS}
    page_refusals, refused_blocks, clean = {}, [], []
    for e in log:
        sid = e["series_id"]
        arm, blocks = guard(site_dir, e, state.get(sid))
        if e.get("status") == 200:
            counts["pages_fetched"] += 1
        if arm:
            refusals[arm] += 1
            page_refusals[sid] = arm
            refused_blocks += blocks
            continue
        for b in blocks:
            blank = [f for f in REQUIRED if not b[f]]
            if blank:
                raise RunError(f"{site}:{sid} {b['image_id']}: blank {blank} (layout change?)")
        clean.append((sid, e["label"], e["fetched_at"], blocks))

    new_products, touched = [], set()
    for sid, label, t, blocks in sorted(clean):
        product_key = f"{site}:{sid}"
        before = counts["added"]
        store.upsert("product", {"key": product_key, "site": site, **model.product_fields(site, sid, label)}, t, counts)
        if counts["added"] > before:
            new_products.append(product_key)
        h = page_hash(blocks)
        if state.get(sid, {}).get("hash") == h:
            counts["pages_unchanged"] += 1
        state[sid] = {"blocks": len(blocks), "hash": h}
        seen_here = set()
        for b in blocks:
            counts["blocks_parsed"] += 1
            if b["source_text"] == "":
                counts["blocks_without_source_text"] += 1
            locator_key = f"{site}:{b['image_id']}"
            try:
                loc, parsed = model.locator_fields(site, b["image_id"])
                if parsed["base"] != b["number"]:
                    raise ValueError(f"image id base {parsed['base']} != printed number {b['number']}")
                card_key = store.card_key(b["number"])
                prt = store.printing_key(locator_key)
                printing = {"key": prt, "card_key": card_key, "site": site, "rarity": b["rarity"],
                            "variant": model.VARIANT_BY_FAMILY[parsed["suffix_family"]],
                            "image_url": model.image_url(site, model.page_url(site, sid), b["image_src"]),
                            "source_text": b["source_text"]}
            except ValueError as ex:
                raise RunError(f"{site}:{sid} {b['image_id']}: {ex}") from ex
            if prt in touched:
                # Listed on a second page this run: the first page (lowest
                # series id) supplied its fields; only the seen-times widen.
                old = store.recs["printing"][prt]
                old["first_seen_at"], old["last_seen_at"] = min(old["first_seen_at"], t), max(old["last_seen_at"], t)
                store.recs["printing_locator"][locator_key]["last_seen_at"] = max(
                    store.recs["printing_locator"][locator_key]["last_seen_at"], t)
            else:
                touched.add(prt)
                store.upsert("printing", printing, t, counts)
                store.upsert("printing_locator", {"key": locator_key, "printing_key": prt, "site": site, **loc}, t, counts)
            listing = f"{prt}@{product_key}"
            seen_here.add(listing)
            store.upsert("printing_product", {"key": listing, "printing_key": prt, "product_key": product_key}, t, counts)
        for key, rec in store.recs["printing_product"].items():
            if rec["product_key"] == product_key and key not in seen_here and "removed_at" not in rec:
                rec["removed_at"] = t
                counts["removed"] += 1

    observe(store, site, clean, refused_blocks, page_refusals, counts)
    counts["new_products"] = new_products
    return counts, refusals, page_refusals


def observe(store, site, clean, refused_blocks, page_refusals, counts):
    """One current card_observation per (card, site), read from a deterministic block."""
    current = {}
    for key, o in store.recs["card_observation"].items():
        if o["site"] == site and "superseded_at" not in o:
            current[o["card_key"]] = o
    # A card with a printing on a refused page keeps its current observation:
    # reading it from a sibling instead would look like an erratum.
    refused_products = {f"{site}:{sid}" for sid in page_refusals}
    held = {b["number"] for b in refused_blocks}
    for rec in store.recs["printing_product"].values():
        if rec["product_key"] in refused_products and "removed_at" not in rec:
            held.add(store.number_by_card.get(store.recs["printing"][rec["printing_key"]]["card_key"]))
    by_number = defaultdict(list)
    for sid, _, t, blocks in clean:
        for b in blocks:
            by_number[b["number"]].append((model.sibling_order(b["image_id"], site), sid, t, b))
    for number, items in sorted(by_number.items()):
        _, _, t, b = min(items, key=lambda x: (x[0], x[1]))
        card_key = store.card_key(number)
        cur = current.get(card_key)
        if cur and number in held:
            continue
        try:
            fields = model.observation_fields(site, b)
        except ValueError as ex:
            raise RunError(f"{site} {b['image_id']}: {ex}") from ex
        h = model.observation_hash(fields)
        if cur and cur["observation_hash"] == h:
            cur["last_seen_at"] = max(cur["last_seen_at"], t)
            continue
        if cur:
            cur["superseded_at"] = t
            counts["changed"] += 1
        key = f"{card_key}:{site}:{h}"
        prior = store.recs["card_observation"].get(key)
        if prior:
            # The text went back to an earlier version: reopen that row.
            prior.pop("superseded_at", None)
            prior["last_seen_at"] = max(prior["last_seen_at"], t)
            counts["changed"] += 1
        else:
            store.recs["card_observation"][key] = ordered("card_observation", {
                "key": key, "card_key": card_key, "site": site, "lang": model.LANG[site],
                "observation_hash": h, **fields, "first_seen_at": t, "last_seen_at": t})
            counts["added"] += 1


def build_cards(store):
    """card rows from each card's current observations, jp first (CONTRACT.md)."""
    obs = defaultdict(dict)
    first = {}
    for o in store.recs["card_observation"].values():
        first[o["card_key"]] = min(first.get(o["card_key"], o["first_seen_at"]), o["first_seen_at"])
        if "superseded_at" not in o:
            obs[o["card_key"]][o["site"]] = o
    counts = defaultdict(int)
    for card_key, by_site in obs.items():
        facts_site = next(s for s in FACTS_ORDER if s in by_site)
        o = by_site[facts_site]
        old = store.recs["card"].get(card_key)
        rec = {"key": card_key, "number": store.number_by_card[card_key], "facts_site": facts_site,
               "first_seen_at": old["first_seen_at"] if old else first[card_key]}
        for k in ("category", "colors", "cost", "life", "power", "counter", "attributes", "block_icon"):
            if k in o:
                rec[k] = o[k]
        rec = ordered("card", rec)
        if old is None:
            counts["added"] += 1
        elif rec != old:
            counts["changed"] += 1
        store.recs["card"][card_key] = rec
    return counts


def validate(store):
    errors = []
    for rtype, recs in store.recs.items():
        v = Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{rtype}"})
        for key, rec in recs.items():
            for e in v.iter_errors(rec):
                errors.append(f"{rtype} {key}: {e.message}")
    if errors:
        raise RunError(f"{len(errors)} records fail the schema, first: {errors[:3]}")


def snapshot_cn(repo, rows):
    """Both cn ids (numeric id and printed cardNumber), sorted, diffed against the last snapshot."""
    path = Path(repo) / "state" / "cn_ids.jsonl"
    old = {}
    if path.exists():
        old = {r["id"]: r["cardNumber"] for r in map(json.loads, path.read_text("utf-8").splitlines())}
    new = {int(r["id"]): r["cardNumber"] for r in rows}
    diff = {"rows": len(new), "added": len(new.keys() - old.keys()), "gone": len(old.keys() - new.keys()),
            "renumbered": sum(1 for i in new.keys() & old.keys() if new[i] != old[i])}
    body = "".join(json.dumps({"id": i, "cardNumber": new[i]}, ensure_ascii=False, separators=(",", ":")) + "\n"
                   for i in sorted(new))
    return {path: body}, diff


def render(store, states, repo):
    """Every file this run owns, as {path: text}. Nothing is written yet."""
    repo = Path(repo)
    out = {}
    for rtype, d in PER_SITE.items():
        by_site = defaultdict(list)
        for r in store.recs[rtype].values():
            by_site[r["site"] if "site" in r else r["product_key"].split(":")[0]].append(r)
        for site, recs in by_site.items():
            out[repo / "data" / d / f"{site}.jsonl"] = "".join(line(rtype, r) + "\n" for r in sorted(recs, key=lambda r: r["key"]))
    for rtype, d in SHARED.items():
        if store.recs[rtype]:
            out[repo / "data" / f"{d}.jsonl"] = "".join(
                line(rtype, r) + "\n" for r in sorted(store.recs[rtype].values(), key=lambda r: r["key"]))
    for site, st in states.items():
        out[repo / "state" / "pages" / f"{site}.json"] = json.dumps(st, sort_keys=True, indent=1) + "\n"
    return out


def manifest(files, repo):
    """manifest.json over every data file, generated_at = the newest timestamp in the data."""
    repo = Path(repo)
    entries, newest = {}, ""
    for path, text in sorted(files.items()):
        rel = path.relative_to(repo).as_posix()
        if not rel.startswith("data/"):
            continue
        d = rel.split("/")[1].removesuffix(".jsonl")
        entries[rel] = {"type": TYPE_OF_DIR[d], "rows": text.count("\n"),
                        "sha256": hashlib.sha256(text.encode()).hexdigest()}
        for ln in text.splitlines():
            r = json.loads(ln)
            for k in ("last_seen_at", "first_seen_at", "observed_at"):
                if k in r:
                    newest = max(newest, r[k])
    return json.dumps({"schema_version": 1, "generated_at": newest, "files": entries},
                      ensure_ascii=False, indent=1) + "\n"


def write(files):
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, "utf-8", newline="\n")
        os.replace(tmp, path)


def run(pageset, repo, sites=SITES, cn_rows=None, now=time.time):
    pageset, repo = Path(pageset), Path(repo)
    store = Store(repo)
    states, results = {}, {}
    started = now()
    for site in sites:
        sp = repo / "state" / "pages" / f"{site}.json"
        states[site] = json.loads(sp.read_text("utf-8")) if sp.exists() else {}
        t0 = now()
        counts, refusals, page_refusals = run_site(store, pageset / site, site, states[site])
        results[site] = {"counts": counts, "refusals": refusals, "page_refusals": page_refusals, "t0": t0, "t1": now()}
    card_counts = build_cards(store)
    validate(store)
    files = render(store, states, repo)
    cn_diff = None
    if cn_rows is not None:
        cn_files, cn_diff = snapshot_cn(repo, cn_rows)
        files.update(cn_files)
    files[repo / "manifest.json"] = manifest(files, repo)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started))
    for site, r in results.items():
        c = r["counts"]
        run_rec = {"run_id": f"{stamp}-{site}", "site": site, "started_at": utc(r["t0"]), "finished_at": utc(r["t1"]),
                   "pages_fetched": c["pages_fetched"], "pages_unchanged": c["pages_unchanged"],
                   "blocks_parsed": c["blocks_parsed"], "blocks_without_source_text": c["blocks_without_source_text"],
                   "added": c["added"], "changed": c["changed"], "removed": c["removed"], "refusals": r["refusals"]}
        if c["new_products"]:
            run_rec["new_products"] = c["new_products"]
        errs = list(Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"],
                                          "$ref": "#/$defs/ingest_run"}).iter_errors(run_rec))
        if errs:
            raise RunError(f"ingest_run {site}: {errs[0].message}")
        r["run"] = run_rec
        rp = repo / "runs" / stamp[:4] / stamp[4:6] / f"{run_rec['run_id']}.json"
        # run_id has one-second resolution; a second run in the same second
        # would replace the first one's audit record. Refuse before writing.
        if rp.exists():
            raise RunError(f"{rp.relative_to(repo)} already exists (a run started in the same second); nothing written")
        files[rp] = json.dumps(ordered("ingest_run", run_rec), ensure_ascii=False, indent=1) + "\n"
    write(files)
    return {"sites": results, "cards": dict(card_counts), "cn": cn_diff}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pageset")
    ap.add_argument("repo")
    ap.add_argument("sites", nargs="*", default=SITES)
    ap.add_argument("--cn-list", help="cn list rows (cn.list_all output) as a JSON file")
    a = ap.parse_args(argv[1:])
    cn_rows = json.loads(Path(a.cn_list).read_text("utf-8")) if a.cn_list else None
    try:
        res = run(a.pageset, a.repo, a.sites, cn_rows)
    except RunError as ex:
        print(f"RUN STOPPED, nothing written: {ex}")
        return 1
    for site, r in res["sites"].items():
        run_rec = r["run"]
        print(f"{site}: pages {run_rec['pages_fetched']} (unchanged {run_rec['pages_unchanged']}), "
              f"blocks {run_rec['blocks_parsed']} (no source_text {run_rec['blocks_without_source_text']}), "
              f"added {run_rec['added']}, changed {run_rec['changed']}, removed {run_rec['removed']}, "
              f"refusals {run_rec['refusals']}")
        for sid, arm in sorted(r["page_refusals"].items()):
            print(f"  REFUSED {site}:{sid} {arm}")
    print(f"cards: {res['cards']}")
    if res["cn"]:
        print(f"cn snapshot: {res['cn']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
