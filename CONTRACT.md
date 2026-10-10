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
data/retired_printings.jsonl          retired_printing (printings removed by reviewed PR; the app deletes them)
data/don_sets.jsonl                   don_set (which set page each DON card sits on)
runs/{YYYY}/{MM}/{run_id}.json        ingest_run (audit only, not synced)
state/pages/{site}.json               last clean block count, hash and fetched_at per page (ingest only, not synced)
state/cn_ids.jsonl                    cn id snapshot, numeric id and cardNumber (ingest only, not synced)
state/cn.json                         cn list fetch time and row count of the last committed run (ingest only, not synced)
state/tcgcsv.json                     tcgcsv groups fetch time of the last committed run (ingest only, not synced)
state/product_pages/{site}.json       series each product page links, and the product index fetch time (ingest only, not synced)
state/event_pages/{site}.json         event and topic pages already read, and the event list fetch time (ingest only, not synced)
state/keepalive.txt                   date of the last heartbeat commit, written only after 30 quiet days (ingest only, not synced)
```

`site` is one of `en`, `jp`, `cn`. These are sites, not languages. `asia-en`
and `tc` were sites until 2026-10-10 and are retired (see Retired sites). DON
rows add a fourth, `tcgcsv`, for printings, locators, observations
and runs, and Release Event stamps add it for printings, locators,
distributions and runs (see DON and Release Event stamps). It is never a
product site.

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
| card | `card_` + 12 [0-9a-z], minted once | `number`; for DON cards, `don_design` (see DON) |
| printing | `prt_` + 12 [0-9a-z], minted once | its locator, `{site}:{image_id}` |
| distribution | `dist_` + 12 [0-9a-z], minted from the natural key | `{site}\|{source_text}`: the pack a site's card list names |
| product | `{site}:{series_id}` | itself |
| card_observation | `{card_key}:{site}:{observation_hash}` | itself |
| printing_locator | `{site}:{image_id}` | itself |
| printing_product | `{printing_key}@{product_key}` | itself |
| printing_distribution | `ev_` + sha256(printing\|distribution\|source\|source_url)[:16] | itself |
| printing_link | `{printing_a}={printing_b}`, a sorts first | itself |
| retired_printing | the retired printing's key | itself (`key` equals `printing_key`) |

- Minted keys never change and are never reused. The ingest looks up the natural
  key first and mints only when nothing matches.
- `image_id` is site-local. Bandai sites use the base number plus an optional `_pN`
  or `_rN` suffix; cn uses the API's numeric id. Suffixes do not line up across
  sites, so the same art in two languages is a `printing_link`, never a shared
  printing. tcgcsv uses `{productId}:{Normal|Foil}`. Per site, image ids were measured stable (55 EN and 48 JP weekly
  snapshots, Wayback 2023-2026 on the EN promo page). **cn is an assumption, not
  yet measured:** nothing has covered the stability of the API's numeric id, and
  the cn list also carries its own suffixed `cardNumber` (`P-084_01`: two digits,
  no p or r). The ingest snapshots both, and later runs diff them.
- DON and Release Event stamps: see their sections below. tcgcsv is a
  printing, locator, claim and run site for DON and Release Event stamps, a
  distribution site for Release Event stamps, an observation site for DON
  only, and never a product site.

## DON

No official card list carries DON cards (0 on all five sites, measured
2026-10-08), so they mint from tcgcsv, TCGplayer's catalogue (`ingest/tcgcsv.py`,
`run_tcgcsv`). Mike 86397: each DON is its own card, and a normal and a gold
DON are related the way a rare and its alt art are. Measured 2026-10-09 over 87
groups and 7,717 products; a value outside these vocabularies stops the run.

- **Which products are DON.** Those whose `extendedData` CardType is `DON!!`
  (240). Not Rarity: 17 DON promos carry Rarity `PR`. The 18 sealed
  "Special DON!! Card Pack DP-NN" products carry no card data, so they are not
  DON cards. No DON carries a Number, so a DON card has none.
- **Normal and gold are separate products.** A gold is named as its normal
  plus ` (Gold)`, in the same group (73 of 74). A product named `(Gold)` whose
  group has no normal of that name is a normal product, its own card:
  482236 "DON!! Card (Gold)" sits in the promo colour series (Red, Yellow,
  Blue, Purple, Silver beside it), which is inference from its neighbours, not
  a measurement.
- **The card is the normal product** (alyssa 87481). `don_design` is
  `{group abbreviation}:{slug of the normal product's name}`
  (`PRB-01:don-card-uta`; a space in an abbreviation becomes `-`, so
  `ST-01-PRE:don-card`). It is set once, at mint, and never rewritten. A card
  is found through its normal product's existing locators first, then its
  `don_design`, and minted only when neither matches, so a TCGplayer rename
  keeps the card and versions its observation. **This over-splits until art is
  measured**: the same art reissued in another group is two cards today and
  would be one card later (upper 87516). Over-split was chosen because a merge
  is recoverable and a wrong merge is not.
- **Finish is a printing.** TCGplayer prices each product per finish, its
  price row's `subTypeName`, `Normal` or `Foil`, and one product can carry both
  (69 do: PRB-01 30, PRB-02 30, OP-PR 4, EB03 4, OP10 1). They are two physical
  cards (alyssa 87481). So a printing is one product in one finish, and its
  locator **always** carries the finish: `tcgcsv:{productId}:{subType}`, on a
  Foil-only product too, so a product that gains a finish later gains a
  printing and nothing rekeys. `productId` is TCGplayer's own integer, which
  price rows key on too.
- **Variant.** A normal product's printing is `normal` or `foil`, its finish.
  Those two are tcgcsv-only: the schema refuses them on every official site's
  printing. A gold product's printing is `gold`, and **gold is always foil**: there is no
  gold-foil variant (74 of 74 golds are Foil only; a gold priced Normal stops
  the run). The rule lives in one function, `tcgcsv.variant`, so a ruling on
  the finish is a change there only.
- **No price row, no key** (upper 87490). Two DON have no price row
  (2026-10-09: 561656 and 619595, the 2023 and 2024 World Championship promos),
  so no finish and no locator. They are refused, counted in the run's
  `unpriced`, named by productId on the run line, and mint on the first run
  that sees a price row. No finish is ever guessed.
- **Rows.** A DON printing: `site` `tcgcsv`, `rarity` `DON`, `source_text` the
  TCGplayer product name verbatim (the gold's own name on a gold printing),
  `image_url` TCGplayer's `{productId}_in_1000x1000.jpg`, `block_icon` `null`.
- **image_url is omitted while TCGplayer has no image** (upper 87556, alyssa
  87571). A product's `imageCount` is TCGplayer's own count of its images;
  where it is 0 the CDN answers 403 (2026-10-09: 3 of 240, 677570, 677571 and
  719824, and exactly those 3). Such a printing carries no `image_url`, the
  run counts it in `no_image` and names it, and the first run that sees a
  positive count writes the URL. This is why the field is split by site: a
  new DON listing sits at `imageCount` 0 for a while, so a required URL would
  be wrong by construction, not only for three rows. Every official site's
  printing still requires one.
- **provenance_url links Bandai's own copy, measured matches only** (Mike,
  DON provenance arc; app side koala-kollect #25). Optional on a tcgcsv
  printing, refused on an official site's, and always an
  `https://en.onepiece-cardgame.com/` URL. It is written from
  `tcgcsv.PROVENANCE`, a hand-kept table by productId, onto every finish of
  that product. A product joins only when Bandai's image matches TCGplayer's
  `{productId}_in_1000x1000.jpg` at a mean absolute pixel difference of about
  5/255 (2026-10-10: 677560 and 677559, EB-03 Nami normal and Gold; 683969,
  Netflix Chopper). Where TCGplayer's image is its own scan the difference
  runs 26-56 even for the same art, and an art correlation names the
  character but cannot tell finishes apart, so those stay unlinked rather
  than guessed.
  A DON card's observation (alyssa 87523): `site` `tcgcsv`, `lang` `en`, `name`
  the normal product's name verbatim, `category` `don`, no colours, attributes
  or types, and its `facts_site` is `tcgcsv`. tcgcsv has no product rows and no
  listings, so a DON printing has no `printing_product` and nothing is ever
  removed.
- **Stale guard.** `state/tcgcsv.json` holds the committed page set's groups
  `fetched_at`; an older page set stops the run with nothing written, an equal
  one is a re-run. The page set is complete or absent: any failed request fails
  the daily run, as for every other source.

## DON sets

A DON carries no number, so its set cannot be read off it the way a numbered
card's is. `data/don_sets.jsonl` says which set page each DON card sits on: one
`don_set` row per DON card, written by the tcgcsv walker in the run that mints
the card (`tcgcsv.don_set`, called from `run_tcgcsv`). Survey and rulings:
alyssa general 88538, upper 88539/88540, 88803, 88805.

- **The unit is the card, not the product.** A gold is a printing of its
  normal's card (73 of 74 in the same group), so a card and its gold sit on
  one page. `key` is the card key, `printing_keys` every tcgcsv printing the
  card has, sorted, and `don_design` the card's, for readers and checks only.
- **set_slug** is the app's own slug for a set page: an en product `code`
  lowercased (`prb-01`, `op14-eb04`, `dp-06`), or `promo`, the existing promo
  page. Placement within the page is the app's; the file carries no order.
- **source group.** The card's TCGplayer group abbreviation, with punctuation
  and spaces stripped, equals an en product code stripped the same way (`OP09`
  is `OP-09`, `OP14-EB04` is itself). Measured 2026-10-10: 23 groups, 189
  DON products, of which the overrides below take 27. Two en codes that strip
  alike stop the run.
- **source override.** The product's own name names another product, so its
  group is the wrong page. `tcgcsv.SET_OVERRIDES` keys these by the card's
  normal productId; a gold follows its normal's card. 22 Double Pack Set DONs,
  two per volume, sit on `dp-02` … `dp-12` (Double Pack Sets, en `DP-NN`
  products). A name that says Double Pack or Special DON!! Card Pack with no
  row in the table **stops the run; it is a stop, not a skip**, so the next
  volume cannot land on its group's page by default and cannot go missing
  either.
  **A DON with no en product sits on promo with source override**: the
  Heroines Special Set DON 710745 (its gold 710746) in EB03, the Film RED promo
  456320 in OP01, and the two Special DON!! Card Pack DONs in OP04, 517477
  (Color) and 517478 (Black & White). `source` says a hand placed them;
  `promo` would claim the promo page lists them, and it does not (upper 88805,
  88872). If a real en product appears later, such as a Special DON!! Card
  Pack in the product index, the row moves then.
- **source promo.** TCGplayer's promo groups, which list promos and not a
  product: OP-PR (17675), OP-DD (23907) and ST-01 PRE (17659). `set_slug` is
  `promo`.
- **Anything else stops the run**: a DON whose group is no en product, no
  promo group and not overridden, or an override onto a slug that is no en
  product. Nothing is guessed.
- **A row changes when its card does.** A finish appearing adds its printing
  to `printing_keys`; an override added or removed moves `set_slug`.
  `first_seen_at` keeps the first sighting. Rows are never removed, as no
  tcgcsv row is.

## Release Event stamps

Release Event cards are copies of a set's own cards with a Release Event stamp,
handed out at that set's release events. Mike, dm-alyssa 88048: they are their
own printings. No official card list carries them (Bandai's en "OPxx Release
Event" distributions hold the event's prize promo only, not the stamped set
cards), so they mint from tcgcsv, as DON do. Measured 2026-10-09: 554 stamped
cards in 7 TCGplayer groups whose abbreviation ends ` RE` (OP10 24068, OP11
24242, OP12 24406, OP14 24579, OP15 24638, OP16 24677, OP17 24775; OP18 RE
24834 is empty). Pre-Release stamps are out of scope.

- **Which groups.** Only the groups in `tcgcsv.RE_GROUPS` mint, by groupId,
  so a group joins the data by a change there: today the seven listed above.
  OP18 RE (24834) is not admitted while it lists no stamp, since an admitted
  group with none stops the run.
  A stamp is a product in such a group with an `extendedData` Number; the
  group's sealed pack has none and is not read. An admitted group missing
  from `/groups`, holding no stamp, or with no price file stops the run.

- **The card is the set card it stamps.** Each product's `extendedData`
  Number is one card of ours, and the printing hangs off that card. Its facts
  and observations stay on the official sites: a stamp writes no
  `card_observation` and never sets `facts_site` (the schema keeps a tcgcsv
  observation DON-only). A Number that matches no card is refused, never
  minted, and counted in `ingest_run.unmatched` and named by productId on the
  run line.
- **Locator.** Unchanged from DON: `tcgcsv:{productId}:{subType}`. All 554
  price Normal only, so every locator ends `:Normal`; `scripts/check.py`
  refuses a stamped printing on any other finish, or with no locator at all (a
  cross-record check, since a locator does not carry its printing's variant).
- **Variant `stamped`.** A stamp's printing is `stamped` and nothing else, and
  `stamped` is tcgcsv-only: the schema refuses it on every official site's
  printing, and refuses it on a DON. A tcgcsv printing is therefore a DON
  (rarity `DON`, variant `normal`, `foil` or `gold`) or a stamp (variant
  `stamped`, any other rarity). A stamped product priced Foil stops the run:
  a second finish would be a second physical card, and that is a ruling, not a
  guess.
- **Rows.** A stamped printing: `site` `tcgcsv`, `rarity` as TCGplayer prints
  it (C or UC so far; 553 of 554 agree with the en base printing, EB04-053
  reads C against en R). The rarity is kept verbatim; a stamp whose rarity
  differs from its card's en base printing is counted in
  `ingest_run.rarity_disagreements` and named on the run line. `source_text`
  the TCGplayer product name verbatim,
  `block_icon` `null`. `image_url` follows the DON rule: TCGplayer's
  `{productId}_in_1000x1000.jpg`, omitted while the product's `imageCount` is
  0 and counted in the run's `no_image` (2026-10-09: OP16 RE, 75 of 75).
- **No dates.** A stamped printing carries no date and its claim no
  `starts_on` or `ends_on`. tcgcsv is never a date source (Release dates
  above), and that holds here too. The claim's `observed_at` is not an event
  date: it is when the group's products were fetched, the earliest kept, as
  on every claim.
- **One distribution per RE group.** `site` `tcgcsv`, `region` `en` (TCGplayer
  is North American), `kind` `event_pack` (the schema pins both), `name` the
  group's name verbatim ("The World's Strongest Warriors Release Event
  Cards"), `source_url` the group's products listing below. Its key is
  minted from `tcgcsv|{group name}`.
- **At least one claim per stamped printing.** The walker writes one: `source`
  `tcgcsv`, `source_url` `https://tcgcsv.com/tcgplayer/68/{groupId}/products`,
  `quote` the group's name verbatim, `confidence` `corroborated`. The count is
  a floor, not an exact number: several claims per printing are normal (Promo
  origin). `scripts/check.py` refuses a stamped printing with no tcgcsv
  claim, and a tcgcsv claim that is not a stamped printing's, not on a tcgcsv
  distribution, not `corroborated`, or carrying `starts_on` or `ends_on`.
- **`corroborated` here means a third-party catalogue's own grouping**
  (alyssa 88082): TCGplayer files the product under that event group, and we
  record that it did. It is not "two sources agree", and it is never
  `authoritative`, which is kept for Bandai's own pages.
- **No product rows.** tcgcsv is never a product site, so a stamped printing
  has no `printing_product`, as with DON.

## Write rules

- **Nothing is deleted.** A listing that disappears gets `removed_at` on its
  `printing_product` row. Printings have no `removed_at`, because a printing can
  move between series pages under the same image id (13 left the EN promo page
  for Other Product Card). The one exception is a printing that should never
  have been minted: a reviewed PR removes it and lists it in
  `data/retired_printings.jsonl` (see Retired printings).
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
  cn. `facts_site` records which one, so a China-only or EN-only
  card still has facts.
- **Raw strings stay raw.** `printing.source_text` and
  `printing_distribution.quote` are verbatim, so a better parser can re-derive
  everything without a re-scrape. When a site shows no provenance for a printing,
  `source_text` is the empty string and the run counts it in
  `blocks_without_source_text`. A jump in that count means a broken parser.
- **Images are linked, never hosted.** `image_url` is the absolute official URL
  (TCGplayer's for a DON printing) with no query string. Bandai's card images carry a site-wide deploy stamp
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
  half-width on en, full-width on jp. Both are stored as
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
  a warning, never a failure. For jp a code match against tcgcsv is a false
  join: it carries the en date, and jp differs from en on every shared code. The schema refuses a source outside the Bandai
  products path.
- **The join is the series link, never a code.** A product page links its card
  list as `cardlist/?series=NNNNNN`, and that id equals `product.series_id` on
  the same site. A code read from the title would miss the bundle pages (the
  ST-01 to ST-04 page links four series) and the compound codes (en
  `op14-eb04` links 569114). A page that links no series dates nothing.
- **The date is per site.** en differs from jp on every shared product, and is
  earlier on ST-23 to ST-28 (asia-en and tc, retired, matched jp on all of them). The
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
- **Double Pack Sets are minted from the index, en only.** A Double Pack Set
  has no card list of its own (its cards are a booster's), so its page links no
  series and the card-list walk never writes it. The run writes one `other` row
  per en index item titled `... [DP-NN]`: key `en:DP-NN`, `series_id` and
  `code` `DP-NN`, `name` the index title, `release_date` the item's date,
  `release_date_source` and `product_url` the item's page. Each run dates it
  from its own item again, so a moved date moves; one code listed under two
  pages or two dates stops the run. A pack that drops off the index keeps its
  row and date (sticky). jp is not read for packs.
- **No undated row is written, and that is load-bearing.** A pack the index
  lists with no date is not written; the run line names it. The reason is the
  undated check above: one product row of a dated kind without a date makes
  every daily run re-read every product page that linked no series (139 of 190
  en pages on 2026-10-10), forever, and hides the next real undated product
  under a check that is already true. Anything minted outside the card-list
  walk follows the same rule: dated, or not written.
- **en:OP-14 and en:OP-15 are placed by hand.** en sells OP-14 and OP-15 only
  inside the combined OP14-EB04 and OP15-EB04 packs (en:569114, en:569115), so
  no en walk writes a row with either code, and the app titles a set from a
  product carrying its code. These two rows are those combined en listings
  placed on the set code by hand (Mike, 2026-10-10): `name` verbatim from the en
  page, so the row says `[OP14-EB04]` in its own text; `release_date` and
  `release_date_source` the combined pack's, so each set dates off its earliest
  en product like every other set. They link no series, so the walk keeps them
  as they are (sticky) and never re-dates them. EB-04 has no such row: no en
  page names it on its own.

## Promo origin

Where a promo printing came from, and when (ruled alyssa 87163). Every printing
on a site's promo card list names its pack in `source_text`. That string is the
authority on which pack a printing came from.

- **One distribution per (site, `source_text`).** Its key is minted from
  `{site}|{source_text}`, `name` is the string with the card list's own
  decoration removed (a trailing カードリスト, a leading "Included in"),
  `region` is the site's region (en: en, jp: jp, cn: cn), and
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
that locator, so two cn ids share a printing only when cn saved one
printing twice (Duplicates, below). A product is
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

**Duplicates.** cn sometimes saves one printing twice: two ids whose list row
and detail agree in every field apart from `id`, `createTime` and `updateTime`.
On 2026-10-08 there were two such groups in 4,927 ids, each saved within one
second: OP09-043 as ids 4647, 4648 and 4649 (created 2025-04-17 14:17:03,
14:17:03 and 14:17:04, 特别宣传包Vol.7) and P-108 as ids 5521 and 5522
(2025-10-20 13:16:55 and 13:16:56, 参加纪念). jp, tc and asia-en list one
printing for each. The lowest id keeps the printing; each later id is a second
locator on it (`cn:4648` points at the `cn:4647` printing) and mints nothing,
so a later run still knows the id. `ingest_run.cn_duplicate_ids` names every
such id on every run, so a new double-save shows as a new id. Both details are
needed to tell, so `cn.fetch_pageset` fetches a held id's detail again when a
new id's list row equals it. A duplicate that already has a printing of its
own stops the run: that printing leaves only through a reviewed PR, as the
three above did (see Retired printings).

The predicate includes the list row, and a cn list row names its product, so
two duplicates are always on one product. **Known case it must not match:**
asia-en lists P-029_r1 (one image id) under two products, ST-16 and PRB-01,
where en, jp and tc list two image ids. Those are two releases, but the
locator `{site}:{image_id}` cannot tell them apart, so asia-en P-029_r1 stays
one printing with two `printing_product` rows (alyssa 87754). If a second case
appears, or pricing needs the two priced apart, the shape to rule is a product
suffix on the locator, as tcgcsv carries one. On cn that shape is two ids with
different `cardOfferType`, and a test asserts it stays two printings.

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

## Retired sites

`asia-en` (Asia English) and `tc` (Traditional Chinese) are retired as of
2026-10-10 (Mike, dm-roberto 88876: those are not real languages for the app).
`run.RETIRED_SITES` names them; `run.SITES`, the daily walk's list, does not,
and `run()` refuses either before it reads anything. The schema's `site`,
`region` and `lang` enums and the locator, observation, product and
printing_product key patterns no longer admit them, so a row for either fails
the schema.

What went, 2026-10-10: 9,829 printings (asia-en 4,914, tc 4,915) with their
locators and printing_products, 5,630 card_observations, 124 products, 353
distributions and the 1,805 claims on them (every one on a retired-site
distribution and a retired-site printing), and their `state/pages`,
`state/product_pages` and `state/event_pages` files. No card changed: none had
either site as its `facts_site`, and every card keeps a printing on en, jp, cn
or tcgcsv.

How the app removes them: each retired printing is a `retired_printings` row
with reason `site_retired` and its own locator as the one `source_id` (an
`asia-en:` or `tc:` locator; the schema refuses any other), which the
sync deletes with everything pointing at it. The sync has no delete path for
products, distributions or card_observations, so those go by a one-off app-side
sweep after this data lands (alyssa, general 88890).

What the app shows differently: three sets, EB-04, OP-14 and OP-15, took their
English title from asia-en, because en sells them only as the combined
OP14-EB04 and OP15-EB04 packs. OP-14 and OP-15 keep an English title from the
hand-placed en rows (Release dates); EB-04 shows the jp name. No set date
moves from the removal itself: a set dates off en, else jp, never asia-en.

Runs stay: the `runs/` records both sites wrote before 2026-10-10 are the audit
record, so `ingest_run.site` and `new_products` still admit them (`run_site`,
`run_product_key`). `scripts/check.py` validates every run and refuses a run of
a retired site started after its retirement, which it reads from the
`site_retired` rows' `retired_at`.

Measurement tables below that name asia-en or tc are dated history and are left
as they were.

## Retired printings

`data/retired_printings.jsonl` lists printings that should never have been
minted and were removed from the data by a reviewed PR (alyssa 87767). One row
per printing: `key` (equal to `printing_key`), `printing_key`, `reason`,
`retired_at`, and `source_ids`, the locators that show it (the retired
printing's own first, then the one it duplicates).

- The app sync applies the file as a delete: the printing, and every row that
  points at it (`printing_products`, `printing_distributions`,
  `printing_locators`, `printing_links`), matched on the printing key and never
  on a locator string. The sync is otherwise insert or update only, so a row
  removed from the data without a retired row stays in the app.
- The ingest refuses to write a retired printing again, and the check refuses a
  key that is both a live printing and retired, or a row whose `key` is not its
  `printing_key`.
- A locator is never retired by this file: a retired printing's locator either
  goes with it or moves to the printing that survives (cn duplicates, above).
- Reasons: `duplicate_source_record`, the source listed one printing under two
  ids and the later id had been minted a printing of its own (`source_ids` two
  or more); `site_retired`, the printing's site is retired (`source_ids` its own
  locator only, see Retired sites).

The first three, retired 2026-10-09: the printings of `cn:4648` and `cn:4649`
(duplicates of `cn:4647`) and of `cn:5522` (duplicate of `cn:5521`).

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
either, except the printings listed in `data/retired_printings.jsonl` and
the rows that point at them (see Retired printings).

## Checks

`python scripts/check.py` validates the examples against the schema. It also
confirms that each control in `examples/invalid.jsonl` fails with the error
named in its `expect` field (or one of a list, where jsonschema versions spell
the same error differently), refuses a key that appears on two lines of the
valid examples (the sync upserts by key, so a repeat overwrites silently; the
data files get the same check, within and across files), and runs the
image-id parser cases. Over the data it adds the cross-record checks the
schema cannot express: no retired printing is also live, a stamped printing
has a locator and every one ends `:Normal`, the Release Event claim rules
above, and every DON card has exactly one `don_set` row whose `don_design`,
`printing_keys` and `set_slug` agree with the card, its tcgcsv printings and
the en product codes (`source` `promo` only on `promo`, `group` never on it),
and every record under `runs/` is a valid `ingest_run`, none of a retired site
started after its retirement. Each has red controls in `check.py`. CI runs it on every push.
