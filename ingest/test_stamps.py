"""Release Event stamps from tcgcsv (CONTRACT.md, Release Event stamps), on verbatim rows.

fixtures/tcgcsv_stamps.json holds the OP17 RE group (24775) as tcgcsv served it
on 2026-10-09: Atmos OP17-002 (C), Haruta OP17-009 (UC), the Event Ga Ha Ha
Ha!! OP17-017 (C), and the group's sealed 4th Anniversary Tournament Pack, which
has no Number. Its cards and en_base are those three set cards and their en base
printings as koala-kollect-data main holds them. The runs also carry the DON
fixture, as the daily page set does.
"""
import hashlib
import itertools
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run
import tcgcsv
import test_tcgcsv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from data_check import check_data  # noqa: E402

FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "tcgcsv_stamps.json").read_text("utf-8"))
GID = 24775
GROUP_NAME = "The World's Strongest Warriors Release Event Cards"
URL = "https://tcgcsv.com/tcgplayer/68/24775/products"
STAMPS = [712666, 712669, 712677]
SEALED = 712741
T1, T2 = "2026-10-09T14:01:28Z", "2026-10-10T14:01:28Z"


def fixture():
    """The DON fixture plus the OP17 RE group, as (groups, products, prices)."""
    g, p, pr = test_tcgcsv.fixture()
    g.append(dict(FIX["group"]))
    p[GID] = [json.loads(json.dumps(x)) for x in FIX["products"]]
    pr[GID] = [dict(r) for r in FIX["prices"]]
    return g, p, pr


def product(p, pid):
    return next(x for x in p[GID] if x["productId"] == pid)


def set_ext(prod, name, value):
    next(e for e in prod["extendedData"] if e["name"] == name)["value"] = value


class Plan(unittest.TestCase):
    def setUp(self):
        self.stamps, self.unpriced, self.count = tcgcsv.plan_stamps(*fixture())
        self.by = {s["product_id"]: s for s in self.stamps}

    def test_a_stamp_is_a_numbered_product_in_an_admitted_group(self):
        self.assertEqual((sorted(self.by), self.count, self.unpriced), (STAMPS, 3, []))

    def test_the_sealed_pack_is_not_a_stamp(self):
        self.assertNotIn(SEALED, self.by)

    def test_a_stamp_row(self):
        self.assertEqual(self.by[712669], {
            "product_id": 712669, "sub_type": "Normal", "variant": "stamped", "number": "OP17-009",
            "rarity": "UC", "name": "Haruta", "group_id": GID, "group_name": GROUP_NAME, "has_image": True})

    def test_dons_are_not_stamps_and_stamps_are_not_dons(self):
        specs, _, dons = tcgcsv.plan(*fixture())
        self.assertEqual(dons, 8)
        self.assertFalse({s["product_id"] for s in specs} & set(STAMPS))

    def test_a_group_not_admitted_is_not_read(self):
        with mock.patch.object(tcgcsv, "RE_GROUPS", set()):
            self.assertEqual(tcgcsv.plan_stamps(*fixture()), ([], [], 0))

    def test_a_stamp_priced_foil_stops_the_run(self):
        g, p, pr = fixture()
        pr[GID].append({"productId": 712666, "subTypeName": "Foil"})
        with self.assertRaisesRegex(ValueError, "stamp priced 'Foil'"):
            tcgcsv.plan_stamps(g, p, pr)

    def test_an_unpriced_stamp_is_refused_and_counted(self):
        g, p, pr = fixture()
        pr[GID] = [r for r in pr[GID] if r["productId"] != 712669]
        stamps, unpriced, count = tcgcsv.plan_stamps(g, p, pr)
        self.assertEqual((unpriced, count), ([712669], 3))
        self.assertNotIn(712669, {s["product_id"] for s in stamps})

    def test_an_admitted_group_missing_from_groups_stops_the_run(self):
        g, p, pr = fixture()
        with self.assertRaisesRegex(ValueError, "24775 is not in /groups"):
            tcgcsv.plan_stamps([x for x in g if x["groupId"] != GID], p, pr)

    def test_an_admitted_group_with_no_stamp_stops_the_run(self):
        g, p, pr = fixture()
        p[GID] = [product(p, SEALED)]
        with self.assertRaisesRegex(ValueError, "carries no stamped card"):
            tcgcsv.plan_stamps(g, p, pr)

    def test_an_admitted_group_with_no_price_file_stops_the_run(self):
        g, p, pr = fixture()
        del pr[GID]
        with self.assertRaisesRegex(ValueError, "no price file"):
            tcgcsv.plan_stamps(g, p, pr)

    def test_a_stamp_with_no_rarity_stops_the_run(self):
        g, p, pr = fixture()
        prod = product(p, 712666)
        prod["extendedData"] = [e for e in prod["extendedData"] if e["name"] != "Rarity"]
        with self.assertRaisesRegex(ValueError, "712666 .OP17-002. has no Rarity"):
            tcgcsv.plan_stamps(g, p, pr)


class Fetch(unittest.TestCase):
    def test_an_admitted_group_has_its_prices_fetched_though_it_carries_no_don(self):
        g, p, pr = fixture()
        served = {"groups": g, **{f"{gid}/products": rows for gid, rows in p.items()},
                  **{f"{gid}/prices": rows for gid, rows in pr.items()}}
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        with mock.patch.object(tcgcsv, "_get", lambda path: served[path]), mock.patch.object(tcgcsv.time, "sleep"):
            tcgcsv.fetch_pageset(tmp)
        self.assertFalse(any(tcgcsv.is_don(x) for x in p[GID]))
        self.assertTrue((tmp / "prices" / f"{GID}.json").exists())


class Run(unittest.TestCase):
    """Through run.run on a temp repo that holds the three set cards and their en base printings."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo, self.pages = self.tmp / "repo", self.tmp / "pages"
        (self.repo / "data" / "printings").mkdir(parents=True)
        self.seed()
        self.clock = itertools.count(1_791_331_200)

    def seed(self, en_base=None):
        (self.repo / "data" / "cards.jsonl").write_text(
            "".join(run.line("card", c) + "\n" for c in FIX["cards"]), "utf-8")
        (self.repo / "data" / "printings" / "en.jsonl").write_text(
            "".join(run.line("printing", p) + "\n" for p in (en_base or FIX["en_base"])), "utf-8")

    def pageset(self, t, g=None, p=None, pr=None):
        fg, fp, fpr = fixture()
        g, p, pr = g or fg, p or fp, pr or fpr
        d = self.pages / "tcgcsv"
        shutil.rmtree(d, ignore_errors=True)
        for gid, rows in p.items():
            tcgcsv._write(d / "products" / f"{gid}.json", {"fetched_at": t, "products": rows})
        for gid, rows in pr.items():
            tcgcsv._write(d / "prices" / f"{gid}.json", {"fetched_at": t, "prices": rows})
        tcgcsv._write(d / "_groups.json", {"fetched_at": t, "groups": g})

    def go(self):
        return run.run(self.pages, self.repo, [], now=lambda: float(next(self.clock)))

    def jsonl(self, rel):
        return run.read_jsonl(self.repo / "data" / rel)

    def stamps(self):
        return {k: x for k, x in self.jsonl("printings/tcgcsv.jsonl").items() if x["variant"] == "stamped"}

    def data(self):
        return {p.relative_to(self.repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((self.repo / "data").rglob("*.jsonl"))} | {
            "manifest.json": hashlib.sha256((self.repo / "manifest.json").read_bytes()).hexdigest()}

    def test_first_run(self):
        self.pageset(T1)
        r = self.go()["tcgcsv_run"]
        c = r["counts"]
        self.assertEqual((c["dons"], c["stamps"], r["run"]["blocks_parsed"]), (8, 3, 11))
        self.assertEqual((r["run"]["unmatched"], r["run"]["rarity_disagreements"]), (0, 0))
        card_of = {c["number"]: c["key"] for c in FIX["cards"]}
        locs = self.jsonl("printing_locators/tcgcsv.jsonl")
        stamps = self.stamps()
        self.assertEqual(len(stamps), 3)
        atmos = stamps[locs["tcgcsv:712666:Normal"]["printing_key"]]
        self.assertEqual({k: v for k, v in atmos.items() if k not in ("key", "first_seen_at")}, {
            "card_key": card_of["OP17-002"], "site": "tcgcsv", "rarity": "C", "variant": "stamped",
            "image_url": "https://tcgplayer-cdn.tcgplayer.com/product/712666_in_1000x1000.jpg",
            "source_text": "Atmos", "block_icon": None})
        self.assertEqual(stamps[locs["tcgcsv:712669:Normal"]["printing_key"]]["card_key"], card_of["OP17-009"])
        self.assertEqual(stamps[locs["tcgcsv:712677:Normal"]["printing_key"]]["card_key"], card_of["OP17-017"])
        self.assertNotIn("tcgcsv:712741:Normal", locs)
        # One distribution for the group, one claim per stamp, undated.
        dists = self.jsonl("distributions.jsonl")
        self.assertEqual(list(dists.values()), [{
            "key": next(iter(dists)), "site": "tcgcsv", "region": "en", "kind": "event_pack",
            "name": GROUP_NAME, "source_url": URL}])
        claims = self.jsonl("printing_distributions.jsonl")
        self.assertEqual(sorted(x["printing_key"] for x in claims.values()), sorted(stamps))
        for k, x in claims.items():
            self.assertEqual(k, run.claim_key(x["printing_key"], next(iter(dists)), "tcgcsv", URL))
            self.assertEqual({f: v for f, v in x.items() if f not in ("key", "printing_key")}, {
                "distribution_key": next(iter(dists)), "source": "tcgcsv", "source_url": URL, "quote": GROUP_NAME,
                "confidence": "corroborated", "observed_at": T1})

    def test_a_stamp_mints_no_card_and_no_observation(self):
        self.pageset(T1)
        self.go()
        cards = run.read_jsonl(self.repo / "data" / "cards.jsonl")
        # The 3 set cards, untouched, and the 5 DON cards; nothing else.
        self.assertEqual(len(cards), 8)
        for c in FIX["cards"]:
            self.assertEqual(cards[c["key"]], c)
        obs = run.read_jsonl(self.repo / "data" / "card_observations" / "tcgcsv.jsonl")
        self.assertFalse({o["card_key"] for o in obs.values()} & {c["key"] for c in FIX["cards"]})
        self.assertFalse((self.repo / "data" / "printing_products" / "tcgcsv.jsonl").exists())

    def test_the_written_data_passes_the_data_check(self):
        self.pageset(T1)
        self.go()
        self.assertEqual(check_data(self.repo, run.SCHEMA), [])

    def test_a_number_that_is_no_card_of_ours_is_refused_and_counted(self):
        g, p, pr = fixture()
        set_ext(product(p, 712669), "Number", "OP17-999")
        self.pageset(T1, g, p, pr)
        r = self.go()["tcgcsv_run"]
        self.assertEqual((r["counts"]["unmatched"], r["run"]["unmatched"]), ([712669], 1))
        self.assertNotIn("tcgcsv:712669:Normal", self.jsonl("printing_locators/tcgcsv.jsonl"))
        self.assertEqual(len(self.stamps()), 2)
        self.assertEqual(len(run.read_jsonl(self.repo / "data" / "cards.jsonl")), 8, "no card minted")
        self.assertIn("stamps on no card of ours (refused) 1 [712669]", run.tcgcsv_line(r))

    def test_a_rarity_that_differs_from_the_en_base_is_kept_and_counted(self):
        en = [dict(p) for p in FIX["en_base"]]
        atmos_card = next(c["key"] for c in FIX["cards"] if c["number"] == "OP17-002")
        for p in en:
            if p["card_key"] == atmos_card:
                p["rarity"] = "R"
        self.seed(en)
        self.pageset(T1)
        r = self.go()["tcgcsv_run"]
        self.assertEqual((r["counts"]["rarity_disagreements"], r["run"]["rarity_disagreements"]), ([712666], 1))
        loc = self.jsonl("printing_locators/tcgcsv.jsonl")["tcgcsv:712666:Normal"]
        self.assertEqual(self.stamps()[loc["printing_key"]]["rarity"], "C", "TCGplayer's rarity, verbatim")

    def test_rerun_is_byte_identical(self):
        self.pageset(T1)
        self.go()
        before = self.data()
        r = self.go()["tcgcsv_run"]
        self.assertEqual(self.data(), before)
        self.assertEqual((r["run"]["added"], r["run"]["changed"]), (0, 0))

    def test_a_later_day_keeps_the_claims_first_observed_at(self):
        self.pageset(T1)
        self.go()
        self.pageset(T2)
        r = self.go()["tcgcsv_run"]
        self.assertEqual((r["run"]["added"], r["run"]["changed"]), (0, 0))
        self.assertEqual({x["observed_at"] for x in self.jsonl("printing_distributions.jsonl").values()}, {T1})

    def test_a_stamp_priced_foil_writes_nothing(self):
        self.pageset(T1)
        self.go()
        before = self.data()
        g, p, pr = fixture()
        pr[GID].append({"productId": 712666, "subTypeName": "Foil"})
        self.pageset(T2, g, p, pr)
        with self.assertRaisesRegex(run.RunError, "stamp priced 'Foil'"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_the_run_line_names_both_kinds(self):
        self.pageset(T1)
        line = run.tcgcsv_line(self.go()["tcgcsv_run"])
        self.assertIn("DON products 8, Release Event stamps 3,", line)
        self.assertIn("stamp rarity differs from the en base 0", line)


if __name__ == "__main__":
    unittest.main()
