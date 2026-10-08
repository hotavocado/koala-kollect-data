# Data contract (v1)

This repo is the source of truth for the Koala Kollect card database. The ingest
writes it and the app syncs from it. `schema/v1.schema.json` defines every record
type; this file covers layout and write rules.

## Layout

```
manifest.json                         one file entry per data file: type, rows, sha256
data/cards.jsonl                      card
data/card_observations/{site}.jsonl   card_observation
data/printings/{site}.jsonl           printing
data/printing_locators/{site}.jsonl   printing_locator
data/products/{site}.jsonl            product
data/printing_products/{site}.jsonl   printing_product
data/distributions.jsonl              distribution
data/printing_distributions.jsonl     printing_distribution
data/printing_links.jsonl             printing_link
runs/{YYYY}/{MM}/{run_id}.json        ingest_run (audit only, not synced)
```

`site` is one of `en`, `asia-en`, `jp`, `tc`, `cn`. These are sites, not
languages: `en` and `asia-en` are both English and list different products and
promos.

## File format

- JSONL, UTF-8, LF, one record per line, trailing newline.
- Lines sorted by `key`. Object keys in schema order. Then a re-run with no
  change is a byte-identical file, and the git diff is the changelog.
- An absent value is omitted, never `null`.
- Timestamps are UTC, `YYYY-MM-DDTHH:MM:SSZ`. Event and release dates may be
  `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, because some sources only give a month.

## Identity

| Record | Key | Natural key |
|---|---|---|
| card | `card_` + 12 [0-9a-z], minted once | `number`; for DON cards, `don_design` |
| printing | `prt_` + 12 [0-9a-z], minted once | its locator, `{site}:{image_id}` |
| distribution | `dist_` + 12 [0-9a-z], minted once | reviewed by hand |
| product | `{site}:{series_id}` | itself |
| card_observation | `{card_key}:{site}:{observation_hash}` | itself |
| printing_locator | `{site}:{image_id}` | itself |
| printing_product | `{printing_key}@{product_key}` | itself |
| printing_distribution | `ev_` + sha256(printing\|distribution\|source\|source_url)[:16] | itself |
| printing_link | `{printing_a}={printing_b}`, a sorts first | itself |

- Minted keys never change and are never reused. The ingest looks up the natural
  key first and mints only when nothing matches.
- `image_id` is site-local. Bandai sites use the base number plus an optional `_pN`
  or `_rN` suffix; cn uses the API's numeric id. Suffixes do not line up across
  sites, so the same art in two languages is a `printing_link`, never a shared
  printing. Per site, image ids were measured stable (55 EN and 48 JP weekly
  snapshots, Wayback 2023-2026 on the EN promo page). **cn is an assumption, not
  yet measured:** nothing has covered the stability of the API's numeric id, and
  the cn list also carries its own suffixed `cardNumber` (`P-084_01`: two digits,
  no p or r). The ingest snapshots both, and later runs diff them.
- DON: each DON design is its own card (`category: don`, no `number`,
  `don_design` = `{first product code}:{art slug}`). The normal and gold DON of one
  design are two printings of that card (`variant: base` and `variant: gold`).
  **Open:** which source DON designs are minted from. This depends on whether
  the official card lists carry DON at all.

## Write rules

- **Nothing is deleted.** A listing that disappears gets `removed_at` on its
  `printing_product` row. Printings have no `removed_at`, because a printing can
  move between series pages under the same image id (13 left the EN promo page
  for Other Product Card).
- **Removal guard.** A page stamps `removed_at` only when it was read cleanly.
  The run refuses removals from a page, and counts the refusal in its
  `ingest_run`, when any of these holds:
  - the fetch failed (`http_error`);
  - zero cards parsed (`zero_parse`);
  - the parsed count fell more than 10% below the page's last clean run
    (`count_drop`); this catches a truncated 200 or a layout change;
  - the parsed count disagrees with an independent count of card blocks in the
    same HTML (`count_mismatch`).
  A real removal larger than 10% keeps getting refused. It lands only through a
  reviewed PR, never on its own.
- **Change detection hashes parsed blocks, not raw HTML**, so a page-chrome edit
  is not a card change.
- **Errata are versions.** A changed card block closes the current
  `card_observation` (`superseded_at`) and opens a new one. `card` holds the facts
  from one site's current observation, in this order: jp (the authority), en,
  asia-en, tc, cn. `facts_site` records which one, so a China-only or EN-only
  card still has facts.
- **Raw strings stay raw.** `printing.source_text` and
  `printing_distribution.quote` are verbatim, so a better parser can re-derive
  everything without a re-scrape. When a site shows no provenance for a printing,
  `source_text` is the empty string and the run counts it in
  `blocks_without_source_text`. A jump in that count means a broken parser.
- **Images are linked, never hosted.** `image_url` points at the official site.
- `printing_links` and `distributions` are reviewed; the ingest may propose
  them only with `confidence: inferred`.

## Sync (app side)

The app reads `manifest.json` and checks every file's `sha256` and row count.
On any mismatch it refuses and records the refusal in `data_syncs`, writing
nothing. Then it upserts by `key`. Records are never deleted on the app side
either.

## Checks

`python scripts/check.py` validates the examples against the schema. It also
confirms that each control in `examples/invalid.jsonl` fails with the error
named in its `expect` field, and runs the image-id parser cases. CI runs it on every push.
