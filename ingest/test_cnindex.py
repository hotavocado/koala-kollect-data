"""cn's product index (CONTRACT.md, Promo origin): the page each item names, its
date, the title fold, the schema host, and that the run hands it to mint_origin.

Rows are the cn.INDEX_FIELDS shape of products/productsinfo/webList items,
copied from the live list on 2026-10-15 (ids 66, 137, 139).
"""
import itertools
import json
import shutil
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

import cn
import model  # noqa: F401  (puts scripts/ on the path)
import products
import run
import test_cnduplicates

SLEEVE = {"id": 137, "type": 3, "name": "航海王卡牌对战 官方卡套16", "saleTime": "2026-08-22 00:00:00",
          "link": "", "newDetail": 0}
BOOSTER = {"id": 139, "type": 2, "name": "特别补充包 ONE PIECE Heroines Edition vol.2",
           "saleTime": "2026-10-31 00:00:00", "link": "", "newDetail": 1}
OP09 = {"id": 66, "type": 2, "name": "补充包 新帝降临", "saleTime": "2024-10-18 00:00:01",
        "link": "https://www.onepiece-cardgame.cn/op09", "newDetail": 1}
SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schema" / "v1.schema.json").read_text("utf-8"))


class CnIndex(unittest.TestCase):
    def test_page_is_the_one_the_site_list_opens(self):
        got = products.cn_index([SLEEVE, BOOSTER, OP09])
        self.assertEqual([it["href"] for it in got], [
            "https://www.onepiece-cardgame.cn/products/newDetail?id=137&type=3",
            "https://www.onepiece-cardgame.cn/products/detail?id=139&type=2",
            "https://www.onepiece-cardgame.cn/op09"])

    def test_date_is_the_sale_time_date_part(self):
        self.assertEqual([it["date"] for it in products.cn_index([SLEEVE, OP09])], ["2026-08-22", "2024-10-18"])

    def test_no_sale_time_is_no_date(self):
        self.assertIsNone(products.cn_index([dict(SLEEVE, saleTime="")])[0]["date"])

    def test_red_link_off_the_site_refuses(self):
        with self.assertRaises(ValueError):
            products.cn_index([dict(OP09, link="https://example.com/op09")])

    def test_red_sale_time_of_another_form_refuses(self):
        for bad in ("2026-08-22", "2026/08/22 00:00:00", "2026-08-22T00:00:00"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                products.cn_index([dict(SLEEVE, saleTime=bad)])

    def test_red_id_that_is_not_an_integer_refuses(self):
        with self.assertRaises(ValueError):
            products.cn_index([dict(SLEEVE, id="137")])

    def test_cn_game_name_folds_out(self):
        self.assertEqual(products.title_key("航海王卡牌对战 官方卡套16"), products.title_key("官方卡套16"))

    def test_control_cn_game_name_folds_only_as_a_prefix(self):
        self.assertNotEqual(products.title_key("官方卡套 航海王卡牌对战"), products.title_key("官方卡套"))


class CnOriginPage(unittest.TestCase):
    """run.origin_page over cn index items: the matching every minted cn origin goes through."""

    def page(self, name, rows, old=None):
        counts = defaultdict(int)
        out = run.origin_page(old, name, products.by_title(products.cn_index(rows)), counts)
        return out, counts

    def test_matched_title_sets_url_and_date_sourced_to_the_page(self):
        href = "https://www.onepiece-cardgame.cn/products/newDetail?id=137&type=3"
        out, counts = self.page("航海王卡牌对战 官方卡套16", [SLEEVE, BOOSTER])
        self.assertEqual(out, {"product_url": href, "release_date": "2026-08-22", "release_date_source": href})
        self.assertEqual((counts["page_matched"], counts["page_dated"]), (1, 1))

    def test_red_title_matching_two_index_pages_sets_nothing_and_counts(self):
        # Two cn items under one folded title: one with the game name, one without, other ids.
        twin = dict(SLEEVE, id=200, name="官方卡套16", saleTime="2026-09-01 00:00:00")
        old = {"product_url": "https://www.onepiece-cardgame.cn/products/newDetail?id=1&type=3"}
        out, counts = self.page("航海王卡牌对战 官方卡套16", [SLEEVE, twin], old=old)
        self.assertEqual(out, old)
        self.assertEqual((counts["page_ambiguous"], counts["page_matched"]), (1, 0))

    def test_control_the_same_page_twice_is_one_page(self):
        # by_title keys on the href set, so one item listed twice is still one page.
        out, counts = self.page("航海王卡牌对战 官方卡套16", [SLEEVE, SLEEVE])
        self.assertEqual(counts["page_matched"], 1)


class Schema(unittest.TestCase):
    def valid(self, url):
        sub = SCHEMA["$defs"]["product"]["properties"]["release_date_source"]
        return Draft202012Validator(sub).is_valid(url)

    def test_cn_pages_are_admitted(self):
        self.assertTrue(self.valid("https://www.onepiece-cardgame.cn/products/newDetail?id=137&type=3"))
        self.assertTrue(self.valid("https://www.onepiece-cardgame.cn/op09"))

    def test_control_en_and_jp_pages_still_are(self):
        self.assertTrue(self.valid("https://en.onepiece-cardgame.com/products/other/playmat009.php"))
        self.assertTrue(self.valid("https://www.onepiece-cardgame.com/products/other/playmat009.php"))

    def test_red_other_hosts_are_not(self):
        for url in ("https://www.onepiece-cardgame.cn.example.com/op09", "https://onepiece-cardgame.cn/op09",
                    "https://example.com/products/", "http://www.onepiece-cardgame.cn/op09"):
            with self.subTest(url=url):
                self.assertFalse(self.valid(url))


class Fetch(unittest.TestCase):
    def test_index_is_written_with_index_fields_only(self):
        tmp = Path(tempfile.mkdtemp())
        live = dict(SLEEVE, detailBanner=["x"], detailContent="<p>copy</p>")
        try:
            with mock.patch.object(cn, "list_all", return_value=[]), \
                 mock.patch.object(cn, "products", return_value=[]), \
                 mock.patch.object(cn, "product_index", return_value=[live]), \
                 mock.patch.object(cn.time, "sleep"):
                self.assertEqual(cn.fetch_pageset(tmp, set()), [])
            rows = json.loads((tmp / "_index.json").read_text("utf-8"))["rows"]
        finally:
            shutil.rmtree(tmp)
        self.assertEqual(rows, [SLEEVE])


class RunWiring(test_cnduplicates.Duplicates):
    """run.run hands cn/_index.json to mint_origin as indexes['cn']; no file, no cn index."""

    def indexes(self, with_index):
        self.pageset([4647], test_cnduplicates.T1)
        if with_index:
            (self.cn / "_index.json").write_text(
                json.dumps({"fetched_at": test_cnduplicates.T1, "rows": [SLEEVE, OP09]}, ensure_ascii=False), "utf-8")
        seen = {}

        def spy(store, t, counts, indexes=None):
            seen.update(indexes or {})
            return real(store, t, counts, indexes)
        real = run.mint_origin
        clock = itertools.count(1_760_000_000)
        with mock.patch.object(run, "mint_origin", spy):
            run.run(self.tmp, self.tmp / "repo", [], now=lambda: float(next(clock)))
        return seen

    def test_index_reaches_mint_origin(self):
        self.assertEqual(self.indexes(True).get("cn"), products.cn_index([SLEEVE, OP09]))

    def test_control_no_index_file_no_cn_index(self):
        self.assertNotIn("cn", self.indexes(False))


if __name__ == "__main__":
    unittest.main()
