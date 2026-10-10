"""DON from tcgcsv (CONTRACT.md, DON), on verbatim rows of the 2026-10-09 snapshot.

fixtures/tcgcsv_don.json holds four groups, rows copied as tcgcsv served them:
PRB-01 (DON!! Card (Uta), priced Normal and Foil, its gold 586181, and a
non-DON card), OP-PR (the colour-series DON!! Card (Gold) 482236 and Red
482237, the two unpriced Worlds promos 561656 and 619595, and Rarity PR
683969), ST-01 PRE (a group abbreviation with a space) and OP16 (only the
sealed Special DON!! Card Pack DP-11, no card data).
"""
import hashlib
import itertools
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run
import tcgcsv

FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "tcgcsv_don.json").read_text("utf-8"))
T1, T2 = "2026-10-09T00:58:26Z", "2026-10-10T00:58:26Z"
UNPRICED = [561656, 619595]
# This fixture is DON only; the Release Event stamps have their own fixture and
# tests (test_stamps.py). With no RE group admitted, plan_stamps reads nothing.
_no_re = mock.patch.object(tcgcsv, "RE_GROUPS", set())


def setUpModule():
    _no_re.start()


def tearDownModule():
    _no_re.stop()


def fixture():
    return ([dict(g) for g in FIX["groups"]],
            {int(k): [json.loads(json.dumps(p)) for p in v] for k, v in FIX["products"].items()},
            {int(k): [dict(r) for r in v] for k, v in FIX["prices"].items()})


class Plan(unittest.TestCase):
    def setUp(self):
        self.specs, self.unpriced, self.dons = tcgcsv.plan(*fixture())
        self.by = {(s["product_id"], s["sub_type"]): s for s in self.specs}

    def test_don_is_keyed_on_card_type(self):
        # 8 DON in the fixture: Rarity DON!! and Rarity PR alike.
        self.assertEqual(self.dons, 8)
        self.assertIn((683969, "Normal"), self.by)  # Rarity PR, CardType DON!!

    def test_sealed_don_pack_is_not_a_don(self):
        # Special DON!! Card Pack DP-11 has no extendedData, so no CardType.
        self.assertNotIn(701593, {s["product_id"] for s in self.specs})
        self.assertNotIn(701593, self.unpriced)

    def test_a_non_don_card_is_not_read(self):
        self.assertNotIn(586178, {s["product_id"] for s in self.specs})

    def test_one_printing_per_finish(self):
        self.assertEqual(self.by[(593826, "Normal")]["variant"], "normal")
        self.assertEqual(self.by[(593826, "Foil")]["variant"], "foil")
        self.assertEqual(self.by[(482237, "Foil")]["variant"], "foil")

    def test_gold_is_its_normals_printing(self):
        gold = self.by[(586181, "Foil")]
        self.assertEqual(gold["variant"], "gold")
        self.assertEqual(gold["card_product_id"], 593826)
        self.assertEqual(gold["design"], "PRB-01:don-card-uta")

    def test_gold_with_no_normal_in_its_group_is_a_normal_product(self):
        # 482236 sits in the colour series; no plain DON!! Card in OP-PR. Inference, listed in the PR.
        s = self.by[(482236, "Foil")]
        self.assertEqual((s["variant"], s["card_product_id"]), ("foil", 482236))
        self.assertEqual(s["design"], "OP-PR:don-card-gold")

    def test_abbreviation_with_a_space(self):
        self.assertEqual(self.by[(434340, "Normal")]["design"], "ST-01-PRE:don-card")


class Unpriced(unittest.TestCase):
    """upper 87490: no price row, no finish, no key. Refused and counted, never minted."""

    def test_561656_is_refused(self):
        _, unpriced, _ = tcgcsv.plan(*fixture())
        self.assertIn(561656, unpriced)

    def test_619595_is_refused(self):
        _, unpriced, _ = tcgcsv.plan(*fixture())
        self.assertIn(619595, unpriced)

    def test_unpriced_never_reaches_the_finish_rule(self):
        # The red control: record every product the finish rule is asked about.
        seen = []
        real = tcgcsv.variant

        def spy(gold, sub_type):
            seen.append(sub_type)
            return real(gold, sub_type)
        with mock.patch.object(tcgcsv, "variant", spy):
            specs, unpriced, _ = tcgcsv.plan(*fixture())
        self.assertEqual(sorted(unpriced), UNPRICED)
        self.assertEqual(len(seen), len(specs))
        self.assertFalse({s["product_id"] for s in specs} & set(UNPRICED))

    def test_a_price_row_appearing_mints_it(self):
        g, p, pr = fixture()
        pr[17675].append({"productId": 561656, "subTypeName": "Foil"})
        specs, unpriced, _ = tcgcsv.plan(g, p, pr)
        self.assertEqual(unpriced, [619595])
        self.assertIn((561656, "Foil"), {(s["product_id"], s["sub_type"]) for s in specs})


class FinishRule(unittest.TestCase):
    def test_gold_priced_normal_stops_the_run(self):
        g, p, pr = fixture()
        pr[23496].append({"productId": 586181, "subTypeName": "Normal"})
        with self.assertRaisesRegex(ValueError, "gold is always foil"):
            tcgcsv.plan(g, p, pr)

    def test_unknown_finish_stops_the_run(self):
        g, p, pr = fixture()
        pr[17675].append({"productId": 482237, "subTypeName": "Holofoil"})
        with self.assertRaisesRegex(ValueError, "Holofoil"):
            tcgcsv.plan(g, p, pr)

    def test_two_normals_with_one_name_in_a_group_stop_the_run(self):
        g, p, pr = fixture()
        twin = json.loads(json.dumps(next(x for x in p[17675] if x["productId"] == 482237)))
        twin["productId"] = 999999
        p[17675].append(twin)
        with self.assertRaisesRegex(ValueError, "two DON products named"):
            tcgcsv.plan(g, p, pr)

    def test_a_don_group_with_no_price_file_stops_the_run(self):
        g, p, pr = fixture()
        del pr[23496]
        with self.assertRaisesRegex(ValueError, "no price file"):
            tcgcsv.plan(g, p, pr)


class Run(unittest.TestCase):
    """Through run.run on a temp repo, the way the daily job calls it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo, self.pages = self.tmp / "repo", self.tmp / "pages"
        self.repo.mkdir()
        self.clock = itertools.count(1_791_331_200)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def pageset(self, t, groups=None, products=None, prices=None):
        g, p, pr = fixture()
        g, p, pr = groups or g, products or p, prices or pr
        d = self.pages / "tcgcsv"
        shutil.rmtree(d, ignore_errors=True)
        for gid, rows in p.items():
            tcgcsv._write(d / "products" / f"{gid}.json", {"fetched_at": t, "products": rows})
        for gid, rows in pr.items():
            tcgcsv._write(d / "prices" / f"{gid}.json", {"fetched_at": t, "prices": rows})
        tcgcsv._write(d / "_groups.json", {"fetched_at": t, "groups": g})

    def go(self):
        return run.run(self.pages, self.repo, [], now=lambda: float(next(self.clock)))

    def rows(self, d, site="tcgcsv"):
        return run.read_jsonl(self.repo / "data" / d / f"{site}.jsonl")

    def data(self):
        return {p.relative_to(self.repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((self.repo / "data").rglob("*.jsonl"))} | {
            "manifest.json": hashlib.sha256((self.repo / "manifest.json").read_bytes()).hexdigest()}

    def test_first_run(self):
        self.pageset(T1)
        res = self.go()
        r = res["tcgcsv_run"]
        self.assertEqual(r["run"]["site"], "tcgcsv")
        self.assertEqual(r["run"]["blocks_parsed"], 8)
        self.assertEqual(r["run"]["unpriced"], 2)
        self.assertEqual(r["run"]["no_image"], 0)
        self.assertEqual(r["counts"]["unpriced"], UNPRICED)
        locs = self.rows("printing_locators")
        self.assertEqual(sorted(locs), ["tcgcsv:434340:Normal", "tcgcsv:482236:Foil", "tcgcsv:482237:Foil",
                                        "tcgcsv:586181:Foil", "tcgcsv:593826:Foil", "tcgcsv:593826:Normal",
                                        "tcgcsv:683969:Normal"])
        prts = self.rows("printings")
        cards = run.read_jsonl(self.repo / "data" / "cards.jsonl")
        # 5 cards: Uta (normal, foil, gold), Gold, Red, Netflix Chopper, ST-01 PRE plain.
        self.assertEqual(len(cards), 5)
        uta = {prts[locs[k]["printing_key"]]["card_key"] for k in
               ("tcgcsv:593826:Normal", "tcgcsv:593826:Foil", "tcgcsv:586181:Foil")}
        self.assertEqual(len(uta), 1)
        card = cards[uta.pop()]
        self.assertEqual({k: card[k] for k in ("don_design", "category", "facts_site")},
                         {"don_design": "PRB-01:don-card-uta", "category": "don", "facts_site": "tcgcsv"})
        self.assertNotIn("number", card)
        gold = prts[locs["tcgcsv:586181:Foil"]["printing_key"]]
        self.assertEqual((gold["variant"], gold["rarity"], gold["source_text"], gold["block_icon"]),
                         ("gold", "DON", "DON!! Card (Uta) (Gold)", None))
        self.assertEqual(gold["image_url"], "https://tcgplayer-cdn.tcgplayer.com/product/586181_in_1000x1000.jpg")
        obs = self.rows("card_observations")
        names = sorted(o["name"] for o in obs.values())
        # The card's name is its normal product's, never the gold's.
        self.assertEqual(names, ["DON!! Card", "DON!! Card (Gold)", "DON!! Card (Netflix Tony Tony.Chopper)",
                                 "DON!! Card (Red)", "DON!! Card (Uta)"])
        self.assertTrue(all(o["lang"] == "en" and o["category"] == "don" for o in obs.values()))
        self.assertEqual(json.loads((self.repo / "state" / "tcgcsv.json").read_text()), {"groups_fetched_at": T1})

    def test_rerun_is_byte_identical(self):
        self.pageset(T1)
        self.go()
        before = self.data()
        res = self.go()
        self.assertEqual(self.data(), before)
        self.assertEqual((res["tcgcsv_run"]["run"]["added"], res["tcgcsv_run"]["run"]["changed"]), (0, 0))

    def test_older_page_set_is_refused(self):
        self.pageset(T2)
        self.go()
        before = self.data()
        self.pageset(T1)
        with self.assertRaisesRegex(run.RunError, "older than the data"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_a_rename_keeps_the_card_and_versions_the_name(self):
        self.pageset(T1)
        self.go()
        cards = run.read_jsonl(self.repo / "data" / "cards.jsonl")
        g, p, pr = fixture()
        for x in p[17675]:
            if x["productId"] == 482237:
                x["name"] = "DON!! Card (Red) (Color Series)"
        self.pageset(T2, g, p, pr)
        self.go()
        after = run.read_jsonl(self.repo / "data" / "cards.jsonl")
        self.assertEqual(after, cards, "no card minted, none rewritten: don_design is set once")
        red = [c for c in after.values() if c["don_design"] == "OP-PR:don-card-red"]
        self.assertEqual(len(red), 1)
        obs = [o for o in self.rows("card_observations").values() if o["card_key"] == red[0]["key"]]
        self.assertEqual(sorted((o["name"], "superseded_at" in o) for o in obs),
                         [("DON!! Card (Red)", True), ("DON!! Card (Red) (Color Series)", False)])

    def test_a_rename_that_parts_a_gold_from_its_normal_stops_the_run(self):
        # The gold's printings already sit on Uta's card. Renaming the normal alone
        # leaves the gold unpaired, so it would claim a card of its own: refuse.
        self.pageset(T1)
        self.go()
        before = self.data()
        g, p, pr = fixture()
        for x in p[23496]:
            if x["productId"] == 593826:
                x["name"] = "DON!! Card (Uta) (Alternate Art)"
        self.pageset(T2, g, p, pr)
        with self.assertRaisesRegex(run.RunError, "586181 and 593826 resolve to one card"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_a_rename_keeps_the_card_of_a_gold_whose_normal_is_unpriced(self):
        # codex round 2 on PR 15: the unpriced normal has no locator, so the card is
        # reached through the gold's own locator, never re-minted from the new name.
        g, p, pr = fixture()
        pr[23496] = [x for x in pr[23496] if x["productId"] != 593826]
        self.pageset(T1, g, p, pr)
        self.go()
        gold_loc = "tcgcsv:586181:Foil"
        card = self.rows("printings")[self.rows("printing_locators")[gold_loc]["printing_key"]]["card_key"]
        cards = run.read_jsonl(self.repo / "data" / "cards.jsonl")
        for x in p[23496]:
            if x["productId"] == 593826:
                x["name"] = "DON!! Card (Uta) (Alternate Art)"
            if x["productId"] == 586181:
                x["name"] = "DON!! Card (Uta) (Alternate Art) (Gold)"
        self.pageset(T2, g, p, pr)
        self.go()
        self.assertEqual(run.read_jsonl(self.repo / "data" / "cards.jsonl"), cards, "no card minted")
        self.assertEqual(self.rows("printings")[self.rows("printing_locators")[gold_loc]["printing_key"]]["card_key"],
                         card)
        obs = [o for o in self.rows("card_observations").values() if o["card_key"] == card]
        self.assertEqual(sorted((o["name"], "superseded_at" in o) for o in obs),
                         [("DON!! Card (Uta)", True), ("DON!! Card (Uta) (Alternate Art)", False)])

    def test_the_finish_is_in_every_locator(self):
        self.pageset(T1)
        self.go()
        for loc in self.rows("printing_locators").values():
            pid, finish = loc["image_id"].split(":")
            self.assertTrue(pid.isdigit())
            self.assertIn(finish, ("Normal", "Foil"))

    def test_a_product_gaining_a_finish_does_not_rekey(self):
        self.pageset(T1)
        self.go()
        before = self.rows("printing_locators")
        g, p, pr = fixture()
        pr[17675].append({"productId": 482237, "subTypeName": "Normal"})
        self.pageset(T2, g, p, pr)
        self.go()
        after = self.rows("printing_locators")
        self.assertEqual({k: v["printing_key"] for k, v in before.items()},
                         {k: v["printing_key"] for k, v in after.items() if k in before})
        self.assertEqual(set(after) - set(before), {"tcgcsv:482237:Normal"})

    def test_no_image_on_tcgcsv_means_no_image_url_until_it_has_one(self):
        # 677570/677571/719824 on 2026-10-09: imageCount 0 and the CDN answers 403 (upper 87556).
        g, p, pr = fixture()
        for x in p[23496]:
            if x["productId"] == 586181:
                x["imageCount"] = 0
        self.pageset(T1, g, p, pr)
        r = self.go()["tcgcsv_run"]
        self.assertEqual((r["run"]["no_image"], r["counts"]["no_image"]), (1, [586181]))
        gold = self.rows("printings")[self.rows("printing_locators")["tcgcsv:586181:Foil"]["printing_key"]]
        self.assertNotIn("image_url", gold)
        others = [x for k, x in self.rows("printings").items() if k != gold["key"]]
        self.assertEqual(len(others), 6)
        self.assertTrue(all("image_url" in x for x in others))
        # The day TCGplayer has the image, the printing gains its URL: one changed row.
        self.pageset(T2)
        r = self.go()["tcgcsv_run"]
        self.assertEqual((r["run"]["no_image"], r["run"]["added"], r["run"]["changed"]), (0, 0, 1))
        gold = self.rows("printings")[self.rows("printing_locators")["tcgcsv:586181:Foil"]["printing_key"]]
        self.assertEqual(gold["image_url"], "https://tcgplayer-cdn.tcgplayer.com/product/586181_in_1000x1000.jpg")

    def test_provenance_url_only_on_the_tabled_product(self):
        # 683969 (Netflix Chopper) is in tcgcsv.PROVENANCE and in the fixture; nothing else in the fixture is.
        self.pageset(T1)
        self.go()
        locs, prts = self.rows("printing_locators"), self.rows("printings")
        chopper = prts[locs["tcgcsv:683969:Normal"]["printing_key"]]
        self.assertEqual(chopper["provenance_url"], "https://en.onepiece-cardgame.com/images/topics/028/01.png")
        others = [x for k, x in prts.items() if k != chopper["key"]]
        self.assertEqual(len(others), 6)
        self.assertTrue(all("provenance_url" not in x for x in others))

    def test_provenance_table_is_bandai_en_only(self):
        self.assertEqual(sorted(tcgcsv.PROVENANCE), [677559, 677560, 683969])
        self.assertTrue(all(u.startswith("https://en.onepiece-cardgame.com/") and "?" not in u and "#" not in u
                            for u in tcgcsv.PROVENANCE.values()))

    def test_bandai_cards_untouched_by_a_tcgcsv_only_run(self):
        self.pageset(T1)
        res = self.go()
        self.assertEqual(res["sites"], {})
        self.assertFalse((self.repo / "data" / "printings" / "en.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
