# koala-kollect-data

An open, self-updating database of every One Piece Card Game card printed in
English, Japanese and Chinese (Traditional and Simplified). It records where
each printing came from: which product, event or promo, as each official site
states it, with the source and the exact quote.

This is the data half of [Koala Kollect](https://github.com/hotavocado/koala-kollect).
The schema is `schema/v1.schema.json`; layout and write rules are in
[CONTRACT.md](CONTRACT.md). Card images are linked to the official sites, never
re-hosted.

**Status:** schema only. The ingest that fills `data/` is next.

**Licence:** this dataset (its structure, provenance records, keys and links) is
licensed [CC BY 4.0](LICENSE): reuse it freely, crediting "Koala Kollect" with a link
to this repo. The card text quoted in it, and the card images it links to, are not
ours to license. They remain Bandai's, and you are responsible for your own use of them.

One Piece Card Game and its card text and images are owned by Bandai and Eiichiro
Oda/Shueisha/Toei Animation. This project is not affiliated with them.
