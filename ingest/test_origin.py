"""Promo origin minting, bucket flags and set derivation (CONTRACT.md, Promo origin and Sets)."""
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cnmodel  # noqa: E402
import test_cnduplicates  # noqa: E402
import run  # noqa: E402

T0, T1 = "2026-10-01T00:00:00Z", "2026-10-15T00:00:00Z"
NAMI = "オフィシャルプレイマット ネフェルタリ・ビビ カードリスト"


class Origin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = run.Store(self.tmp.name)
        r = self.store.recs
        r["product"]["jp:550801"] = {"key": "jp:550801", "site": "jp", "series_id": "550801",
                                     "name": "限定商品収録カード", "kind": "limited", "first_seen_at": T0}
        r["product"]["jp:550901"] = {"key": "jp:550901", "site": "jp", "series_id": "550901",
                                     "name": "プロモーションカード", "kind": "promo_bucket", "first_seen_at": T0}
        r["product"]["jp:550117"] = {"key": "jp:550117", "site": "jp", "series_id": "550117", "code": "OP-17",
                                     "name": "ブースターパック", "kind": "booster", "first_seen_at": T0}
        for prt, prod, text in [("prt_aaaaaaaaaaaa", "jp:550801", NAMI), ("prt_bbbbbbbbbbbb", "jp:550801", NAMI),
                                ("prt_cccccccccccc", "jp:550801", "大会記念品 カードリスト"),
                                ("prt_dddddddddddd", "jp:550117", "ブースターパック")]:
            r["printing"][prt] = {"key": prt, "site": "jp", "source_text": text}
            r["printing_product"][f"{prt}@{prod}"] = {"key": f"{prt}@{prod}", "printing_key": prt,
                                                      "product_key": prod, "first_seen_at": T0}
        # prt_c has a distribution: its origin is the distribution, never a minted product.
        r["printing_distribution"]["ev_0"] = {"key": "ev_0", "printing_key": "prt_cccccccccccc"}

    def tearDown(self):
        self.tmp.cleanup()

    def mint(self, t=T1):
        counts = defaultdict(int)
        run.refresh_products(self.store)
        run.mint_origin(self.store, t, counts)
        return counts

    def minted(self):
        return {k: v for k, v in self.store.recs["printing_product"].items() if ":src_" in v["product_key"]}

    def test_buckets_flagged_and_real_products_not(self):
        self.mint()
        p = self.store.recs["product"]
        self.assertTrue(p["jp:550801"]["is_bucket"])
        self.assertTrue(p["jp:550901"]["is_bucket"])
        self.assertNotIn("is_bucket", p["jp:550117"])

    def test_one_product_per_source_text_and_a_second_row_per_printing(self):
        self.mint()
        key = run.origin_key("jp", NAMI)
        self.assertRegex(key, r"^jp:src_[0-9a-f]{12}$")
        prod = self.store.recs["product"][key]
        self.assertEqual(prod["kind"], "limited")
        self.assertNotIn("series_id", prod)
        self.assertNotIn("is_bucket", prod)
        self.assertEqual(prod["name"], "オフィシャルプレイマット ネフェルタリ・ビビ")
        self.assertEqual(set(self.minted()), {f"prt_aaaaaaaaaaaa@{key}", f"prt_bbbbbbbbbbbb@{key}"})
        # The bucket rows stay.
        self.assertIn("prt_aaaaaaaaaaaa@jp:550801", self.store.recs["printing_product"])

    def test_control_printing_with_a_distribution_mints_nothing(self):
        self.mint()
        self.assertFalse([k for k in self.minted() if k.startswith("prt_cccccccccccc")])

    def test_control_real_product_printing_mints_nothing(self):
        self.mint()
        self.assertFalse([k for k in self.minted() if k.startswith("prt_dddddddddddd")])

    def test_rerun_adds_and_changes_nothing(self):
        self.mint()
        again = self.mint()
        self.assertEqual((again["added"], again["changed"], again["removed"]), (0, 0, 0))

    def test_printing_that_gains_a_distribution_closes_its_minted_row(self):
        self.mint()
        self.store.recs["printing_distribution"]["ev_1"] = {"key": "ev_1", "printing_key": "prt_aaaaaaaaaaaa"}
        counts = self.mint()
        rows = self.minted()
        a = [v for k, v in rows.items() if k.startswith("prt_aaaaaaaaaaaa")][0]
        b = [v for k, v in rows.items() if k.startswith("prt_bbbbbbbbbbbb")][0]
        self.assertEqual(a.get("removed_at"), T1)
        self.assertNotIn("removed_at", b)
        self.assertEqual(counts["removed"], 1)


class Sets(unittest.TestCase):
    def test_global_form(self):
        for code, want in [("OP-17", ["OP-17"]), ("OPC-16", ["OP-16"]), ("EBC-02", ["EB-02"]), ("STC-31", ["ST-31"]),
                           ("PRBC-01", ["PRB-01"]), ("EBK-01", ["EB-01"]), ("OP14-EB04", ["OP-14", "EB-04"]),
                           ("OP15-EB04", ["OP-15", "EB-04"]), ("ST-29", ["ST-29"]), ("PRB-02", ["PRB-02"])]:
            with self.subTest(code=code):
                self.assertEqual(run.global_sets(code), want)

    def test_control_codes_that_name_no_set(self):
        for code in ["DP-06", "OP-1", "STC31&34", ""]:
            with self.subTest(code=code):
                self.assertEqual(run.global_sets(code), [])

    def test_cn_two_deck_starter(self):
        name = "基本卡组 红色蒙奇·D·路飞&紫色夏洛特·卡塔库栗【STC31&34】"
        self.assertEqual(cnmodel.product_code(name), "STC-31")
        self.assertEqual(cnmodel.product_codes(name), ["STC-31", "STC-34"])
        self.assertEqual(cnmodel.product_code("基本卡组 草帽一伙【STC-01】"), "STC-01")
        self.assertIsNone(cnmodel.product_code("宣传卡"))

    def test_derive(self):
        with tempfile.TemporaryDirectory() as d:
            store = run.Store(d)
            p = store.recs["product"]
            p["cn:127"] = {"key": "cn:127", "site": "cn", "series_id": "127", "code": "STC-31", "kind": "starter",
                           "name": "基本卡组 红色蒙奇·D·路飞&紫色夏洛特·卡塔库栗【STC31&34】"}
            p["cn:9"] = {"key": "cn:9", "site": "cn", "series_id": "9", "kind": "promo_bucket", "name": "宣传卡",
                         "is_bucket": True}
            p["cn:76"] = {"key": "cn:76", "site": "cn", "series_id": "76", "kind": "limited",
                          "name": "限定商品收录卡牌", "is_bucket": True}
            p["en:569402"] = {"key": "en:569402", "site": "en", "series_id": "569402", "code": "OP14-EB04",
                              "kind": "booster", "name": "x"}
            p["en:DP-06"] = {"key": "en:DP-06", "site": "en", "series_id": "DP-06", "code": "DP-06", "kind": "other",
                             "name": "x"}
            run.derive_sets(store)
            self.assertEqual(set(store.recs["product_set"]),
                             {"cn:127@ST-31", "cn:127@ST-34", "cn:9@P", "en:569402@OP-14", "en:569402@EB-04"})
            self.assertEqual(set(store.recs["set"]), {"ST-31", "ST-34", "P", "OP-14", "EB-04"})
            for s in store.recs["set"].values():
                self.assertEqual(s["key"], s["code"])


class CnRemoval(test_cnduplicates.Duplicates):
    """run_cn closes every cn: listing the list did not show. A minted cn:src_ row is no listing."""

    def test_minted_cn_row_survives_the_next_cn_run(self):
        # 4647 under the limited bucket (cn:48 in the fixture products): no distribution, so it mints.
        rows = {4647: dict(test_cnduplicates.ROWS[4647], cardOfferType="限定商品收录卡牌")}
        self.pageset([4647], T0, rows=rows)
        run.run_cn(self.store, self.cn, {})
        run.refresh_products(self.store)
        run.mint_origin(self.store, T0, defaultdict(int))
        minted = [k for k, v in self.store.recs["printing_product"].items() if ":src_" in v["product_key"]]
        self.assertEqual(len(minted), 1)
        self.pageset([4647], T1, rows=rows)
        counts, _, _ = run.run_cn(self.store, self.cn, {})
        self.assertNotIn("removed_at", self.store.recs["printing_product"][minted[0]])
        self.assertEqual(counts["removed"], 0)

    # The inherited cases are the duplicates file's own; run them there, not twice.
    test_the_op09_043_triple_is_one_printing = None
    test_the_p108_pair_is_one_printing = None
    test_boundary_one_image_under_two_products_is_two_printings = None
    test_one_field_apart_is_two_printings = None
    test_red_a_duplicate_with_its_own_printing_stops_the_run = None
    test_retiring_moves_the_locator_to_the_survivor = None
    test_red_a_retired_printing_is_never_written_again = None
    test_the_card_list_claim_names_the_lowest_id = None


if __name__ == "__main__":
    unittest.main()


class OriginCheck(unittest.TestCase):
    """data_check.origin_errors: clean on a correct mint, red for each rule on its own."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import data_check
        self.dc = data_check
        self.key = data_check.origin_key("jp", NAMI)
        self.products = [
            {"key": "jp:550801", "site": "jp", "series_id": "550801", "is_bucket": True, "kind": "limited", "name": "x"},
            {"key": self.key, "site": "jp", "kind": "limited", "name": "y"}]
        self.printings = [{"key": "prt_a", "source_text": NAMI}, {"key": "prt_c", "source_text": "大会"}]
        self.listings = [{"key": "prt_a@jp:550801", "printing_key": "prt_a", "product_key": "jp:550801"},
                         {"key": f"prt_a@{self.key}", "printing_key": "prt_a", "product_key": self.key},
                         {"key": "prt_c@jp:550801", "printing_key": "prt_c", "product_key": "jp:550801"}]
        self.claims = [{"printing_key": "prt_c"}]

    def errs(self):
        return self.dc.origin_errors(self.products, self.printings, self.listings, self.claims)

    def test_clean(self):
        self.assertEqual(self.errs(), [])
        self.assertEqual(run.origin_key("jp", NAMI), self.key)

    def test_red_minted_row_on_a_printing_with_a_distribution(self):
        self.listings.append({"key": f"prt_c@{self.key}", "printing_key": "prt_c", "product_key": self.key})
        self.assertTrue(any("has a distribution and a minted product" in e for e in self.errs()), self.errs())

    def test_red_bucket_printing_with_no_distribution_and_no_mint(self):
        self.listings.pop(1)
        self.assertTrue(any("has 0 minted product rows, not 1" in e for e in self.errs()), self.errs())

    def test_control_a_closed_minted_row_does_not_count(self):
        self.listings[1]["removed_at"] = T1
        self.assertTrue(any("has 0 minted product rows, not 1" in e for e in self.errs()), self.errs())

    def test_red_minted_key_not_the_mint_of_its_source_text(self):
        self.printings[0]["source_text"] = "別の商品"
        self.assertTrue(any("is not the mint of its source_text" in e for e in self.errs()), self.errs())

    def test_red_minted_product_with_series_id(self):
        self.products[1]["series_id"] = "550899"
        self.assertTrue(any("carries series_id" in e for e in self.errs()), self.errs())

    def test_red_bucket_without_flag_and_flag_off_bucket(self):
        del self.products[0]["is_bucket"]
        self.products.append({"key": "jp:550117", "site": "jp", "series_id": "550117", "is_bucket": True,
                              "kind": "booster", "name": "z"})
        e = self.errs()
        self.assertTrue(any("catch-all series without is_bucket" in x for x in e), e)
        self.assertTrue(any("is_bucket on a product that is no catch-all" in x for x in e), e)

    def test_red_keys_that_do_not_restate_their_fields(self):
        e = self.dc.key_errors([{"key": "OP-09", "code": "OP-10"}],
                               [{"key": "cn:1@ST-31", "product_key": "cn:1", "set_key": "ST-34"}],
                               [{"key": "prt_a", "printing_key": "prt_b"}])
        self.assertEqual(len(e), 3, e)
