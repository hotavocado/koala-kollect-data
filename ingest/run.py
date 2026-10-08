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
- A product's release_date comes from its site's product index (products.py),
  only where the page set carries one ({site}/products/_log.json). A date is
  sticky: a run sets or moves it and never clears it.
- Each promo printing's distribution mints from its card-list source_text, with
  an authoritative claim; the event and topic pages ({site}/events/_log.json)
  add one claim per printing, distribution and page (CONTRACT.md, Promo origin).
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

import events
import model
import products
from bandai import independent_count, parse_page

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema/v1.schema.json").read_text("utf-8"))
SITES = ["en", "asia-en", "jp", "tc"]
REGION = {"en": "en", "asia-en": "asia", "jp": "jp", "tc": "asia"}
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
        """Insert, or update keeping the earliest first_seen_at.

        A change to anything but first_seen_at counts as changed, so a re-run
        over the same pages adds 0 and changes 0 and writes the same bytes.
        """
        old = self.recs[rtype].get(rec["key"])
        if old is None:
            new = dict(rec, first_seen_at=t)
            counts["added"] += 1
        else:
            new = dict(rec, first_seen_at=min(old["first_seen_at"], t))
            strip = lambda r: {k: v for k, v in r.items() if k != "first_seen_at"}  # noqa: E731
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
    if prev and "blocks" in prev and len(blocks) < DROP_FLOOR * prev["blocks"]:
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
        # An older page replayed over newer data would supersede current facts
        # with stale ones, stamped earlier than what they replace. fetched_at in
        # state/pages is this page's fetch time in the last committed run, the
        # fetch time and not the last change, so a replay older than the last
        # fetch but newer than the last change is refused too. Equal is a re-run.
        last = state.get(sid, {}).get("fetched_at")
        if last and t < last:
            raise RunError(f"{site}:{sid} fetched {t}, older than the data ({last}); nothing written")
        before = counts["added"]
        product = {"key": product_key, "site": site, **model.product_fields(site, sid, label)}
        # The date is not on the card list page; it comes from the product
        # index after this loop. Carried over here so an upsert never drops it.
        for k in ("release_date", "release_date_source"):
            if k in store.recs["product"].get(product_key, {}):
                product[k] = store.recs["product"][product_key][k]
        store.upsert("product", product, t, counts)
        if counts["added"] > before:
            new_products.append(product_key)
        h = page_hash(blocks)
        if state.get(sid, {}).get("hash") == h:
            counts["pages_unchanged"] += 1
        state[sid] = {"blocks": len(blocks), "fetched_at": t, "hash": h}
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
                            "source_text": b["source_text"],
                            "block_icon": model.printing_block_icon(b["block_icon"])}
            except ValueError as ex:
                raise RunError(f"{site}:{sid} {b['image_id']}: {ex}") from ex
            if prt in touched:
                # Listed on a second page this run: the first page (lowest
                # series id) supplied its fields; only first_seen_at can move.
                for rtype, key in (("printing", prt), ("printing_locator", locator_key)):
                    old = store.recs[rtype][key]
                    old["first_seen_at"] = min(old["first_seen_at"], t)
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


def date_site(store, site, site_dir, pstate, counts):
    """Set release_date on this site's products from its product index. Returns the date counts.

    pstate is state/product_pages/{site}.json: the series each product page
    links, so a daily run need not read every product page again. It is
    updated in place with the pages this page set read.
    """
    try:
        items, fetched, index_at = products.read(site_dir, site)
    except (OSError, ValueError) as ex:
        raise RunError(f"{site} product index: {ex}") from None
    last = pstate.get("index_fetched_at")
    if last and index_at < last:
        raise RunError(f"{site} product index fetched {index_at}, older than the data ({last}); nothing written")
    pages = pstate.setdefault("pages", {})
    for href, (series, t) in fetched.items():
        pages[href] = {"fetched_at": t, "series": series}
    pstate["index_fetched_at"] = index_at
    links = {href: p["series"] for href, p in pages.items()}
    by_series = products.dates_by_series(items, links)
    out = {"dated": 0, "set": 0, "moved": 0, "kept": 0, "undated": [], "pages_read": len(fetched)}
    added = set(counts["new_products"])
    for key, p in sorted(store.recs["product"].items()):
        if p["site"] != site:
            continue
        if p["kind"] in products.UNDATED_KINDS:
            continue
        try:
            hit = products.retail_date(by_series.get(p["series_id"], []))
        except ValueError as ex:
            raise RunError(f"{key}: {ex}") from None
        if hit is None:
            # Sticky: a product that drops off the index keeps the date it had.
            if "release_date" in p:
                out["dated"] += 1
                out["kept"] += 1
            else:
                out["undated"].append(f"{key} {p['code']}" if p.get("code") else key)
            continue
        date, source = hit
        out["dated"] += 1
        if (p.get("release_date"), p.get("release_date_source")) == (date, source):
            continue
        out["moved" if "release_date" in p else "set"] += 1
        store.recs["product"][key] = ordered("product", dict(p, release_date=date, release_date_source=source))
        if key not in added:
            counts["changed"] += 1
    return out


def claim_key(printing, dist, source, url):
    return "ev_" + hashlib.sha256(f"{printing}|{dist}|{source}|{url}".encode()).hexdigest()[:16]


def put(store, rtype, rec, counts):
    """Insert or replace one distribution or claim. A claim keeps its earliest observed_at."""
    old = store.recs[rtype].get(rec["key"])
    if old is not None and "observed_at" in old:
        rec = dict(rec, observed_at=min(old["observed_at"], rec["observed_at"]))
    rec = ordered(rtype, rec)
    if old is None:
        counts["added"] += 1
    elif rec != old:
        counts["changed"] += 1
    store.recs[rtype][rec["key"]] = rec


def with_dates(rec, dates):
    if dates:
        rec["starts_on"] = dates[0]
        if dates[1]:
            rec["ends_on"] = dates[1]
    return rec


def link_site(store, site, counts):
    """Mint each promo printing's distribution from its card-list source_text. Returns {source_text: [printing_key]}.

    The card list is the authority on which pack a printing came from (alyssa
    87163), so this claim is authoritative and needs no page: it is read from
    the store. One distribution per (site, source_text).
    """
    by_name = defaultdict(list)
    prods = store.recs["product"]
    for pp in sorted(store.recs["printing_product"].values(), key=lambda r: r["key"]):
        prod = prods.get(pp["product_key"])
        if "removed_at" in pp or prod is None or prod["site"] != site or prod["kind"] != "promo_bucket":
            continue
        prt = store.recs["printing"].get(pp["printing_key"])
        text = prt.get("source_text") if prt else None
        if not text:
            continue
        dist = model.mint("dist", f"{site}|{text}")
        name = events.core(text)
        put(store, "distribution", {"key": dist, "site": site, "region": REGION[site],
                                    "kind": events.kind_of(name), "name": name}, counts)
        rec = {"key": claim_key(prt["key"], dist, "official_cardlist", prod["product_url"]), "printing_key": prt["key"],
               "distribution_key": dist, "source": "official_cardlist", "source_url": prod["product_url"],
               "quote": text, "confidence": "authoritative", "observed_at": pp["first_seen_at"]}
        put(store, "printing_distribution", with_dates(rec, events.name_dates(text)), counts)
        if prt["key"] not in by_name[text]:
            by_name[text].append(prt["key"])
    return by_name


def events_site(store, site, site_dir, estate, by_name, counts):
    """Claims from the event and topic pages this page set read. Returns the counts.

    One claim per (printing, distribution, page): the line that names the
    pack, the tier above it, the quantity on it and the page's date.
    Authoritative when the line prints the card list's string (give or take
    spacing and the brand); inferred, for review, when it only matches with
    width, case and punctuation folded. A card block whose heading names pack
    A and shows a card whose promo printings the card list puts in no pack A is
    an inferred claim of A on those printings: the page contradicts the list.

    estate is state/event_pages/{site}.json, updated in place: the pages read,
    so a daily run fetches only new pages and the current list.
    """
    try:
        pages, _current, index_at = events.read(site_dir, site)
    except (OSError, ValueError) as ex:
        raise RunError(f"{site} events: {ex}") from None
    last = estate.get("index_fetched_at")
    if last and index_at < last:
        raise RunError(f"{site} event lists fetched {index_at}, older than the data ({last}); nothing written")
    names = {t: m for t in by_name if (m := events.matcher(t))}
    dist_of = {t: model.mint("dist", f"{site}|{t}") for t in names}
    promo_by_card = defaultdict(list)
    for t, prts in by_name.items():
        for p in prts:
            promo_by_card[store.recs["printing"][p]["card_key"]].append((p, t))
    out = {"pages_read": len(pages), "pages_dated": 0, "claims": 0, "inferred": 0, "contradictions": 0}
    seen_pages = estate.setdefault("pages", {})
    for href, (html, t) in sorted(pages.items()):
        seen_pages[href] = t
        lines = events.lines_of(html)
        dates = events.event_dates(lines)
        out["pages_dated"] += bool(dates)
        source = "official_event" if "/events/" in href else "official_topic"

        def claim(prt, text, quote, conf, tier=None, qty=None):
            rec = {"key": claim_key(prt, dist_of[text], source, href), "printing_key": prt,
                   "distribution_key": dist_of[text], "source": source, "source_url": href, "quote": quote,
                   "confidence": conf, "observed_at": t}
            if tier:
                rec["tier"] = tier
            if qty:
                rec["quantity_note"] = qty
            put(store, "printing_distribution", with_dates(rec, dates), counts)
            out["claims"] += 1
            out["inferred"] += conf == "inferred"

        named = set()
        for c in events.claims(lines, names):
            if c["name"] in named:
                continue
            named.add(c["name"])
            for prt in by_name[c["name"]]:
                claim(prt, c["name"], c["line"], "authoritative" if c["match"] == "exact" else "inferred",
                      c["tier"], c["quantity"])
        for head, nums in events.card_blocks(html):
            hits = [n for n in names if events.find(head, names[n])]
            if not hits:
                continue
            pack = max(hits, key=len)
            for num in nums:
                card = store.card_by_number.get(num)
                prts = promo_by_card.get(card, [])
                if prts and all(text != pack for _, text in prts):
                    out["contradictions"] += 1
                    for prt, _ in prts:
                        claim(prt, pack, head, "inferred")
    estate["index_fetched_at"] = index_at
    return out


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
            continue
        key = f"{card_key}:{site}:{h}"
        if cur:
            cur["superseded_at"] = t
            counts["changed"] += 1
        prior = store.recs["card_observation"].get(key)
        if prior:
            # The text went back to an earlier version: reopen that row.
            prior.pop("superseded_at", None)
            counts["changed"] += 1
        else:
            store.recs["card_observation"][key] = ordered("card_observation", {
                "key": key, "card_key": card_key, "site": site, "lang": model.LANG[site],
                "observation_hash": h, **fields, "first_seen_at": t})
            counts["added"] += 1


def build_cards(store):
    """card rows from each card's current observations, jp first (CONTRACT.md)."""
    obs = defaultdict(dict)
    first = {}
    for o in store.recs["card_observation"].values():
        first[o["card_key"]] = min(first.get(o["card_key"], o["first_seen_at"]), o["first_seen_at"])
        if "superseded_at" not in o:
            obs[o["card_key"]][o["site"]] = o
    # card.block_icon is derived (CONTRACT.md): the number printed on the facts
    # site's base printing; with no base there, the value every one of that
    # site's printings of the card agrees on. Never "X"; omitted when the base
    # shows none or the printings disagree, because then no card-level value is honest.
    on_site = defaultdict(list)
    for p in store.recs["printing"].values():
        on_site[(p["card_key"], p["site"])].append(p)
    base_icon = {}
    for k, ps in on_site.items():
        base = [p for p in ps if p["variant"] == "base"]
        values = {json.dumps(p.get("block_icon")) for p in (base or ps)}
        v = json.loads(values.pop()) if len(values) == 1 else None
        if isinstance(v, int):
            base_icon[k] = v
    counts = defaultdict(int)
    for card_key, by_site in obs.items():
        facts_site = next(s for s in FACTS_ORDER if s in by_site)
        o = by_site[facts_site]
        old = store.recs["card"].get(card_key)
        rec = {"key": card_key, "number": store.number_by_card[card_key], "facts_site": facts_site,
               "first_seen_at": old["first_seen_at"] if old else first[card_key]}
        for k in ("category", "colors", "cost", "life", "power", "counter", "attributes"):
            if k in o:
                rec[k] = o[k]
        if (card_key, facts_site) in base_icon:
            rec["block_icon"] = base_icon[(card_key, facts_site)]
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


def render(store, states, repo, product_states=None, event_states=None):
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
    for site, st in (product_states or {}).items():
        out[repo / "state" / "product_pages" / f"{site}.json"] = json.dumps(st, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    for site, st in (event_states or {}).items():
        out[repo / "state" / "event_pages" / f"{site}.json"] = json.dumps(st, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
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
            for k in ("first_seen_at", "removed_at", "superseded_at", "observed_at"):
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
    # The committed data must already meet the schema. A row the schema refuses
    # (a legacy last_seen_at or observation block_icon) stops the run before any
    # fetch is read, so it can never be rewritten without its field quietly.
    try:
        validate(store)
    except RunError as ex:
        raise RunError(f"committed data: {ex}") from None
    states, product_states, event_states, results = {}, {}, {}, {}
    started = now()
    for site in sites:
        sp = repo / "state" / "pages" / f"{site}.json"
        states[site] = json.loads(sp.read_text("utf-8")) if sp.exists() else {}
        t0 = now()
        counts, refusals, page_refusals = run_site(store, pageset / site, site, states[site])
        dates = None
        if (pageset / site / "products" / "_log.json").exists():
            pp = repo / "state" / "product_pages" / f"{site}.json"
            product_states[site] = json.loads(pp.read_text("utf-8")) if pp.exists() else {}
            dates = date_site(store, site, pageset / site, product_states[site], counts)
        by_name = link_site(store, site, counts)
        origin = None
        if (pageset / site / "events" / "_log.json").exists():
            ep = repo / "state" / "event_pages" / f"{site}.json"
            event_states[site] = json.loads(ep.read_text("utf-8")) if ep.exists() else {}
            origin = events_site(store, site, pageset / site, event_states[site], by_name, counts)
        results[site] = {"counts": counts, "refusals": refusals, "page_refusals": page_refusals, "dates": dates,
                         "distributions": len(by_name), "origin": origin, "t0": t0, "t1": now()}
    card_counts = build_cards(store)
    validate(store)
    files = render(store, states, repo, product_states, event_states)
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


def date_line(d):
    if d is None:
        return "release dates: not in this page set, none read"
    return (f"release dates: dated {d['dated']} (set {d['set']}, moved {d['moved']}, kept off the index {d['kept']}), "
            f"undated {len(d['undated'])}{' [' + ', '.join(d['undated']) + ']' if d['undated'] else ''}, "
            f"product pages read {d['pages_read']}")


def origin_line(dists, o):
    head = f"distributions from the card list: {dists}"
    if o is None:
        return f"{head}; event pages: not in this page set, none read"
    return (f"{head}; event pages read {o['pages_read']} (dated {o['pages_dated']}), claims {o['claims']} "
            f"(inferred, for review {o['inferred']}, of them card-block contradictions {o['contradictions']})")


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
        print(f"  {date_line(r['dates'])}")
        print(f"  {origin_line(r['distributions'], r['origin'])}")
    print(f"cards: {res['cards']}")
    if res["cn"]:
        print(f"cn snapshot: {res['cn']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
