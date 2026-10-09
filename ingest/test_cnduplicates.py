"""cn double-saves: one printing listed under two or three ids (CONTRACT.md, cn).

cn_duplicate_rows.json is the five list rows, verbatim from the 2026-10-08
list, and cn_details.json carries their five details: OP09-043 is ids
4647/4648/4649 and P-108 is ids 5521/5522. Each group agrees in every field of
the list row and the detail apart from id, createTime and updateTime. The
boundary is asia-en P-029_r1: one image listed under two products. A cn id
names one product, so that shape on cn is two ids whose list rows differ in
cardOfferType, and those are two printings.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cn
import model
import run

HERE = Path(__file__).resolve().parent
ROWS = {r["id"]: r for r in json.loads((HERE / "fixtures" / "cn_duplicate_rows.json").read_text("utf-8"))}
DETAILS = {int(k): v for k, v in json.loads((HERE / "fixtures" / "cn_details.json").read_text("utf-8")).items()}
PRODUCTS = [{"id": 9, "name": "宣传卡"}, {"id": 48, "name": "限定商品收录卡牌"}]
T1, T2 = "2026-10-08T21:53:39Z", "2026-10-09T04:23:00Z"


class Duplicates(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "repo").mkdir()
        self.cn = self.tmp / "cn"
        self.store = run.Store(self.tmp / "repo")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def pageset(self, ids, t, rows=None, details=None):
        shutil.rmtree(self.cn, ignore_errors=True)
        (self.cn / "detail").mkdir(parents=True)
        rows = rows or {i: ROWS[i] for i in ids}
        details = details or {}
        (self.cn / "_list.json").write_text(
            json.dumps({"fetched_at": t, "rows": [rows[i] for i in ids]}, ensure_ascii=False), "utf-8")
        (self.cn / "_products.json").write_text(
            json.dumps({"fetched_at": t, "products": PRODUCTS}, ensure_ascii=False), "utf-8")
        for i in ids:
            (self.cn / "detail" / f"{i}.json").write_text(
                json.dumps({"fetched_at": t, "info": details.get(i, DETAILS[i])}, ensure_ascii=False), "utf-8")

    def locators(self):
        return {int(r["image_id"]): r["printing_key"] for r in self.store.recs["printing_locator"].values()}

    def test_the_op09_043_triple_is_one_printing(self):
        self.pageset([4647, 4648, 4649], T1)
        state = {}
        counts, refusals, _ = run.run_cn(self.store, self.cn, state)
        loc = self.locators()
        self.assertEqual(len(self.store.recs["printing"]), 1)
        self.assertEqual(loc[4648], loc[4647])
        self.assertEqual(loc[4649], loc[4647])
        self.assertEqual(loc[4647], model.mint("prt", "cn:4647"))
        self.assertEqual(counts["duplicate_ids"], [4648, 4649])
        self.assertEqual(len(self.store.recs["printing_product"]), 1)
        self.assertEqual(sum(refusals.values()), 0)
        run.validate(self.store)

        self.pageset([4647, 4648, 4649], T2)
        counts, _, _ = run.run_cn(self.store, self.cn, state)
        self.assertEqual((counts["added"], counts["changed"]), (0, 0))
        self.assertEqual(counts["duplicate_ids"], [4648, 4649])

    def test_the_p108_pair_is_one_printing(self):
        self.pageset([5521, 5522], T1)
        counts, _, _ = run.run_cn(self.store, self.cn, {})
        self.assertEqual(len(self.store.recs["printing"]), 1)
        self.assertEqual(counts["duplicate_ids"], [5522])

    def test_boundary_one_image_under_two_products_is_two_printings(self):
        # The P-029 shape (asia-en P-029_r1 listed under ST-16 and PRB-01): the
        # same detail, the same image, a different product. Not a duplicate.
        rows = {4647: ROWS[4647], 4648: dict(ROWS[4648], cardOfferType="限定商品收录卡牌")}
        self.pageset([4647, 4648], T1, rows=rows)
        counts, _, _ = run.run_cn(self.store, self.cn, {})
        loc = self.locators()
        self.assertNotEqual(loc[4647], loc[4648])
        self.assertEqual(len(self.store.recs["printing"]), 2)
        self.assertEqual(counts["duplicate_ids"], [])

    def test_one_field_apart_is_two_printings(self):
        self.pageset([4647, 4648], T1, details={4648: dict(DETAILS[4648], cardRarity="稀有（R）")})
        counts, _, _ = run.run_cn(self.store, self.cn, {})
        self.assertEqual(len(self.store.recs["printing"]), 2)
        self.assertEqual(counts["duplicate_ids"], [])

    def test_red_a_duplicate_with_its_own_printing_stops_the_run(self):
        # What main held before this change: cn:4648 minted its own printing.
        self.pageset([4648], T1)
        run.run_cn(self.store, self.cn, {})
        self.pageset([4647, 4648], T2)
        with self.assertRaisesRegex(run.RunError, "cn:4648 duplicates cn:4647 but has its own printing"):
            run.run_cn(self.store, self.cn, {})

    def test_retiring_moves_the_locator_to_the_survivor(self):
        # The migration this change ships: 4648's printing removed by the PR and
        # retired; its locator still names it until the run moves it.
        self.pageset([4648], T1)
        run.run_cn(self.store, self.cn, {})
        gone = self.locators()[4648]
        self.retire(gone)
        self.pageset([4647, 4648], T2)
        counts, _, _ = run.run_cn(self.store, self.cn, {})
        loc = self.locators()
        self.assertEqual(loc[4648], loc[4647])
        self.assertNotIn(gone, self.store.recs["printing"])
        self.assertNotIn(gone, {r["printing_key"] for r in self.store.recs["printing_product"].values()})
        self.assertEqual(counts["duplicate_ids"], [4648])

    def test_red_a_retired_printing_is_never_written_again(self):
        self.retire(model.mint("prt", "cn:4648"))
        self.pageset([4648], T1)
        with self.assertRaisesRegex(run.RunError, "is retired"):
            run.run_cn(self.store, self.cn, {})

    def test_the_card_list_claim_names_the_lowest_id(self):
        self.pageset([4647, 4648, 4649], T1)
        counts, _, _ = run.run_cn(self.store, self.cn, {})
        run.link_site(self.store, "cn", counts)
        (claim,) = self.store.recs["printing_distribution"].values()
        self.assertTrue(claim["source_url"].endswith("/4647"), claim["source_url"])

    def retire(self, key):
        # The PR's removal: the printing and its listing go, the retired row stays.
        self.store.recs["printing"].pop(key, None)
        for k in [k for k, r in self.store.recs["printing_product"].items() if r["printing_key"] == key]:
            del self.store.recs["printing_product"][k]
        self.store.recs["retired_printing"][key] = {
            "key": key, "printing_key": key, "reason": "duplicate_source_record",
            "retired_at": T1, "source_ids": ["cn:4648", "cn:4647"]}


class Manifest(unittest.TestCase):
    def test_generated_at_counts_retired_at(self):
        # A retirement is the newest change in the data when it lands, so the
        # manifest has to say so (codex, PR 16).
        repo = Path("/r")
        files = {repo / "data/printings/cn.jsonl": json.dumps({"key": "prt_000000000001", "first_seen_at": T1}) + "\n",
                 repo / "data/retired_printings.jsonl": json.dumps({"key": "prt_000000000009", "retired_at": T2}) + "\n"}
        self.assertEqual(json.loads(run.manifest(files, repo))["generated_at"], T2)


class TwinFetch(unittest.TestCase):
    """A new id whose list row equals a held id's: fetch the held id's detail too, so run_cn can compare."""

    def fetched(self, rows, known):
        tmp = Path(tempfile.mkdtemp())
        got = []
        try:
            with mock.patch.object(cn, "list_all", return_value=rows), \
                 mock.patch.object(cn, "products", return_value=PRODUCTS), \
                 mock.patch.object(cn, "detail", side_effect=lambda i: got.append(i) or DETAILS[i]), \
                 mock.patch.object(cn.time, "sleep"):
                self.assertEqual(cn.fetch_pageset(tmp, known), [])
        finally:
            shutil.rmtree(tmp)
        return sorted(got)

    def test_a_held_twin_is_fetched_again(self):
        self.assertEqual(self.fetched([ROWS[4647], ROWS[4648], ROWS[5521]], {4647, 5521}), [4647, 4648])

    def test_a_held_id_with_no_new_twin_is_not(self):
        self.assertEqual(self.fetched([ROWS[4647], ROWS[5521], ROWS[5522]], {4647, 5521}), [5521, 5522])


if __name__ == "__main__":
    unittest.main()
