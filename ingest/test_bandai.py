"""Parser tests on verbatim cuts of real pages (tests/fixtures, fetched 2026-10-08).

Each fixture keeps the series dropdown and a few chosen blocks byte-for-byte, so
the parser is tested against what the sites actually serve, not against markup
written to suit it. The controls at the bottom break a real page the ways a live
fetch breaks and assert the done-check's two counts disagree, or a required
field goes blank.
"""
import unittest
from pathlib import Path

from bandai import independent_count, parse_page, series_options

FIX = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return (FIX / name).read_text("utf-8")


class Bandai(unittest.TestCase):
    def test_counts_agree_on_every_fixture(self):
        for f in sorted(FIX.glob("*.html")):
            t = f.read_text("utf-8")
            with self.subTest(fixture=f.name):
                self.assertGreater(len(parse_page(t)), 0)
                self.assertEqual(len(parse_page(t)), independent_count(t))

    def test_required_fields_present_on_every_fixture(self):
        for f in sorted(FIX.glob("*.html")):
            for b in parse_page(f.read_text("utf-8")):
                for k in ("number", "rarity", "category", "name", "image_src"):
                    with self.subTest(fixture=f.name, image_id=b["image_id"], field=k):
                        self.assertTrue(b[k])

    def test_en_leader_life_is_labelled(self):
        b = {x["image_id"]: x for x in parse_page(load("en_569117.html"))}["OP17-001"]
        self.assertEqual(b["category"], "LEADER")
        self.assertEqual((b["cost_label"], b["cost"]), ("Life", "5"))
        self.assertEqual(b["name"], "Edward.Newgate")
        self.assertEqual(b["attributes"], ["Special"])
        self.assertEqual(b["block_icon"], "5")
        self.assertEqual(b["source_text"], "BOOSTER PACK -THE WORLD’S STRONGEST WARRIORS- [OP-17]")

    def test_en_absent_provenance_is_empty_string(self):
        # ST14-010_r1 has no getInfo div on the live page (2026-10-08).
        b = {x["image_id"]: x for x in parse_page(load("en_569026.html"))}["ST14-010_r1"]
        self.assertEqual(b["source_text"], "")

    def test_leader_life_label_on_every_language(self):
        # Same markup on all four sites; only the label text is translated.
        for fixture, life in [("asia-en_556117.html", "Life"), ("jp_550117.html", "ライフ"),
                              ("tc_554117.html", "生命值")]:
            with self.subTest(fixture=fixture):
                b = {x["image_id"]: x for x in parse_page(load(fixture))}
                self.assertEqual(b["OP17-001"]["category"], "LEADER")
                self.assertEqual(b["OP17-001"]["cost_label"], life)
                self.assertIn("OP17-001_p1", b)
                self.assertEqual(b["OP17-019"]["category"], "EVENT")

    def test_jp_category_typo_kept_raw(self):
        # Bandai's jp page prints P-160 as "キャラクタークター" (2026-10-08). The parser
        # keeps it verbatim; mapping to the schema vocabulary is step 3's job.
        b = {x["image_id"]: x for x in parse_page(load("jp_550901.html"))}
        self.assertEqual(b["P-160"]["category"], "キャラクタークター")
        self.assertEqual(b["P-001"]["category"], "CHARACTER")

    def test_en_event_trigger_and_dash_values(self):
        b = {x["image_id"]: x for x in parse_page(load("en_569117.html"))}["OP17-019"]
        self.assertEqual(b["name"], "I Don't Have Time to Chat with Snot-Nosed Brats")
        self.assertIsNone(b["power"])
        self.assertEqual(b["attributes"], [])
        self.assertTrue(b["trigger"].startswith("[Trigger]"))

    def test_suffixed_printings_keep_their_own_id(self):
        ids = [b["image_id"] for b in parse_page(load("en_569901.html"))]
        self.assertIn("P-001_p2", ids)
        for i in ids:
            self.assertEqual(i, i.strip())

    def test_series_dropdown(self):
        opts = dict(series_options(load("en_569117.html")))
        self.assertIn("569117", opts)
        self.assertIn("569901", opts)

    def test_series_label_drops_escaped_br(self):
        # The page serves "PREMIUM BOOSTER &lt;br class=&quot;spInline&quot;&gt;-ONE ...".
        opts = dict(series_options(load("en_569117.html")))
        self.assertEqual(opts["569301"], "PREMIUM BOOSTER -ONE PIECE CARD THE BEST- [PRB-01]")
        for label in opts.values():
            self.assertNotIn("<", label)

    def test_effect_keeps_attribute_marker(self):
        # en_569103_slash.html: two blocks cut from series 569103. OP03-032 serves
        # the marker raw ("by <Slash> attribute"), OP03-008 escaped (&lt;Slash&gt;).
        # Both must come out as <Slash>; neither may lose the word.
        effects = {b["image_id"]: b["effect"] for b in parse_page(load("en_569103_slash.html"))}
        self.assertEqual(set(effects), {"OP03-032", "OP03-008"})
        for image_id, effect in effects.items():
            self.assertIn("<Slash>", effect, image_id)
            self.assertNotIn("by  ", effect, image_id)
            self.assertNotIn("<br", effect, image_id)

    # Controls: a live fetch breaks these ways, and the check must go red.
    def test_control_cut_mid_block(self):
        # Cut just after the block's image tag and before its </dl>: the image is
        # referenced, the block never closes. (A cut before the image drops both
        # counts together; that case is count_drop's job, not this check's.)
        t = load("en_569117.html")
        start = t.index('<dl class="modalCol" id="OP17-019"')
        img = t.index("/images/cardlist/card/OP17-019.png", start)
        cut = t[: t.index(">", img) + 1]
        self.assertLess(len(cut), t.index("</dl>", start))
        self.assertNotEqual(len(parse_page(cut)), independent_count(cut))

    def test_control_renamed_class_blanks_a_required_field(self):
        t = load("en_569117.html").replace('class="cardName"', 'class="cardTitle"')
        self.assertTrue(all(b["name"] is None for b in parse_page(t)))


if __name__ == "__main__":
    unittest.main()
