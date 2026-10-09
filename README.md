# koala-kollect-data

An open, self-updating database of every One Piece Card Game card printed in
English, Japanese and Chinese (Traditional and Simplified). It records where
each printing came from: which product, event or promo, as each official site
states it, with the source and the exact quote.

This is the data half of [Koala Kollect](https://github.com/hotavocado/koala-kollect).
The schema is `schema/v1.schema.json`; layout and write rules are in
[CONTRACT.md](CONTRACT.md). Card images are linked to the official sites, never
re-hosted.

**Status:** cards, printings, products and card text for `en`, `asia-en`, `jp`
and `tc`, from each site's official card list (first run 2026-10-08), updated
daily. Each product carries that site's release date, read from the site's own
product index. `cn` printings come from the mainland-China card API, promo
origins from each site's event pages, and DON cards from tcgcsv, TCGplayer's
catalogue, because no official card list carries them (see CONTRACT.md, DON;
their images link to TCGplayer). `state/` holds what the ingest needs between runs (each page's
last clean count and hash, the series each product page links, the `cn` id
snapshot, and `keepalive.txt`, the date of the last heartbeat commit that stops
GitHub switching the daily schedule off after a quiet spell); the app does not
read it.

**Licence:** this dataset (its structure, provenance records, keys and links) is
licensed [CC BY 4.0](LICENSE): reuse it freely, crediting "Koala Kollect" with a link
to this repo. The card text quoted in it, and the card images it links to, are not
ours to license. They remain Bandai's, and you are responsible for your own use of them.

One Piece Card Game and its card text and images are owned by Bandai and Eiichiro
Oda/Shueisha/Toei Animation. This project is not affiliated with them.
