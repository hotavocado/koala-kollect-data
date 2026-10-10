"""Step 3: identity, upsert, ingest_run and the removal guard. Writes the data repo.

usage: python run.py <pageset_dir> <data_repo> [site ...]

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
- DON cards mint from tcgcsv/ in the page set when it is there (Mike 86520;
  run_tcgcsv, CONTRACT.md DON): one card per normal DON product, one printing
  per product and finish, keyed tcgcsv:{productId}:{subType}.
- A product's release_date comes from its site's product index (products.py),
  only where the page set carries one ({site}/products/_log.json). A date is
  sticky: a run sets or moves it and never clears it.
- Each promo printing's distribution mints from its card-list source_text, with
  an authoritative claim; the event and topic pages ({site}/events/_log.json)
  add one claim per printing, distribution and page (CONTRACT.md, Promo origin).
- cn comes from cn/ in the page set when it is there (cn.fetch_pageset): the
  list, the products and a detail per id the store lacks (run_cn, CONTRACT.md cn).
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

import cnmodel
import events
import model
import products
import tcgcsv
from bandai import independent_count, parse_page

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema/v1.schema.json").read_text("utf-8"))
SITES = ["en", "asia-en", "jp", "tc"]
REGION = {"en": "en", "asia-en": "asia", "jp": "jp", "tc": "asia", "cn": "cn"}
FACTS_ORDER = ["jp", "en", "asia-en", "tc", "cn", "tcgcsv"]
ARMS = ["http_error", "zero_parse", "count_drop", "count_mismatch"]
DROP_FLOOR = 0.9
REQUIRED = ["number", "rarity", "category", "name", "image_src"]
# Per-site record types and their directory under data/ (CONTRACT.md layout).
PER_SITE = {"card_observation": "card_observations", "printing": "printings",
            "printing_locator": "printing_locators", "product": "products",
            "printing_product": "printing_products"}
SHARED = {"card": "cards", "distribution": "distributions",
          "printing_distribution": "printing_distributions", "printing_link": "printing_links",
          "retired_printing": "retired_printings"}
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
        self.design_by_card = {k: c["don_design"] for k, c in self.recs["card"].items() if "don_design" in c}

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
        if rtype == "printing" and rec["key"] in self.recs["retired_printing"]:
            # A retired printing never comes back under its old key
            # (data/retired_printings.jsonl, CONTRACT.md).
            raise RunError(f"printing {rec['key']} is retired ({self.recs['retired_printing'][rec['key']]['reason']}); nothing written")
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
    packs = set()
    if site in products.DOUBLE_PACK_SITES:
        # Minted from the index item itself, dated from it on every run, so a
        # moved date updates. A pack that drops off the index is dated below
        # like any product: it keeps the date it had.
        try:
            items_by_code, undated_packs = products.double_packs(items)
        except ValueError as ex:
            raise RunError(f"{site} product index: {ex}") from None
        out["double_packs"], out["double_packs_undated"] = len(items_by_code), undated_packs
        for code, it in sorted(items_by_code.items()):
            key = f"{site}:{code}"
            old = store.recs["product"].get(key)
            rec = {"key": key, "site": site, "series_id": code, "code": code, "name": it["title"], "kind": "other",
                   "release_date": it["date"], "release_date_source": it["href"], "product_url": it["href"]}
            store.upsert("product", rec, index_at, counts)
            packs.add(key)
            out["dated"] += 1
            if old is None:
                counts["new_products"].append(key)
                out["set"] += 1
            elif old.get("release_date") != it["date"]:
                out["moved"] += 1
    added = set(counts["new_products"])
    for key, p in sorted(store.recs["product"].items()):
        if p["site"] != site or key in packs:
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


def cn_ids(store):
    """{printing key: its cn id}. A duplicate cn id is a second locator on one printing (run_cn), so the lowest id names it."""
    ids = {}
    for r in store.recs["printing_locator"].values():
        if r["site"] == "cn":
            ids[r["printing_key"]] = min(ids.get(r["printing_key"], int(r["image_id"])), int(r["image_id"]))
    return ids


def link_site(store, site, counts):
    """Mint each promo printing's distribution from its card-list source_text. Returns {source_text: [printing_key]}.

    The card list is the authority on which pack a printing came from (alyssa
    87163), so this claim is authoritative and needs no page: it is read from
    the store. One distribution per (site, source_text).
    """
    by_name = defaultdict(list)
    prods = store.recs["product"]
    ids = cn_ids(store)
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
        # cn has no card-list page per product; its card list is the detail call.
        url = prod.get("product_url") or cnmodel.DETAIL_URL.format(ids[prt["key"]])
        rec = {"key": claim_key(prt["key"], dist, "official_cardlist", url), "printing_key": prt["key"],
               "distribution_key": dist, "source": "official_cardlist", "source_url": url,
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
        rec = {"key": card_key, "facts_site": facts_site,
               "first_seen_at": old["first_seen_at"] if old else first[card_key]}
        if card_key in store.number_by_card:
            rec["number"] = store.number_by_card[card_key]
        else:
            rec["don_design"] = store.design_by_card[card_key]
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


def run_cn(store, cn_dir, state):
    """Apply the cn page set to the store. Returns this run's cn counts.

    cn_dir is cn.fetch_pageset's output. state is state/cn.json, updated in
    place. The list is complete or absent (cn.list_all refuses a short one), so
    an id that leaves it stamps removed_at on its listing, unless the list fell
    more than 10% below the last committed one (count_drop): then nothing is
    removed. A listed id with no detail and no printing yet is refused
    (http_error) and comes in on a later run. A detail is read only for ids the
    store lacks, so cn errata show only on a full re-read (CONTRACT.md, cn).
    """
    lst = json.loads((cn_dir / "_list.json").read_text("utf-8"))
    prods = json.loads((cn_dir / "_products.json").read_text("utf-8"))
    t = lst["fetched_at"]
    # Stale guard: an older list replayed over newer data would stamp removals
    # and listings from the past. Equal is a re-run.
    last = state.get("list_fetched_at")
    if last and t < last:
        raise RunError(f"cn list fetched {t}, older than the data ({last}); nothing written")
    rows = lst["rows"]
    counts = defaultdict(int)
    refusals = {a: 0 for a in ARMS}
    if not rows:
        refusals["zero_parse"] += 1
        return counts, refusals, {"rows": 0}
    if len({r["id"] for r in rows}) != len(rows):
        raise RunError(f"cn list: {len(rows)} rows but {len({r['id'] for r in rows})} unique ids")
    drop = bool(state.get("rows")) and len(rows) < DROP_FLOOR * state["rows"]
    if drop:
        refusals["count_drop"] += 1
    by_name = {}
    new_products = []
    for p in prods["products"]:
        key = f"cn:{p['id']}"
        rec = {"key": key, "site": "cn", **cnmodel.product_fields(p)}
        before = counts["added"]
        store.upsert("product", rec, prods["fetched_at"], counts)
        if counts["added"] > before:
            new_products.append(key)
        if p["name"] in by_name:
            raise RunError(f"cn: two products named {p['name']!r}")
        by_name[p["name"]] = key

    # cn sometimes saves one printing twice: two ids whose list rows and details
    # agree apart from the id and the save times (cnmodel.duplicate_signature).
    # The later id becomes a second locator on the lowest id's printing and
    # mints nothing. Read only where both details are in the page set;
    # cn.fetch_pageset fetches a held twin's detail when a new id's list row
    # matches it, so a new double-save is always read.
    first, dup_of = {}, {}
    for r in sorted(rows, key=lambda r: r["id"]):
        dpath = cn_dir / "detail" / f"{r['id']}.json"
        if dpath.exists():
            sig = cnmodel.duplicate_signature(r, json.loads(dpath.read_text("utf-8"))["info"])
            if sig in first:
                dup_of[r["id"]] = first[sig]
            else:
                first[sig] = r["id"]

    seen, details = set(), {}
    for r in sorted(rows, key=lambda r: r["id"]):
        counts["blocks_parsed"] += 1
        loc_key = f"cn:{r['id']}"
        product_key = by_name.get(r["cardOfferType"])
        if product_key is None:
            raise RunError(f"{loc_key}: list names product {r['cardOfferType']!r}, which cn's product list does not carry")
        try:
            base, token = cnmodel.split_number(r["cardNumber"])
        except ValueError as ex:
            raise RunError(f"{loc_key}: {ex}") from ex
        if r["id"] in dup_of:
            prt = store.printing_key(f"cn:{dup_of[r['id']]}")
            own = store.recs["printing_locator"].get(loc_key, {}).get("printing_key")
            if own not in (None, prt) and own in store.recs["printing"]:
                # A printing already minted for a duplicate leaves the data only
                # through a reviewed PR (CONTRACT.md, Write rules), never on its own.
                raise RunError(f"{loc_key} duplicates cn:{dup_of[r['id']]} but has its own printing {own}; "
                               f"remove it by a reviewed PR; nothing written")
            dt = json.loads((cn_dir / "detail" / f"{r['id']}.json").read_text("utf-8"))["fetched_at"]
            counts["pages_fetched"] += 1
            store.upsert("printing_locator", {"key": loc_key, "printing_key": prt, "site": "cn", "image_id": str(r["id"])},
                         dt, counts)
            listing = f"{prt}@{product_key}"
            seen.add(listing)
            store.upsert("printing_product", {"key": listing, "printing_key": prt, "product_key": product_key}, t, counts)
            continue
        prt = store.printing_key(loc_key)
        dpath = cn_dir / "detail" / f"{r['id']}.json"
        if dpath.exists():
            d = json.loads(dpath.read_text("utf-8"))
            info, dt = d["info"], d["fetched_at"]
            counts["pages_fetched"] += 1
            try:
                dbase, _ = cnmodel.split_number(info.get("cardNumber"))
                if dbase != base:
                    raise ValueError(f"detail number {info.get('cardNumber')!r} != list number {r['cardNumber']!r}")
                url = model.image_url("cn", r["cardImg"], r["cardImg"])
                img = cnmodel.image_token(url, base)
                marker = cnmodel.has_marker(info.get("cardName"))
                variant = cnmodel.variant(token, img, marker)
                source_text = cnmodel.source_text(info)
                printing = {"key": prt, "card_key": store.card_key(base), "site": "cn",
                            "rarity": cnmodel.rarity(info.get("cardRarity"), r["id"]), "variant": variant,
                            "image_url": url, "source_text": source_text,
                            "block_icon": cnmodel.block_icon(info.get("subscript"))}
            except ValueError as ex:
                raise RunError(f"{loc_key}: {ex}") from ex
            if token:
                printing["number_token"] = token
            if img:
                printing["image_token"] = img
            if not cnmodel.known_token(token):
                counts["unknown_tokens"] += 1
            if cnmodel.disagree(token, img, marker):
                counts["variant_disagreements"] += 1
            if source_text == "":
                counts["blocks_without_source_text"] += 1
            store.upsert("printing", printing, dt, counts)
            store.upsert("printing_locator", {"key": loc_key, "printing_key": prt, "site": "cn", "image_id": str(r["id"])},
                         dt, counts)
            details[prt] = (base, info, dt)
        elif prt not in store.recs["printing"]:
            refusals["http_error"] += 1
            continue
        listing = f"{prt}@{product_key}"
        seen.add(listing)
        store.upsert("printing_product", {"key": listing, "printing_key": prt, "product_key": product_key}, t, counts)
    if not drop:
        for key, rec in store.recs["printing_product"].items():
            if rec["product_key"].startswith("cn:") and key not in seen and "removed_at" not in rec:
                rec["removed_at"] = t
                counts["removed"] += 1
    # Every listed id that is a second locator on a lower id's printing, named
    # on every run, so a new double-save shows as a new id in the list.
    ids_by_prt = defaultdict(list)
    for r in rows:
        loc = store.recs["printing_locator"].get(f"cn:{r['id']}")
        if loc:
            ids_by_prt[loc["printing_key"]].append(r["id"])
    counts["duplicate_ids"] = sorted(i for ids in ids_by_prt.values() for i in sorted(ids)[1:])

    # One observation per number, from its preferred printing over every cn
    # printing the store knows: if that printing's detail is not in this run,
    # the current observation stands (reading a sibling would look like an erratum).
    cn_by_card = defaultdict(list)
    for k, p in store.recs["printing"].items():
        if p["site"] == "cn":
            cn_by_card[p["card_key"]].append(k)
    cn_id = cn_ids(store)

    def preference(k):
        # base before parallel, then the lowest numeric id
        return (store.recs["printing"][k]["variant"] != "base", cn_id[k])
    current = {o["card_key"]: o for o in store.recs["card_observation"].values()
               if o["site"] == "cn" and "superseded_at" not in o}
    for card_key, prts in sorted(cn_by_card.items()):
        best = min(prts, key=preference)
        if best not in details:
            continue
        _, info, dt = details[best]
        try:
            fields = cnmodel.observation_fields(info)
        except ValueError as ex:
            raise RunError(f"cn:{cn_id[best]}: {ex}") from ex
        h = model.observation_hash(fields)
        cur = current.get(card_key)
        if cur and cur["observation_hash"] == h:
            continue
        key = f"{card_key}:cn:{h}"
        if cur:
            cur["superseded_at"] = dt
            counts["changed"] += 1
        prior = store.recs["card_observation"].get(key)
        if prior:
            prior.pop("superseded_at", None)
            counts["changed"] += 1
        else:
            store.recs["card_observation"][key] = ordered("card_observation", {
                "key": key, "card_key": card_key, "site": "cn", "lang": model.LANG["cn"],
                "observation_hash": h, **fields, "first_seen_at": dt})
            counts["added"] += 1
    state["list_fetched_at"] = t
    if not drop:
        state["rows"] = len(rows)
    counts["new_products"] = new_products
    return counts, refusals, {"rows": len(rows)}


def run_tcgcsv(store, tcg_dir, state):
    """Apply the tcgcsv page set: DON cards, their observations and printings. Returns this run's counts.

    tcg_dir is tcgcsv.fetch_pageset's output; state is state/tcgcsv.json,
    updated in place. A card is one normal DON product (alyssa 87481), reached
    first through that product's existing locators, then through its
    don_design, and minted from the don_design only when neither matches, so a
    TCGplayer rename never mints a second card. tcgcsv has no product rows and
    no listings, so nothing is ever removed here. A DON with no price row has no
    finish and so no key: it is refused, counted, and mints when a price row
    appears (upper 87490). A product TCGplayer has no image for (imageCount 0)
    gets no image_url, is counted in no_image, and gains one the day the count
    turns positive (upper 87556). The Release Event stamps in tcgcsv.RE_GROUPS
    are printings on existing set cards, with one distribution per group and a
    claim per printing (CONTRACT.md, Release Event stamps).
    """
    try:
        t, groups, prods, prices, times = tcgcsv.read_pageset(tcg_dir)
        specs, unpriced, don_count = tcgcsv.plan(groups, prods, prices)
        stamps, stamp_unpriced, stamp_count = tcgcsv.plan_stamps(groups, prods, prices)
    except ValueError as ex:
        raise RunError(f"tcgcsv: {ex}") from ex
    # Stale guard, as on cn: an older page set replayed over newer data. Equal is a re-run.
    last = state.get("groups_fetched_at")
    if last and t < last:
        raise RunError(f"tcgcsv groups fetched {t}, older than the data ({last}); nothing written")
    counts = defaultdict(int)
    counts["pages_fetched"] = len(prods) + len(prices)
    counts["blocks_parsed"] = don_count + stamp_count
    counts["dons"], counts["stamps"] = don_count, stamp_count
    counts["unpriced"] = sorted(unpriced + stamp_unpriced)
    counts["no_image"] = sorted({sp["product_id"] for sp in [*specs, *stamps] if not sp["has_image"]})
    counts["unmatched"], counts["rarity_disagreements"] = [], []
    refusals = {a: 0 for a in ARMS}
    if not don_count:
        refusals["zero_parse"] += 1
        return counts, refusals

    card_of_product = {}
    for loc in store.recs["printing_locator"].values():
        if loc["site"] == "tcgcsv":
            pid = int(loc["image_id"].split(":")[0])
            card_of_product[pid] = store.recs["printing"][loc["printing_key"]]["card_key"]
    card_of_design = {d: k for k, d in store.design_by_card.items()}
    product_of_card, names = {}, {}
    for sp in specs:
        cp = sp["card_product_id"]
        # The normal's locators first; then this product's own, since a gold whose
        # normal is unpriced is the only locator its card has (codex, PR 15).
        card_key = (card_of_product.get(cp) or card_of_product.get(sp["product_id"])
                    or card_of_design.get(sp["design"]) or model.mint("card", sp["design"]))
        if product_of_card.setdefault(card_key, cp) != cp:
            raise RunError(f"tcgcsv: products {product_of_card[card_key]} and {cp} resolve to one card "
                           f"({card_key}, {sp['design']}); one normal product is one card")
        card_of_product.setdefault(cp, card_key)
        # don_design is set once, at mint, and never rewritten.
        store.design_by_card.setdefault(card_key, sp["design"])
        card_of_design.setdefault(store.design_by_card[card_key], card_key)
        dt = times[sp["group_id"]]
        if sp["product_id"] == cp:
            names[card_key] = (sp["name"], dt)
        loc_key = f"tcgcsv:{sp['product_id']}:{sp['sub_type']}"
        prt = store.printing_key(loc_key)
        printing = {"key": prt, "card_key": card_key, "site": "tcgcsv", "rarity": "DON", "variant": sp["variant"],
                    "source_text": sp["name"], "block_icon": None}
        if sp["has_image"]:
            printing["image_url"] = tcgcsv.image_url(sp["product_id"])
        if sp["product_id"] in tcgcsv.PROVENANCE:
            printing["provenance_url"] = tcgcsv.PROVENANCE[sp["product_id"]]
        store.upsert("printing", printing, dt, counts)
        store.upsert("printing_locator", {"key": loc_key, "printing_key": prt, "site": "tcgcsv",
                                          "image_id": f"{sp['product_id']}:{sp['sub_type']}"}, dt, counts)
    # A gold whose normal is unpriced still names its card from the normal product.
    for sp in specs:
        card_key = card_of_product[sp["card_product_id"]]
        if card_key not in names:
            normal = next(p for p in prods[sp["group_id"]] if p["productId"] == sp["card_product_id"])
            names[card_key] = (normal["name"], times[sp["group_id"]])

    # Release Event stamps (CONTRACT.md): a printing on the set card its Number
    # names, never a card of its own and never an observation. A Number that is
    # no card of ours is refused and counted. The rarity is TCGplayer's,
    # verbatim; where it differs from the card's en base printing it is counted.
    en_rarity = defaultdict(set)
    for p in store.recs["printing"].values():
        if p["site"] == "en" and p["variant"] == "base":
            en_rarity[p["card_key"]].add(p["rarity"])
    for st in stamps:
        card_key = store.card_by_number.get(st["number"])
        if card_key is None:
            counts["unmatched"].append(st["product_id"])
            continue
        if en_rarity.get(card_key) and st["rarity"] not in en_rarity[card_key]:
            counts["rarity_disagreements"].append(st["product_id"])
        dt = times[st["group_id"]]
        loc_key = f"tcgcsv:{st['product_id']}:{st['sub_type']}"
        prt = store.printing_key(loc_key)
        printing = {"key": prt, "card_key": card_key, "site": "tcgcsv", "rarity": st["rarity"],
                    "variant": st["variant"], "source_text": st["name"], "block_icon": None}
        if st["has_image"]:
            printing["image_url"] = tcgcsv.image_url(st["product_id"])
        store.upsert("printing", printing, dt, counts)
        store.upsert("printing_locator", {"key": loc_key, "printing_key": prt, "site": "tcgcsv",
                                          "image_id": f"{st['product_id']}:{st['sub_type']}"}, dt, counts)
        # One distribution per RE group, and the group's own listing as the claim.
        # No starts_on or ends_on: tcgcsv is never a date source.
        dist = model.mint("dist", f"tcgcsv|{st['group_name']}")
        url = f"{tcgcsv.BASE}/{st['group_id']}/products"
        put(store, "distribution", {"key": dist, "site": "tcgcsv", "region": "en", "kind": "event_pack",
                                    "name": st["group_name"], "source_url": url}, counts)
        put(store, "printing_distribution", {
            "key": claim_key(prt, dist, "tcgcsv", url), "printing_key": prt, "distribution_key": dist,
            "source": "tcgcsv", "source_url": url, "quote": st["group_name"], "confidence": "corroborated",
            "observed_at": dt}, counts)

    # One observation per card: the normal product's name as TCGplayer lists it
    # (alyssa 87523). A rename supersedes it, the same as an erratum.
    current = {o["card_key"]: o for o in store.recs["card_observation"].values()
               if o["site"] == "tcgcsv" and "superseded_at" not in o}
    for card_key, (name, dt) in sorted(names.items()):
        fields = {"name": name, "category": "don", "colors": [], "attributes": [], "types": []}
        h = model.observation_hash(fields)
        cur = current.get(card_key)
        if cur and cur["observation_hash"] == h:
            continue
        key = f"{card_key}:tcgcsv:{h}"
        if cur:
            cur["superseded_at"] = dt
            counts["changed"] += 1
        prior = store.recs["card_observation"].get(key)
        if prior:
            prior.pop("superseded_at", None)
            counts["changed"] += 1
        else:
            store.recs["card_observation"][key] = ordered("card_observation", {
                "key": key, "card_key": card_key, "site": "tcgcsv", "lang": "en",
                "observation_hash": h, **fields, "first_seen_at": dt})
            counts["added"] += 1
    state["groups_fetched_at"] = t
    return counts, refusals


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


def render(store, states, repo, product_states=None, event_states=None, cn_state=None, tcg_state=None):
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
    if cn_state is not None:
        out[repo / "state" / "cn.json"] = json.dumps(cn_state, sort_keys=True, indent=1) + "\n"
    if tcg_state is not None:
        out[repo / "state" / "tcgcsv.json"] = json.dumps(tcg_state, sort_keys=True, indent=1) + "\n"
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
            for k in ("first_seen_at", "removed_at", "superseded_at", "observed_at", "retired_at"):
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


def run(pageset, repo, sites=SITES, now=time.time):
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
    cn_dir, cn_state, cn_result, cn_rows = pageset / "cn", None, None, None
    if (cn_dir / "_list.json").exists():
        sp = repo / "state" / "cn.json"
        cn_state = json.loads(sp.read_text("utf-8")) if sp.exists() else {}
        t0 = now()
        counts, refusals, _ = run_cn(store, cn_dir, cn_state)
        by_name = link_site(store, "cn", counts)
        cn_result = {"counts": counts, "refusals": refusals, "page_refusals": {}, "distributions": len(by_name),
                     "t0": t0, "t1": now()}
        cn_rows = json.loads((cn_dir / "_list.json").read_text("utf-8"))["rows"]
    tcg_dir, tcg_state, tcg_result = pageset / "tcgcsv", None, None
    if (tcg_dir / "_groups.json").exists():
        sp = repo / "state" / "tcgcsv.json"
        tcg_state = json.loads(sp.read_text("utf-8")) if sp.exists() else {}
        t0 = now()
        counts, refusals = run_tcgcsv(store, tcg_dir, tcg_state)
        tcg_result = {"counts": counts, "refusals": refusals, "page_refusals": {}, "t0": t0, "t1": now()}
    card_counts = build_cards(store)
    validate(store)
    files = render(store, states, repo, product_states, event_states, cn_state, tcg_state)
    cn_diff = None
    if cn_rows is not None:
        cn_files, cn_diff = snapshot_cn(repo, cn_rows)
        files.update(cn_files)
    files[repo / "manifest.json"] = manifest(files, repo)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started))
    runs = dict(results, **({"cn": cn_result} if cn_result else {}), **({"tcgcsv": tcg_result} if tcg_result else {}))
    for site, r in runs.items():
        c = r["counts"]
        run_rec = {"run_id": f"{stamp}-{site}", "site": site, "started_at": utc(r["t0"]), "finished_at": utc(r["t1"]),
                   "pages_fetched": c["pages_fetched"], "pages_unchanged": c["pages_unchanged"],
                   "blocks_parsed": c["blocks_parsed"], "blocks_without_source_text": c["blocks_without_source_text"],
                   "added": c["added"], "changed": c["changed"], "removed": c["removed"], "refusals": r["refusals"]}
        if c["new_products"]:
            run_rec["new_products"] = c["new_products"]
        if site == "cn":
            run_rec["variant_disagreements"] = c["variant_disagreements"]
            run_rec["cn_duplicate_ids"] = c.get("duplicate_ids", [])
        if site == "tcgcsv":
            run_rec["unpriced"] = len(c["unpriced"])
            run_rec["no_image"] = len(c["no_image"])
            run_rec["unmatched"] = len(c["unmatched"])
            run_rec["rarity_disagreements"] = len(c["rarity_disagreements"])
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
    return {"sites": results, "cards": dict(card_counts), "cn": cn_diff, "cn_run": cn_result, "tcgcsv_run": tcg_result}


def date_line(d):
    if d is None:
        return "release dates: not in this page set, none read"
    return (f"release dates: dated {d['dated']} (set {d['set']}, moved {d['moved']}, kept off the index {d['kept']}), "
            f"undated {len(d['undated'])}{' [' + ', '.join(d['undated']) + ']' if d['undated'] else ''}, "
            f"product pages read {d['pages_read']}"
            + (f", double packs {d['double_packs']}" if "double_packs" in d else "")
            + (f", double packs with no date on the index [{', '.join(d['double_packs_undated'])}]"
               if d.get("double_packs_undated") else ""))


def origin_line(dists, o):
    head = f"distributions from the card list: {dists}"
    if o is None:
        return f"{head}; event pages: not in this page set, none read"
    return (f"{head}; event pages read {o['pages_read']} (dated {o['pages_dated']}), claims {o['claims']} "
            f"(inferred, for review {o['inferred']}, of them card-block contradictions {o['contradictions']})")


def cn_line(r):
    run_rec = r["run"]
    return (f"cn: details read {run_rec['pages_fetched']}, rows {run_rec['blocks_parsed']}, "
            f"added {run_rec['added']}, changed {run_rec['changed']}, removed {run_rec['removed']}, "
            f"refusals {run_rec['refusals']}, variant signals disagree on {run_rec['variant_disagreements']}, "
            f"unknown number tokens {r['counts']['unknown_tokens']}, distributions {r['distributions']}, "
            f"duplicate ids (a second save of a lower id's printing) {len(run_rec['cn_duplicate_ids'])}"
            f"{' ' + str(run_rec['cn_duplicate_ids']) if run_rec['cn_duplicate_ids'] else ''}")


def tcgcsv_line(r):
    run_rec = r["run"]
    c = r["counts"]

    def named(ids):
        return f"{len(ids)}{' [' + ', '.join(map(str, ids)) + ']' if ids else ''}"
    return (f"tcgcsv: product and price files read {run_rec['pages_fetched']}, DON products {c['dons']}, "
            f"Release Event stamps {c['stamps']}, "
            f"added {run_rec['added']}, changed {run_rec['changed']}, refusals {run_rec['refusals']}, "
            f"unpriced (refused, no key) {named(c['unpriced'])}, no image yet {named(c['no_image'])}, "
            f"stamps on no card of ours (refused) {named(c['unmatched'])}, "
            f"stamp rarity differs from the en base {named(c['rarity_disagreements'])}")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pageset")
    ap.add_argument("repo")
    ap.add_argument("sites", nargs="*", default=SITES)
    a = ap.parse_args(argv[1:])
    try:
        res = run(a.pageset, a.repo, a.sites)
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
    if res["cn_run"]:
        print(cn_line(res["cn_run"]))
    if res["tcgcsv_run"]:
        print(tcgcsv_line(res["tcgcsv_run"]))
    print(f"cards: {res['cards']}")
    if res["cn"]:
        print(f"cn snapshot: {res['cn']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
