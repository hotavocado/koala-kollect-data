"""cn mapping on verbatim rows from the live list and detail endpoints (2026-10-08).

cn_variant_rows.json is 82 list rows: the 75 whose number carries an inline
token, plus the shapes the variant rule turns on (2181, 2763, 2764, 6987,
6748, 4141, 1572). Its base/number_token columns came from an independent
longest-prefix split, not from cnmodel, so the table below is a check and
not a restatement. cn_details.json is six detail calls, verbatim.
"""
import json
import shutil
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

import cnmodel
import run

HERE = Path(__file__).resolve().parent
ROWS = json.loads((HERE / "fixtures" / "cn_variant_rows.json").read_text("utf-8"))
DETAILS = {int(k): v for k, v in json.loads((HERE / "fixtures" / "cn_details.json").read_text("utf-8")).items()}
CARDS = HERE.parent / "data" / "cards.jsonl"
INLINE = [r for r in ROWS if r["number_token"] and not r["number_token"].startswith("_")]
T1, T2 = "2026-10-08T21:51:00Z", "2026-10-09T04:23:00Z"


class Numbers(unittest.TestCase):
    def test_split_matches_the_independent_split(self):
        for r in ROWS:
            with self.subTest(id=r["id"], number=r["cardNumber"]):
                self.assertEqual(cnmodel.split_number(r["cardNumber"]), (r["base"], r["number_token"]))

    def test_every_fixture_token_is_a_measured_shape(self):
        self.assertEqual([r["cardNumber"] for r in ROWS if not cnmodel.known_token(r["number_token"])], [])

    def test_inline_rows_mint_no_cn_only_card(self):
        # alyssa 87274: the number field is the base number. Read raw, the 75
        # inline rows name 75 numbers no card carries; split, they name none.
        numbers = {json.loads(line)["number"] for line in CARDS.read_text("utf-8").splitlines()}
        self.assertEqual(len(INLINE), 75)
        self.assertEqual(sum(r["cardNumber"] not in numbers for r in INLINE), 75)
        self.assertEqual([r["cardNumber"] for r in INLINE if cnmodel.split_number(r["cardNumber"])[0] not in numbers], [])

    def test_a_new_token_is_kept_verbatim_not_minted(self):
        self.assertEqual(cnmodel.split_number("OP01-001ZZ"), ("OP01-001", "ZZ"))
        self.assertFalse(cnmodel.known_token("ZZ"))

    def test_red_no_base_number_raises(self):
        for raw in ["", None, "OP01", "促销卡"]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                cnmodel.split_number(raw)


class Variant(unittest.TestCase):
    CASES = {
        # id: (image token, marker, variant)
        1572: ("", False, "base"),       # OP01-001, plain
        2763: ("", False, "base"),       # OP06-050, plain
        2764: ("P", False, "parallel"),  # OP06-050 again: only the image file says P
        2181: ("SP", True, "parallel"),  # OP01-001 with the marker and SP in the file
        6987: (None, False, "parallel"), # P-084_01, hashed file name: the image is unread
        6748: ("_02", False, "parallel"),
        4141: ("", False, "base"),
    }

    def signals(self, r):
        base, token = cnmodel.split_number(r["cardNumber"])
        return token, cnmodel.image_token(r["cardImg"], base), cnmodel.has_marker(r["cardName"])

    def test_measured_rows(self):
        by_id = {r["id"]: r for r in ROWS}
        for i, (img, marker, variant) in self.CASES.items():
            with self.subTest(id=i):
                token, got_img, got_marker = self.signals(by_id[i])
                self.assertEqual((got_img, got_marker), (img, marker))
                self.assertEqual(cnmodel.variant(token, got_img, got_marker), variant)

    def test_every_inline_row_is_a_parallel(self):
        self.assertEqual([r["cardNumber"] for r in INLINE if cnmodel.variant(*self.signals(r)) != "parallel"], [])

    def test_never_reprint(self):
        self.assertEqual({cnmodel.variant(*self.signals(r)) for r in ROWS}, {"base", "parallel"})

    def test_disagreement(self):
        self.assertTrue(cnmodel.disagree("", "P", False))      # 2764
        self.assertFalse(cnmodel.disagree("", "", False))      # 2763
        self.assertFalse(cnmodel.disagree("P", "P", True))     # all three say parallel
        # An unread image (hashed name, 6987) is left out, never read as "no".
        self.assertFalse(cnmodel.disagree("", None, False))
        self.assertTrue(cnmodel.disagree("_01", None, False))  # the number says parallel, the name does not


class CopyMarks(unittest.TestCase):
    """alyssa 87340: copy marks and a trailing _D are re-uploads, not art.

    18 rows on 2026-10-08 were the only cn id for their number, jp listing it
    base only, and every one wore one. The raw token stays on the printing.
    """
    def test_art_token(self):
        for raw, art in [("%281%29", ""), ("(1)", ""), ("%281%29%282%29%284%29", ""), ("_D", ""),
                         ("SP%281%29", "SP"), ("_01%281%29", "_01"), ("_win_D", "_win"),
                         ("_d%282%29", "_d"), ("_d", "_d"), ("P", "P"), ("", ""), (None, None)]:
            with self.subTest(raw=raw):
                self.assertEqual(cnmodel.art_token(raw), art)

    def test_a_copy_mark_alone_is_base(self):
        self.assertEqual(cnmodel.variant("", "%281%29", False), "base")   # 1938 OP02-077
        self.assertEqual(cnmodel.variant("", "_D", False), "base")        # 1999 P-026
        self.assertEqual(cnmodel.variant("", "_d", False), "parallel")    # lowercase stays an art mark
        self.assertFalse(cnmodel.disagree("", "%281%29", False))

    def test_a_dash_n_goes_only_with_its_mark(self):
        # alyssa 87354: "-1(1)" is one copy mark (5376 OP13-077, jp base only);
        # a bare -N is a real cn token and stays.
        self.assertEqual(cnmodel.art_token("-1%281%29"), "")
        self.assertEqual(cnmodel.variant("", "-1%281%29", False), "base")
        self.assertEqual(cnmodel.art_token("-03"), "-03")
        self.assertEqual(cnmodel.variant("", "-03", False), "parallel")

    def test_run_keeps_the_raw_token(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "repo").mkdir()
            cn = tmp / "cn"
            (cn / "detail").mkdir(parents=True)
            ids = [1938, 1999]
            rows = [{k: DETAILS[i][k] for k in ("id", "cardNumber", "cardImg", "cardOfferType")} for i in ids]
            prods = [{"id": n, "name": name} for n, name in enumerate(sorted({r["cardOfferType"] for r in rows}), 1)]
            (cn / "_list.json").write_text(json.dumps({"fetched_at": T1, "rows": rows}, ensure_ascii=False), "utf-8")
            (cn / "_products.json").write_text(json.dumps({"fetched_at": T1, "products": prods}, ensure_ascii=False), "utf-8")
            for i in ids:
                (cn / "detail" / f"{i}.json").write_text(json.dumps({"fetched_at": T1, "info": DETAILS[i]}, ensure_ascii=False), "utf-8")
            store = run.Store(tmp / "repo")
            counts, _, _ = run.run_cn(store, cn, {})
            got = {p["image_url"].rsplit("/", 1)[-1]: (p["variant"], p.get("image_token")) for p in store.recs["printing"].values()}
            self.assertEqual(sorted(got.values()), [("base", "%281%29"), ("base", "_D")])
            self.assertEqual(counts["variant_disagreements"], 0)
            run.validate(store)
        finally:
            shutil.rmtree(tmp)


class Facts(unittest.TestCase):
    def test_rarity_spellings(self):
        self.assertEqual({cnmodel.rarity(r) for r in ["推广卡（P）", "宣传（P）", "P"]}, {"P"})
        self.assertEqual(cnmodel.rarity("罕见（U）"), "UC")

    def test_red_unknown_rarity(self):
        with self.assertRaises(ValueError):
            cnmodel.rarity("神秘（X）")

    def test_red_unknown_category(self):
        with self.assertRaises(ValueError):
            cnmodel.observation_fields(dict(DETAILS[2763], cardType="卡套"))

    def test_category_slip_is_pinned_by_id(self):
        # 2932 typed its rarity into the category field; only that id is excused.
        self.assertEqual(cnmodel.observation_fields(DETAILS[2932])["category"], "character")
        with self.assertRaises(ValueError):
            cnmodel.observation_fields(dict(DETAILS[2932], id=99999))

    def test_attribute_on_an_event_is_ignored(self):
        # 1925 is an event whose attribute field repeats its colour, 蓝.
        f = cnmodel.observation_fields(DETAILS[1925])
        self.assertEqual((f["category"], f["attributes"]), ("event", []))

    def test_source_text(self):
        self.assertEqual(cnmodel.source_text(DETAILS[1925]), "补充包 顶尖决战【OPC-02】")  # type "2"
        self.assertEqual(cnmodel.source_text(DETAILS[2181]), "航海王卡牌对战 官方25周年纪念卡册")
        self.assertEqual(cnmodel.source_text(DETAILS[2763]), DETAILS[2763]["cardOfferType"])  # type null
        self.assertEqual(cnmodel.source_text(DETAILS[6987]), DETAILS[6987]["cardOfferType"])  # type ""

    def test_types_split_on_a_comma_too(self):
        self.assertEqual(cnmodel.observation_fields(DETAILS[6987])["types"], ["四皇", "十字公会"])

    def test_marker_is_not_in_the_name(self):
        self.assertEqual(cnmodel.observation_fields(DETAILS[2181])["name"], "罗罗诺亚·佐罗")


class Op06050Pair(unittest.TestCase):
    """upper 87297: two cn ids, one number, one base and one parallel, kept apart.

    The printing key is mint(prt, cn:{id}), so the ids never collapse. A key
    built from site plus NUMBER would make these one printing.
    """
    PRODUCT = {"id": 75, "name": "补充包 双璧的霸者【OPC-06】"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "repo").mkdir()
        self.cn = self.tmp / "cn"
        (self.cn / "detail").mkdir(parents=True)
        self.store = run.Store(self.tmp / "repo")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def pageset(self, ids, t):
        rows = [r for r in ROWS if r["id"] in ids]
        (self.cn / "_list.json").write_text(json.dumps({"fetched_at": t, "rows": rows}, ensure_ascii=False), "utf-8")
        (self.cn / "_products.json").write_text(
            json.dumps({"fetched_at": t, "products": [self.PRODUCT]}, ensure_ascii=False), "utf-8")
        for i in ids:
            (self.cn / "detail" / f"{i}.json").write_text(
                json.dumps({"fetched_at": t, "info": DETAILS[i]}, ensure_ascii=False), "utf-8")

    def cn_printings(self):
        return {loc["image_id"]: self.store.recs["printing"][loc["printing_key"]]
                for loc in self.store.recs["printing_locator"].values() if loc["site"] == "cn"}

    def test_pair_survives_a_run_and_a_rerun(self):
        self.pageset([2763, 2764], T1)
        state = {}
        counts, refusals, _ = run.run_cn(self.store, self.cn, state)
        p = self.cn_printings()
        self.assertEqual(sorted(p), ["2763", "2764"])
        self.assertNotEqual(p["2763"]["key"], p["2764"]["key"])
        self.assertEqual(p["2763"]["card_key"], p["2764"]["card_key"])
        self.assertEqual((p["2763"]["variant"], p["2764"]["variant"]), ("base", "parallel"))
        self.assertEqual((p["2763"].get("image_token"), p["2764"].get("image_token")), (None, "P"))
        self.assertEqual(counts["variant_disagreements"], 1)  # 2764: the number is bare, the file says P
        self.assertEqual(sum(refusals.values()), 0)
        run.validate(self.store)

        self.pageset([2763, 2764], T2)
        counts, _, _ = run.run_cn(self.store, self.cn, state)
        self.assertEqual((counts["added"], counts["changed"]), (0, 0))
        self.assertEqual(sorted(self.cn_printings()), ["2763", "2764"])

    def test_observation_comes_from_the_base(self):
        self.pageset([2763, 2764], T1)
        run.run_cn(self.store, self.cn, {})
        obs = [o for o in self.store.recs["card_observation"].values() if o["site"] == "cn"]
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["first_seen_at"], T1)

    def test_an_unknown_token_is_counted_not_refused(self):
        row = dict(next(r for r in ROWS if r["id"] == 2763), cardNumber="OP06-050ZZ")
        self.pageset([2763], T1)
        lst = json.loads((self.cn / "_list.json").read_text("utf-8"))
        lst["rows"] = [row]
        (self.cn / "_list.json").write_text(json.dumps(lst, ensure_ascii=False), "utf-8")
        counts, _, _ = run.run_cn(self.store, self.cn, {})
        (prt,) = self.cn_printings().values()
        self.assertEqual((counts["unknown_tokens"], prt["number_token"], prt["variant"]), (1, "ZZ", "parallel"))


if __name__ == "__main__":
    unittest.main()
