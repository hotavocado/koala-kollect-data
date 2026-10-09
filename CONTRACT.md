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
state/pages/{site}.json               last clean block count, hash and fetched_at per page (ingest only, not synced)
state/cn_ids.jsonl                    cn id snapshot, numeric id and cardNumber (ingest only, not synced)
state/cn.json                         cn list fetch time and row count of the last committed run (ingest only, not synced)
state/product_pages/{site}.json       series each product page links, and the product index fetch time (ingest only, not synced)
state/event_pages/{site}.json         event and topic pages already read, and the event list fetch time (ingest only, not synced)
state/keepalive.txt                   date of the last heartbeat commit, written only after 30 quiet days (ingest only, not synced)
```

`site` is one of `en`, `asia-en`, `jp`, `tc`, `cn`. These are sites, not
languages: `en` and `asia-en` are both English and list different products and
promos.

## File format

- JSONL, UTF-8, LF, one record per line, trailing newline.
- Lines sorted by `key`. Object keys in schema order. Then a re-run with no
  change is a byte-identical file, and the git diff is the changelog.
- An absent value is omitted, never `null`. One exception: `printing.block_icon`
  is `null` when the site prints no block icon on that printing, which is a
  fact, not an absence (see Block icon below).
- Timestamps are UTC, `YYYY-MM-DDTHH:MM:SSZ`. A product's `release_date` is a
  full date, `YYYY-MM-DD` (see Release dates below). A claim's event dates
  (`printing_distribution.starts_on`, `ends_on`) may still be `YYYY` or
  `YYYY-MM`, because some sources, such as monthly store battles, only give a
  month.

## Identity

| Record | Key | Natural key |
|---|---|---|
| card | `card_` + 12 [0-9a-z], minted once | `number`; for DON cards, `don_design` |
| printing | `prt_` + 12 [0-9a-z], minted once | its locator, `{site}:{image_id}` |
| distribution | `dist_` + 12 [0-9a-z], minted from the natural key | `{site}\|{source_text}`: the pack a site's card list names |
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
  printing. tcgcsv (DON printings only) uses TCGplayer's numeric productId. Per site, image ids were measured stable (55 EN and 48 JP weekly
  snapshots, Wayback 2023-2026 on the EN promo page). **cn is an assumption, not
  yet measured:** nothing has covered the stability of the API's numeric id, and
  the cn list also carries its own suffixed `cardNumber` (`P-084_01`: two digits,
  no p or r). The ingest snapshots both, and later runs diff them.
- DON: each DON design is its own card (`category: don`, no `number`,
  `don_design` = `{first product code}:{art slug}`). The normal and gold DON of one
  design are two printings of that card (`variant: base` and `variant: gold`).
  No official card list carries DON cards (0 on all five sites, measured
  2026-10-08), so DON designs are minted from tcgcsv, and a DON card's
  `facts_site` is `tcgcsv`. The schema refuses `tcgcsv` on any other category.
  A DON printing's `site` is `tcgcsv` and its locator is `tcgcsv:{productId}`,
  TCGplayer's own integer id (roberto 86534), which phase 3's price rows key on
  too. `tcgcsv` is a printing and locator site only, never a product,
  observation or run site, and a tcgcsv printing must be `rarity: DON`,
  `variant: base` or `gold`.
  The card is minted from the art, never from the product: a design reissued
  in a later DON pack is a new productId and so a second printing of the same
  card, found by its `don_design`.
  **Assumption, not yet measured:** a normal and a gold DON are separate
  TCGplayer products, so one product is one printing. If they turn out to be
  one product with a variant field, the locator becomes
  `tcgcsv:{productId}:{variant}` and the row is otherwise unchanged. The ingest
  measures this at its tcgcsv step.

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
- **Images are linked, never hosted.** `image_url` is the absolute official URL
  with no query string. Bandai's card images carry a site-wide deploy stamp
  (`?260929`) that changes on every redeploy; keeping it would turn each
  redeploy into a change to every printing.
- `printing_links` are reviewed; the ingest may propose them only with
  `confidence: inferred`. `distributions` are not reviewed: they mint from the
  card list (see Promo origin below).
- **Block icon is a printing fact.** `printing.block_icon` is the block icon as
  printed on that printing on that site: an integer, `"X"`, or `null` where the
  site prints none. `"X"` marks a card that never rotates out of standard. It is
  per printing because parallels of one number differ, and per site because
  sites disagree on the same image id. Measured 2026-10-08: EB04-061_p2 prints X
  on en and 4 on the other three sites; OP01-016_p8 prints 1 on en and X on
  asia-en, jp and tc. `card.block_icon` is a derivation, for filtering by
  block: the number on the facts site's base printing. When the facts site
  lists no base printing, it is that site's value if every one of its
  printings for the card agrees, and omitted otherwise, because a card-level
  value picked from disagreeing printings would be arbitrary (P-081 and P-082
  on jp, 2026-10-08: no base, every printing 3). It is never `"X"`, and it is
  omitted when the printing it would come from shows none.
- **`?` is an attribute value.** OP13-079 Imu prints `?` where an attribute goes:
  half-width on en and asia-en, full-width on jp and tc. Both are stored as
  half-width `"?"`, and the schema refuses the full-width form.
- **No `last_seen_at` on data rows.** It widened on every row on every run, so a
  day with no change still rewrote every line and the git diff stopped being
  the changelog. Rows keep `first_seen_at` and their events (`removed_at`,
  `superseded_at`). When each source was last checked lives in `runs/`.

## Release dates

`product.release_date` is that site's release date for that product, and
`release_date_source` is the page it came from. The two come together or not at
all.

- **Source: the site's own product index**, `/products/?page=N`. Each item
  there links a product page and carries a machine date,
  `<time datetime="YYYY-MM-DD">`. `release_date_source` is that product page's
  URL. Not the index page's URL: the index is newest first, so every new
  product moves every older item down a page.
- **The index can drop a product from one walk.** It sorts by date with no
  tiebreak and sorts again on every request, so products sharing a date that
  straddle a page boundary can be listed twice in one walk and not at all
  (2026-10-08: en OP-06 and dp03 share 2024-03-15 across pages 13 and 14; one
  walk listed dp03 twice and OP-06 never). A run walks the index two to four
  times and unions the items, stopping at the first walk that adds nothing.
  That makes a miss unlikely, not impossible: a real product without a date
  can be a transient, and a later day's walk dates it. The run line names
  every undated product by key and code.
- **tcgcsv is never a source.** It is TCGplayer, so its dates are North
  American releases. It agrees with Bandai en on every en product it carries
  (58 of 58, 2026-10-08) and `scripts/check.py` prints any en disagreement as
  a warning, never a failure. For asia-en, jp and tc a code match against
  tcgcsv is a false join: it carries the en date, and those sites differ from
  en on every shared code. The schema refuses a source outside the Bandai
  products path.
- **The join is the series link, never a code.** A product page links its card
  list as `cardlist/?series=NNNNNN`, and that id equals `product.series_id` on
  the same site. A code read from the title would miss the bundle pages (the
  ST-01 to ST-04 page links four series) and the compound codes (en
  `op14-eb04` links 569114). A page that links no series dates nothing.
- **The date is per site.** asia-en and tc match jp on every shared product; en
  differs from jp on all of them, and is earlier on ST-23 to ST-28. The
  `{site}:{series_id}` row is already that grain.
- **Retail, never pre-release.** When a site lists more than one page for a
  series, the retail page's date is the product's date and a pre-release page
  never is (a page whose file name ends `_pre`, or whose title code ends
  `PRE]`). en ST-01: retail page 2022-12-02, Super Pre-Release page 2022-09-30;
  the row reads 2022-12-02. Two retail pages with different dates for one
  series have no right answer, so the run stops and writes nothing.
- **Card pools carry no date.** `limited` (x801) and `promo_bucket` (x901)
  rows are card pools, not products: several premium collection pages with
  different dates link the same x801 series. The schema refuses a date on them.
- **Sticky.** A run sets a date or moves it to the one the index now shows, and
  never clears one. A product that drops off the index keeps the date it had.
- **What a run reads.** The index, two to four walks, on every run. A product page once, the first
  time it appears; its series links are kept in
  `state/product_pages/{site}.json`. A page that linked no series is read again
  while the site has a product with no date (or a new series in its card list
  dropdown), because a page published before its card list goes live links
  nothing yet. A product index older than the one the data was built from is
  refused, the same replay guard as the card pages.

## Promo origin

Where a promo printing came from, and when (ruled alyssa 87163). Every printing
on a site's promo card list names its pack in `source_text`. That string is the
authority on which pack a printing came from.

- **One distribution per (site, `source_text`).** Its key is minted from
  `{site}|{source_text}`, `name` is the string with the card list's own
  decoration removed (a trailing カードリスト, a leading "Included in"),
  `region` is the site's region (en: en, asia-en and tc: asia, jp: jp), and
  `kind` comes from a keyword table over the name (`events.KINDS`); a name no
  keyword matches is `other`, never a guess. A distribution holds no tier, date
  or quantity: those differ by page and live on each claim.
- **The card-list claim is authoritative.** Each promo printing gets one
  `printing_distribution` with `source: official_cardlist`, the promo card
  list's URL, `quote` = `source_text`, and `observed_at` = when the printing was
  first listed there. It needs no page and no review. When the pack name itself
  carries a date it is the claim's `starts_on`; a magazine name carries its
  issue month beside its on-sale date, and only the on-sale date is read.
- **Event-page claims: one per printing, distribution and page.** The ingest
  reads each site's event and topic pages. A line that names a pack gives every
  printing of that pack a claim with `source: official_event` (or
  `official_topic`), the page URL, `quote` = that line verbatim, `tier` from the
  prize heading above it, `quantity_note` as the page prints it, and the page's
  event date. A name joins on the card-list string with spacing and the game's
  own name ignored; a digit straight after the name is never part of it, so
  "Vol.1" does not match inside "Vol.15". `observed_at` is the first fetch that
  saw it.
- **Review is two cases only, both `confidence: inferred`:** a page that names
  the pack only once width, case and punctuation are folded ("Event Pack vol.7"
  for "Event Pack Vol.7"), and a card block whose heading names pack A and shows
  a card that the card list puts in no pack A on that site (the page contradicts
  the list; the claim is made, pack A must be a known pack). Everything else is
  `authoritative`.
- **A page's date** is its first date label's value (Date, Date & Time, 開催日,
  実施期間, 舉辦期間, …), else its only date-only line. A page with more than two
  distinct date-only lines is a schedule of several events and is undated; so
  is a page with no date. The entry window (応募期間) and a card's legal date are
  not event dates.
- **The event lists are walked 2 to 4 times and unioned.** List pages re-sort
  between requests and one walk can list an entry twice and drop another, so a
  single walk can miss pages; walks stop once one adds nothing new (measured
  2026-10-08: the set of pages was stable across walks on all four sites, the
  per-walk order was not). A daily run reads only pages it has never read and
  pages still on the current events list; `state/event_pages/{site}.json` holds
  the rest. A failed list or event page fails the run, and event lists older
  than the data are refused, the same replay guard as the card pages.
- **Claims are never removed.** A page that drops off a list keeps its claims.

**How much is dated (measured 2026-10-08, first full run).** A promo printing is
dated when at least one of its claims carries `starts_on`. Not every promo has a
date: many pages are schedules or carry no date, and a card-list name dates its
printings only when it names one (a magazine's on-sale date, a month, a period).

| site | promo printings | dated | share | from event pages | from the card-list name only |
|---|---|---|---|---|---|
| en | 374 | 315 | 84% | 315 | 0 |
| asia-en | 389 | 271 | 70% | 140 | 131 |
| jp | 460 | 315 | 68% | 213 | 102 |
| tc | 389 | 165 | 42% | 34 | 131 |
| all four | 1612 | 1066 | 66% | | |

The same run put 139 claims in review (en 103 from 17 distinct page/list name
pairs, jp 36 from 4, asia-en and tc none) and found no card-block contradictions.

## cn

cn is not a Bandai site. Its card list is a JSON API (Windo), one row per
printing keyed by a numeric id, and the card facts sit behind a per-id detail
call. Every vocabulary below was measured on the 2026-10-08 list (4,927 rows,
47 products) and its details; a value outside it stops the run, as on the
Bandai sites, except where a rule below says to keep it.

**Page set.** `cn.fetch_pageset` writes `cn/_list.json` (the whole list, with
`fetched_at`), `cn/_products.json`, and `cn/detail/{id}.json` (`{fetched_at,
info}`) for every listed id that has no printing yet. A detail is read only
for a new id, so **an erratum on cn shows only on a full re-read**, which is a
deliberate run, not the daily one.

**Identity.** A cn printing's locator is `cn:{id}`, and its key is minted from
that locator, so two cn ids never share a printing. A product is
`cn:{product id}`, its `code` is the last 【...】 group in the name, and its
`kind` comes from the name's leading word (补充包 booster, 特别补充包 extra,
豪华补充包 premium, 基本卡组 / 进阶卡组 / 究极进阶卡组 starter), with two named
buckets: 宣传卡 is `promo_bucket` and 限定商品收录卡牌 is `limited`. The product
list's `displayId` is not read: it is null on most products and its meaning is
unknown (P-084's points at the id of OP14-057).

**The number field is the base number.** cn appends its own token to the
printed number, either `_NN` (P-084_01, 389 rows) or inline (P, CP, -NN, SP,
LP, P-R, P-SR: 75 rows). The token moves out of the number, so the card is the
base number's card and cn mints no card of its own from a suffix (alyssa
87274). Read raw, the 75 inline rows name 75 numbers no card carries; split,
they name none, and a test pins that. The token is kept verbatim on the
printing as `number_token`. A token outside the measured shapes is still split
off and kept, and the run counts it (`unknown_tokens`), so a new cn spelling
never mints a card.

**Variant** (alyssa 87295). Three signals are read, and any one present makes
the printing a `parallel`; none makes it `base`:

- the number token;
- the image token: what the image file name carries after the base number
  (`1705891765183OP06-050P.png` gives `P`), kept as `image_token`. A file
  named by a bare hash says nothing, so the image is unread, not a no.
  Windo's re-upload marks are not art and are removed before the token is
  read: `(N)`, URL-encoded as `%28N%29` and sometimes stacked, with a `-N`
  directly in front of it (`-1(1)`, OP13-077), and a trailing `_D` (alyssa
  87340, 87354). A bare `-N` is not a mark and stays: `-NN` is a real inline
  cn token (OP06-050-03). On 2026-10-08, 18 rows were the only cn id for their
  number while jp listed that number base only, and every one wore such a
  mark. `image_token` keeps the raw token, marks included, so a later
  re-rule costs nothing. Lowercase `_d` is kept as an art mark (23 rows, none
  with that pattern);
- the name marker （异画） ("alt art"), which is dropped from the card name.

cn never emits `reprint`, and the schema refuses it there: `_NN` is a parallel
on one card and a reprint on another (P-084_01 is jp P-084_p1, OP12-026_02 is
jp OP12-026_r1), and nothing on cn tells them apart. jp stays the variant
authority, through `printing_links`. A run counts the printings whose signals
did not all agree in `ingest_run.variant_disagreements`.

The case that fixes the key: ids 2763 and 2764 are both OP06-050, with the same
name, rarity and number. Only 2764's image file says `P`. They are two
printings, base and parallel, because the printing key comes from `cn:{id}`. A
key built from the site and the number would make them one printing; a test
runs the pair through a run and a re-run and asserts both survive.

**Facts.** Rarity maps cn's labels to the Bandai codes (推广卡（P）, 宣传（P）
and a bare P are all `P`; 罕见（U） is `UC`; 隐藏稀有 is `SEC` in either case;
`TR` stays `TR`, as the Bandai sites print it). `cardLife` is life on a leader
and cost on everything else; `cardAttack` is the counter (`1000`, or `反击+1000`
with the plus half- or full-width). Two colours are joined by `/` or by `、`.
The attribute field is read on leaders and characters only, because on events
and stages cn fills it with `-` or the colour again (OP02-067, id 1925); two
attributes come as two elements or as one joined by `/` (`打/知`), and `？`
is stored as `?`, as on jp and tc. Types split on `/`, and on `,` where cn
typed one. cn prints a power of `0` (or `０`) where no Bandai site that has the
card carries a power: 152 cards and 252 details on 2026-10-09, none of them on
a card whose jp record has a power, and no Bandai site records a power of 0.
So `0` is cn's spelling of no power and is omitted, not written as zero (upper
87441). Numbers read any Unicode decimal digit (`１０００` is 1000); a digit
`int()` cannot read is refused.

A few rows typed something else into a field, and each is excused by its cn
id, checked against the card on the Bandai sites, never by a lenient match, so
a new slip still stops the run: a rarity in the category field (EB01-003P, id
2932); the art style (`cardCartograph`, 画师原创 or 漫画) in the category field
on five prize promos (ids 4056, 4057, 4063, 4064, 4065), all characters; 双色
("two colours") naming neither colour on two leaders (ids 6603, 6778); and the
category 角色 in the rarity field (ST29-012, id 5962, `C`). `subscript` is the
block icon.

**Source text.** `source_text` is the detail's `type` when it names something
(the promo pack: 特别宣传包Vol.2), and the product name otherwise. On some
booster rows `type` is a bare number (OP02-067: `2`), which names nothing, so
it is not used.

**Stale guard and removals.** `state/cn.json` holds the committed list's
`fetched_at` and row count. A list fetched before it is a replay and stops the
run with nothing written; an equal one is a re-run. The list is complete or
absent (`cn.list_all` refuses a short or doubled one), so an id that leaves it
stamps `removed_at` on its listing, unless the list fell more than 10% below
the last committed one (`count_drop`), when nothing is removed. A listed id
with no detail and no printing yet is refused (`http_error`) and comes in on a
later run.

## Transition (2026-10-08)

The schema accepted both shapes while the ingest moved over, so that CI stayed
green at every step:

1. **Widening:** `printing.block_icon` and the `"?"` value are legal.
   `card_observation.block_icon` and every `last_seen_at` are still legal but
   deprecated, and `last_seen_at` is no longer required.
2. **Ingest:** writes `block_icon` per printing, writes `"?"`, stops writing
   `card_observation.block_icon` and `last_seen_at`, and re-ingests. **Before
   it stops writing `last_seen_at`, it moves the stale-page guard:** today
   `run_site` refuses a page fetched earlier than its `product.last_seen_at`,
   and that is the only committed per-page freshness stamp. The replacement is
   `fetched_at` in `state/pages/{site}.json`: the fetch time of that page in
   the last committed run. A page fetched before its `fetched_at` is refused
   and nothing is written. Every committed run rewrites `fetched_at` for each
   page it fetched clean, in the same commit, so the committed stamp always
   covers the committed data. It is the fetch time, not the time the content
   last changed: a guard keyed on content change would pass a replay older
   than the last fetch but newer than the last change, which is the replay it
   exists to refuse.
   **Dated exception, 2026-10-08: card_observation rows were rekeyed in place,
   not superseded.** Dropping `block_icon` from the observation and reading `"?"`
   as an attribute changed the hash, and so the key, of 11,206 rows that had not
   changed on the page. Each was replaced under its new key with its
   `first_seen_at` preserved, and no `superseded_at` was written, so the errata
   history holds no erratum that did not happen. Old keys are gone from `data/`;
   the app cleared `card_observations` once on dev and re-synced (alyssa,
   general 86832). It happened once. The closing change below removed the
   ingest's rekey path, because the schema now refuses the old shape it matched.
3. **Closing change (this version):** no data row carries the old fields, so
   the schema refuses `card_observation.block_icon` and every `last_seen_at`,
   each with a red control in `examples/invalid.jsonl`, and requires
   `printing.block_icon`. A printing with no icon carries `null`.
   The ingest validates the committed data before it reads any fetch, so a
   row carrying an old field stops the run loudly; nothing drops it on read.

## Sync (app side)

The app reads `manifest.json` and checks every file's `sha256` and row count.
On any mismatch it refuses and records the refusal in `data_syncs`, writing
nothing. Then it upserts by `key`. Records are never deleted on the app side
either.

## Checks

`python scripts/check.py` validates the examples against the schema. It also
confirms that each control in `examples/invalid.jsonl` fails with the error
named in its `expect` field, refuses a key that appears on two lines of the
valid examples (the sync upserts by key, so a repeat overwrites silently; the
data files get the same check, within and across files), and runs the
image-id parser cases. CI runs it on every push.
