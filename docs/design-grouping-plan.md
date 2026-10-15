# Design grouping and classifier plan

Companion to the app's price tracking plan:
[koala-kollect `docs/price-tracking-spec.md`](https://github.com/hotavocado/koala-kollect/blob/main/docs/price-tracking-spec.md).

Status at the halt (2026-10-15): the hand-reviewed lanes are merged. The two
lanes below (bulk base assignment and the vision classifier) are ruled but
**not started**. Everything needed to pick them up is in this file.

A **design** is one way a card looks to a collector: one illustration, one
stamp state (stamped and unstamped are different designs). Stamp colour is not
part of a design: it is a fact of the prize pack and lives on the
`distribution` (`CONTRACT.md`, "Stamp colour"). A design can have printings in several languages, and the card
page groups by design. Rows live in `data/designs.jsonl` and
`data/printing_designs.jsonl`; the contract is in `CONTRACT.md`.

## 1. What shipped

| PR | Merge | Unit |
|----|-------|------|
| #33 base printings | `53b3616` | 29 jp/cn base rows for the 14 eye-reviewed cards. printing_designs 38 → 67. |
| #34 parallel printings | `1614489` | 19 jp/cn parallels on the same 14 cards reviewed: 18 rows written, 1 parked, plus the OP12-098 en _p1 row. 3 new designs. designs 37 → 40, printing_designs 67 → 86. |

Notable rows in the parallel PR:

- **OP10-033 cn _01** is the first language-exclusive design: the championship
  illustration printed without the stamp, and cn only.
- **OP10-033 jp _p1** is a third illustration of the card and its own design.
- **OP12-098** gets a new unstamped design for its second illustration,
  founded on the en _p1 printing (`prt_arzvllmhsrb8`). Founding it on the jp
  printing would have made it read as jp-exclusive, which it is not.

Only these 14 cards carry designs, so most card pages still say
"x printings". Closing that gap is section 3.

Calibration records, with method and per-printing decisions:
`calibration/design-review-*.json`.

## 2. Calibration method (as ruled)

1. **The hash proposes; it never decides.** `imagehash` dhash (16, 256 bits)
   on the site image composited on white, whole card and art crop
   (x 8–92%, y 12–60%). It suggests which illustration a printing belongs to.
   It cannot carry a verdict: 60 art bits was a join on one card and 56/58 a
   split on another. Whole-card hash also cannot separate stamped from
   unstamped (nearest design was the stamped one in 4 of 28 base cases), because
   each site prints its own SAMPLE watermark over the art.
2. **Diff median against a same-art control decides "same drawing or not".**
   Both images at 600×838, 2px blur, 12×8 grid of mean absolute difference,
   take the median cell. Same art: 0–4 for jp vs en, 2–13 for cn (cn images
   are upscaled from 454×635). Different art: 17–62. Always read it next to a
   known same-art pair on the same card.
3. **Locate the stamp before reading it.** A stamp is not always inside the
   art: on OP12-085 it prints under the text box (y 75–91%), so a fixed art
   crop would call a stamped card clean. Diff a stamped sibling against the
   unstamped one to find the region, confirm on a case where the stamp is
   visible, then crop that region and read it.
4. **The eye decides stamp and stamp colour.** Label plus a human read.
5. **Stamped and unstamped are separate designs.** Stamp colour is NOT a
   design axis: it lives on the `distribution` as `stamp_color`, never on the
   design (`CONTRACT.md`, "Stamp colour", ruled 2026-10-15).
6. **Translated text drawn inside the illustration stays one design.** A
   different drawing is a different design; the same drawing with translated
   words is not (worked cases OP12-098, OP12-078).
7. **On cn, the variant label is wrong in both directions.** OP12-098 cn _02 is
   labelled parallel but the image is the cn base, and 106 cards carry two or
   more cn printings labelled base. The label is the only cn signal, so cn
   needs the image read.

## 3. Next lane A: bulk base assignment (ruled, not started)

Goal: give every card one unstamped design founded on its en base, so card
pages group by design instead of saying "x printings".

Rule (upper, 2026-10-15, rows 93624 and 93632):

- One unstamped design per card, founded on the en base printing. Every
  printing in any language with a base or reprint label and none of the three
  parallel signals (number token, image-file token, name marker) joins it.
- The 14 eye-reviewed cards are left untouched.
- Stamped tcgcsv printings are excluded by their label.
- **cn carve:** bulk assigns only cards with exactly one cn base. Cards with
  two or more cn base printings go on the exception list in the calibration
  file with their printing ids.
- **Screen before commit:** every jp and cn base and reprint, and every en
  reprint, against the card's en base by diff median. Median over 20 is
  flagged; flagged rows are parked, not assigned. Report the count and the
  flagged ids before writing.
- Cards with no en base are founded on the jp or cn base and assigned; the app's
  star rule gates them while their parallels stay unreviewed.
- Rows are marked as rule-assigned, not eye-reviewed (`reviewed_by: "rule"`).
  `printing_design.method` is an enum of `manual | image_hash |
  suffix_and_rarity` with no "rule" value. Which `method` value rule rows
  carry is **an open decision**; the classifier's grading set needs eye and
  rule rows to be distinguishable.
- Design keys mint as `model.mint("dsn", "review|<founding printing key>")`
  so a key stays stable if a rule design is later eye-reviewed.

Numbers, measured on `53b3616` (scripted, 2026-10-15):

| Count | Counted over |
|-------|--------------|
| 2,838 | cards with at least one base or reprint printing (covered by the rule) |
| 165 | DON cards with no base or reprint printing (no design from this rule) |
| 106 | cards with 2+ cn printings labelled base (carved out, exception list) |
| 53 | cards with no en base (founded on jp or cn base) |
| pending | printings flagged by the screen (median over 20 against the card's en base); the screen was running at the halt |

The screen does not cover the 53 cards with no en base. Screen their cn base
against their jp base separately, or list them as unscreened.

Tooling exists outside the repo (worker memory, `calib-seat16/screen.py`).
The generator for this lane is not written.

## 4. Next lane B: vision classifier (ruled, not started)

Mike's ruling (2026-10-15, via alyssa): treat review as a classification
problem, in the shape of the direct-hire jev classifier. Two bounds from him:

- **Deterministic structured output:** a fixed schema per printing, temperature
  0, null wherever it cannot answer.
- **Reusable:** it should serve other tasks on this project, not only stamps.

Spec (upper 93645, alyssa 93655):

- Python in this repo, built as a reusable **classify-rows-against-a-schema**
  step. The caller passes the schema; the schema is not bound into the step.
  First schema: stamps and designs. Next: the TCGplayer product-to-printing
  join (1,574 ambiguous cards) and the cn labels.
- One Claude API call per printing. Input: the card's existing design
  reference images plus the printing image. Output through a single tool with
  a fixed schema: `design_match` (a candidate design id or `new`), `stamped`
  (bool), `stamp_text` (string or null), `stamp_color` (string or null),
  `confidence` (0 to 1). Field names follow the repo's schema: `stamp_color` is
  the same field the `distribution` carries, so results compare and write
  without a mapping.
- Start on `claude-fable-5-1`; the grade decides whether a cheaper model holds.
- **Grade first, write nothing:** run over every labelled printing on the 14
  cards (the base rows, the parallel lane, and the stamped en designs), compare
  to the rows, report the confusion per field and the parked count.
- **Leave one out when grading:** the printing being graded must not appear
  among its own design's reference images, or the model matches the image to
  itself and the grade is inflated. A design whose only member is the printing
  under grade has no reference left; the right answer there is `new`, and the
  grade counts it that way.
- Misses go on the exception list. The Robin class stays parked whatever the
  grade says.
- API key read from the environment inside the script: never on a command
  line, never printed, never in a post.

## 5. Parked

- **OP12-098 cn _02** (`prt_eykqpay2423a`, starter deck, product `cn:129`):
  labelled parallel, but the image is a second cn base. No row.
- **The 106 cn double-base cards** (section 3): cn has no reprint variant, so
  two cn "base" printings may be one unstamped and one stamped in hand with
  identical pixels. Robin cn 03 is the known case.
- **The Robin class:** printings whose stamp cannot be seen in the site image
  (watermark across the stamp, one shared image for two packs). Stamp colour
  for these comes from Mike's hand.

## 6. Open questions for Mike (already asked; not re-asking)

- **Should stamped designs star as exclusives?** (new, raised by alyssa at the
  halt, general 93743.) Today a stamped design printed in one language (a
  Release Event stamp that exists only in English) counts as a language
  exclusive, so OP10-033 shows 5 stars. If only distinct art should star, it
  is a one-line change in the app.

- **Robin cn pair colours:** of the two Chinese Robin copies, which pack is gold
  and which is black? Does he have the finals-entry-pack Robin (black-and-white
  art, a third design on art alone)?
- **Finish-only parallels:** proceeding on the assumption that a parallel with
  the same illustration but a different finish (the OP10 textured frames) is
  its own design, as for stamps.
- **Translated in-art text:** proceeding on the assumption that it stays one
  design (section 2, item 6).
- **Five OP10 finish-only items held** with no rows: OP10-105, -108, -114,
  -018, -079 (_p1/_p2). They depend on the finish-only answer and sit on cards
  with no design yet.

## 7. Still due while halted

- Read the 2026-10-16 04:23Z koala-kollect-data daily run by its run id
  (`gh run list --repo hotavocado/koala-kollect-data --workflow daily.yml`):
  expect cn origin counters 18 matched / 7 unmatched / 0 ambiguous and a Commit
  step that succeeded. This is the one unattended check still due.
- The app's 06:00Z sync carries `53b3616` and `1614489` to prod. Read back
  the exclusive stars on the 14 cards afterwards. The app's rule (alyssa,
  general 93728, confirmed against the merged code at 93739): a design is
  exclusive when all its printings are in one language, the card carries
  another language, and no unreviewed printing sits in a language the design
  lacks; stamps count, there is no unstamped filter. Expected from the data at
  `1614489`: **17 stars** — 5 on OP10-033 (jp _p1, cn _01, and three stamped
  en/tcgcsv designs), 1 on each of the other 12 cards (their stamped
  single-language design), 0 on OP12-098 while cn _02 is parked unreviewed.
