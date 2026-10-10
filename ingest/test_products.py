"""release_date controls: the series-link join on verbatim Bandai pages, the
retail rule, the fetch policy, and the run's dating pass.

The join tests read verbatim excerpts of the en product index and product
pages (fixtures/products/, 2026-10-08).
The run tests build a fetch-shaped page set around the 12-block cut of en
series 569117 that test_run uses.
"""
import json
import unittest
from pathlib import Path

import products
import run
import model  # noqa: F401  (puts scripts/ on the path)
import tcgcsv_check
from test_run import FIX, SID, T1, T2, Harness as RunHarness

FX = Path(__file__).resolve().parent / "fixtures" / "products"
EN = "https://en.onepiece-cardgame.com/products/"
ST = EN + "decks/st01-04.php"
ST_PRE = EN + "decks/st01-04_pre.php"
OP14 = EN + "boosters/op14-eb04.php"


def read(name):
    return (FX / name).read_text("utf-8")


def verbatim_links():
    return {ST: products.series_links(read("product_en_st01-04.html")),
            ST_PRE: products.series_links(read("product_en_st01-04_pre.html")),
            OP14: products.series_links(read("product_en_op14-eb04.html"))}


class VerbatimPages(unittest.TestCase):
    def setUp(self):
        self.items = products.parse_index(read("products_index_en.html"), "en")

    def test_index_items_carry_url_title_and_date(self):
        by = {(i["href"], i["title"]): i["date"] for i in self.items}
        self.assertEqual(by[(ST, "STARTER DECK -Straw Hat Crew- [ST-01]")], "2022-12-02")
        self.assertEqual(by[(ST_PRE, "STARTER DECK -Straw Hat Crew- [ST-01 PRE]")], "2022-09-30")
        self.assertEqual(by[(OP14, "BOOSTER PACK -THE AZURE SEA’S SEVEN- [OP14-EB04]")], "2026-01-16")
        self.assertEqual(products.last_page(read("products_index_en.html")), 18)

    def test_the_bundle_page_dates_all_four_starters_though_its_title_names_one_each(self):
        links = verbatim_links()
        self.assertEqual(links[ST], ["569001", "569002", "569003", "569004"])
        dates = products.dates_by_series(self.items, links)
        for sid in ("569001", "569002", "569003", "569004"):
            self.assertEqual(products.retail_date(dates[sid]), ("2022-12-02", ST), sid)

    def test_st01_retail_date_wins_with_the_pre_release_page_present(self):
        dates = products.dates_by_series(self.items, verbatim_links())
        self.assertIn(("2022-09-30", ST_PRE, True), dates["569001"])
        self.assertEqual(products.retail_date(dates["569001"]), ("2022-12-02", ST))

    def test_control_without_the_pre_rule_st01_is_a_conflict(self):
        dates = products.dates_by_series(self.items, verbatim_links())
        as_retail = [(d, h, False) for d, h, _ in dates["569001"]]
        with self.assertRaisesRegex(ValueError, "retail pages disagree"):
            products.retail_date(as_retail)

    def test_the_compound_code_page_dates_569114(self):
        self.assertEqual(verbatim_links()[OP14], ["569114"])
        dates = products.dates_by_series(self.items, verbatim_links())
        self.assertEqual(products.retail_date(dates["569114"]), ("2026-01-16", OP14))

    def test_no_title_join_an_unread_page_dates_nothing(self):
        # The index item names [OP14-EB04] and [ST-01] in its title; without the
        # product page's series link, no series is dated. There is no fallback.
        dates = products.dates_by_series(self.items, {})
        self.assertEqual(dates, {})
        dates = products.dates_by_series(self.items, {ST: []})
        self.assertEqual(dates, {})


class Shape(unittest.TestCase):
    def li(self, href, date='datetime="2024-01-01"', title="X"):
        return (f'<li class="linkListColBox"><a href="{href}"><h4 class="linkListColTitle">{title}</h4>'
                f'<time class="newsDate" {date}>shown</time></a></li>')

    def test_an_empty_datetime_is_no_date(self):
        self.assertIsNone(products.parse_index(self.li(EN + "a.php", 'datetime=""'), "en")[0]["date"])

    def test_control_a_month_only_date_stops(self):
        with self.assertRaisesRegex(ValueError, "not YYYY-MM-DD"):
            products.parse_index(self.li(EN + "a.php", 'datetime="2024-01"'), "en")

    def test_control_a_link_off_the_products_path_stops(self):
        with self.assertRaisesRegex(ValueError, "off the products path"):
            products.parse_index(self.li("https://tcgcsv.com/x"), "en")
        with self.assertRaisesRegex(ValueError, "off the products path"):
            products.parse_index(self.li("https://www.onepiece-cardgame.com/products/a.php"), "en")

    def test_pre_release_is_the_pre_page_only(self):
        self.assertTrue(products.is_pre_release(ST_PRE, "x"))
        self.assertTrue(products.is_pre_release(EN + "a.php", "STARTER DECK [ST-02 PRE]"))
        self.assertFalse(products.is_pre_release(ST, "STARTER DECK -Straw Hat Crew- [ST-01]"))
        self.assertFalse(products.is_pre_release(EN + "boosters/prb01.php", "PREMIUM BOOSTER [PRB-01]"))

    def test_agreeing_retail_pages_name_the_first_url(self):
        self.assertEqual(products.retail_date([("2024-01-01", EN + "b.php", False), ("2024-01-01", EN + "a.php", False)]),
                         ("2024-01-01", EN + "a.php"))

    def test_only_a_pre_page_dates_nothing(self):
        self.assertIsNone(products.retail_date([("2022-09-30", ST_PRE, True)]))


class FetchPolicy(unittest.TestCase):
    ITEMS = [{"href": EN + "a.php"}, {"href": EN + "b.php"}, {"href": EN + "c.php"}, {"href": EN + "a.php"}]
    CACHE = {EN + "a.php": {"series": ["569001"]}, EN + "b.php": {"series": []}}

    def test_a_linked_page_is_never_read_again_and_a_new_page_always_is(self):
        self.assertEqual(products.needs_fetch(self.CACHE, self.ITEMS, undated=False), [EN + "c.php"])

    def test_an_unlinked_page_is_read_again_only_while_a_product_is_undated(self):
        self.assertEqual(products.needs_fetch(self.CACHE, self.ITEMS, undated=True), [EN + "b.php", EN + "c.php"])

    def test_fetch_site_reads_every_index_page_then_only_the_pages_it_needs(self):
        import tempfile
        pages = {products.index_url("en", 1): self.index([EN + "a.php"], 2),
                 products.index_url("en", 2): self.index([EN + "b.php"], 2),
                 EN + "b.php": '<a href="../cardlist/?series=569002">'}
        got = []

        def get(url):
            got.append(url)
            return 200, pages[url].encode()
        with tempfile.TemporaryDirectory() as d:
            log = products.fetch_site("en", d, {EN + "a.php": {"series": ["569001"]}}, False, pause=0, get=get)
            items, fetched, _ = products.read(Path(d) / "en", "en")
        i1, i2 = products.index_url("en", 1), products.index_url("en", 2)
        self.assertEqual(got, [i1, i2, i1, i2, EN + "b.php"])
        self.assertEqual(log["walks"], [{"walk": 1, "items": 2, "new": 2}, {"walk": 2, "items": 2, "new": 0}])
        self.assertEqual(products.failures(log), [])
        self.assertEqual([i["href"] for i in items], [EN + "a.php", EN + "b.php"])
        self.assertEqual(fetched[EN + "b.php"][0], ["569002"])

    def test_a_tie_one_walk_drops_is_recovered_by_the_next(self):
        """The 2026-10-08 shape: dp03 and OP-06 share a date across pages 13 and 14,
        and one walk listed dp03 on both pages and OP-06 on neither."""
        import tempfile
        i1, i2 = products.index_url("en", 1), products.index_url("en", 2)
        good = {i1: self.index([EN + "a.php", EN + "dp03.php"], 2), i2: self.index([EN + "op06.php", EN + "z.php"], 2)}
        shuffled = self.index([EN + "dp03.php", EN + "z.php"], 2)
        calls = []

        def get(url):
            calls.append(url)
            if url == i2 and calls.count(i2) == 1:
                return 200, shuffled.encode()
            return 200, good.get(url, "").encode()
        with tempfile.TemporaryDirectory() as d:
            log = products.fetch_site("en", d, {}, False, pause=0, get=get)
            items, _, _ = products.read(Path(d) / "en", "en")
        self.assertIn(EN + "op06.php", [i["href"] for i in items])
        self.assertEqual([w["new"] for w in log["walks"]], [3, 1, 0])
        self.assertEqual(len([i for i in items if i["href"] == EN + "dp03.php"]), 1)

    def test_the_walks_stop_at_the_cap(self):
        import tempfile
        n = iter(range(100))

        def get(url):
            if url.startswith(products.index_url("en", 1)[:-1]):
                return 200, self.index([EN + f"new{next(n)}.php"], 1).encode()
            return 200, b""
        with tempfile.TemporaryDirectory() as d:
            log = products.fetch_site("en", d, {}, False, pause=0, get=get)
        self.assertEqual(len(log["walks"]), products.MAX_WALKS)
        self.assertEqual([w["new"] for w in log["walks"]], [1] * products.MAX_WALKS)

    def test_a_page_listed_with_two_dates_stops_the_read(self):
        import tempfile
        i1 = products.index_url("en", 1)
        other = self.index([EN + "a.php"], 1).replace("2024-01-01", "2024-02-02")
        calls = []

        def get(url):
            calls.append(url)
            return 200, (other if url == i1 and calls.count(i1) == 2 else self.index([EN + "a.php"], 1)).encode()
        with tempfile.TemporaryDirectory() as d:
            products.fetch_site("en", d, {EN + "a.php": {"series": ["569001"]}}, False, pause=0, get=get)
            with self.assertRaisesRegex(ValueError, "two dates 2024-01-01 and 2024-02-02"):
                products.read(Path(d) / "en", "en")

    def test_a_failed_product_page_fails_the_read(self):
        import tempfile

        def get(url):
            if url == EN + "a.php":
                raise OSError("boom")
            return 200, self.index([EN + "a.php"], 1).encode()
        with tempfile.TemporaryDirectory() as d:
            log = products.fetch_site("en", d, {}, False, pause=0, attempts=1, get=get)
            self.assertEqual(products.failures(log), [f"en: product page {EN}a.php: OSError: boom"])
            with self.assertRaisesRegex(ValueError, "boom"):
                products.read(Path(d) / "en", "en")

    @staticmethod
    def index(hrefs, last):
        lis = "".join(f'<li class="linkListColBox"><a href="{h}"><h4 class="linkListColTitle">T</h4>'
                      f'<time datetime="2024-01-01">x</time></a></li>' for h in hrefs)
        return lis + "".join(f'<a href="?page={n}">{n}</a>' for n in range(1, last + 1))


OP17 = EN + "boosters/op17.php"
OP17_PRE = EN + "boosters/op17_pre.php"


class Dating(RunHarness, unittest.TestCase):
    """The run's dating pass over a page set carrying a product index."""

    def index(self, items, t):
        """Write {site}/products as products.fetch_site would. items: [(href, title, date, series or None)]."""
        out = self.pages / "products"
        (out / "pages").mkdir(parents=True, exist_ok=True)
        lis = "".join(f'<li class="linkListColBox"><a href="{h}"><h4 class="linkListColTitle">{title}</h4>'
                      f'<time class="newsDate" datetime="{d or ""}">x</time></a></li>' for h, title, d, _ in items)
        (out / "index-1.html").write_text(lis + '<a href="?page=1">1</a>', "utf-8")
        log = {"site": "en", "index": [{"page": 1, "status": 200, "fetched_at": t}], "pages": []}
        for h, _, _, series in items:
            if series is not None:
                links = "".join(f'<a href="../../cardlist/?series={s}">' for s in series)
                (out / "pages" / products.page_file(h)).write_text(links, "utf-8")
                log["pages"].append({"href": h, "status": 200, "fetched_at": t})
        (out / "_log.json").write_text(json.dumps(log), "utf-8")

    def product(self):
        return next(r for r in self.rows("product") if r["series_id"] == SID)

    def go_dates(self):
        r = self.go()
        return r, r["dates"]

    def test_the_index_dates_the_product_and_names_its_page(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T1)
        r, d = self.go_dates()
        self.assertEqual((self.product()["release_date"], self.product()["release_date_source"]), ("2026-08-22", OP17))
        self.assertEqual((d["dated"], d["set"], d["undated"]), (1, 1, []))
        # The product was added this run, so dating it is not a second change.
        self.assertEqual(r["counts"]["changed"], 0)

    def test_rerun_on_the_same_pages_is_byte_identical(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T1)
        self.go()
        before = self.data() | {"state": (self.repo / "state/product_pages/en.json").read_bytes()}
        r, d = self.go_dates()
        self.assertEqual(self.data() | {"state": (self.repo / "state/product_pages/en.json").read_bytes()}, before)
        self.assertEqual((r["counts"]["added"], r["counts"]["changed"], d["set"], d["moved"]), (0, 0, 0, 0))

    def test_retail_wins_over_pre_release_at_run_level(self):
        self.fetch(FIX, T1)
        self.index([(OP17_PRE, "BOOSTER PACK [OP-17 PRE]", "2026-08-01", [SID]),
                    (OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T1)
        self.go()
        self.assertEqual(self.product()["release_date"], "2026-08-22")

    def test_a_product_dropped_from_the_index_keeps_its_date(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T1)
        self.go()
        before = self.data()
        self.fetch(FIX, T2)
        self.index([(EN + "other.php", "SLEEVES", "2026-09-01", [])], T2)
        r, d = self.go_dates()
        self.assertEqual(self.product()["release_date"], "2026-08-22")
        self.assertEqual((d["dated"], d["kept"], r["counts"]["changed"]), (1, 1, 0))
        self.assertEqual(self.data(), before)

    def test_a_moved_date_is_a_change(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T1)
        self.go()
        self.fetch(FIX, T2)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-29", [SID])], T2)
        r, d = self.go_dates()
        self.assertEqual(self.product()["release_date"], "2026-08-29")
        self.assertEqual((d["moved"], r["counts"]["changed"]), (1, 1))

    def test_two_retail_dates_stop_the_run_and_write_nothing(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T1)
        self.go()
        before = self.data()
        self.fetch(FIX, T2)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID]),
                    (EN + "boosters/op17-reprint.php", "BOOSTER PACK [OP-17]", "2026-12-01", [SID])], T2)
        with self.assertRaisesRegex(run.RunError, "retail pages disagree"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_an_older_index_after_newer_data_is_refused(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T2)
        self.go()
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-01", [SID])], T1)
        with self.assertRaisesRegex(run.RunError, "older than the data"):
            self.go()

    def test_a_cached_page_dates_a_product_without_being_read_again(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T1)
        self.go()
        self.fetch(FIX, T2)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-29", None)], T2)  # page not read this run
        r, d = self.go_dates()
        self.assertEqual((self.product()["release_date"], d["pages_read"]), ("2026-08-29", 0))

    def test_a_bucket_is_never_dated(self):
        page = FIX
        self.fetch(page, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T1)
        self.go()
        # Make the committed product a promo bucket, then date its series again.
        path = self.repo / "data/products/en.jsonl"
        rec = json.loads(path.read_text())
        for k in ("release_date", "release_date_source", "code"):
            rec.pop(k, None)
        rec["kind"] = "promo_bucket"
        store = run.Store(self.repo)
        store.recs["product"] = {rec["key"]: rec}
        counts = {"new_products": [], "changed": 0}
        d = run.date_site(store, "en", self.pages, {}, counts)
        self.assertNotIn("release_date", store.recs["product"][rec["key"]])
        self.assertEqual((d["dated"], d["undated"]), (0, []))

    def test_without_a_product_index_the_run_reads_no_dates(self):
        self.fetch(FIX, T1)
        r = self.go()
        self.assertIsNone(r["dates"])
        self.assertNotIn("release_date", self.product())


DP02 = EN + "other/dp02.php"
DP12 = EN + "dp12.html"


class DoublePacks(RunHarness, unittest.TestCase):
    """Double Pack Sets: minted from the en index item, since no page links a series for them."""

    index, go_dates = Dating.index, Dating.go_dates

    def pack(self, code):
        return next((r for r in self.rows("product") if r["key"] == f"en:{code}"), None)

    def test_the_twelve_measured_titles_parse(self):
        # The en index titles and hrefs as listed 2026-10-10 (two href shapes).
        items = [{"href": EN + (f"other/dp{n:02d}.php" if n <= 10 else f"dp{n:02d}.html"),
                  "title": f"Double Pack Set Vol.{n} [DP-{n:02d}]", "date": "2024-01-01"} for n in range(1, 13)]
        items.append({"href": OP17, "title": "BOOSTER PACK [OP-17]", "date": "2026-08-22"})
        dated, undated = products.double_packs(items)
        self.assertEqual((sorted(dated), undated), ([f"DP-{n:02d}" for n in range(1, 13)], []))

    def test_a_pack_is_minted_dated_from_its_own_index_item(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID]),
                    (DP02, "Double Pack Set Vol.2 [DP-02]", "2023-12-08", [])], T1)
        r, d = self.go_dates()
        p = self.pack("DP-02")
        self.assertEqual({k: v for k, v in p.items() if k != "first_seen_at"},
                         {"key": "en:DP-02", "site": "en", "series_id": "DP-02", "code": "DP-02",
                          "name": "Double Pack Set Vol.2 [DP-02]", "kind": "other", "release_date": "2023-12-08",
                          "release_date_source": DP02, "product_url": DP02})
        self.assertEqual(p["first_seen_at"], T1)
        self.assertIn("en:DP-02", r["counts"]["new_products"])
        self.assertEqual((d["double_packs"], d["dated"], d["set"], d["undated"]), (1, 2, 2, []))
        self.assertIn("double packs 1", run.date_line(d))

    def test_rerun_on_the_same_pages_is_byte_identical(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID]),
                    (DP02, "Double Pack Set Vol.2 [DP-02]", "2023-12-08", [])], T1)
        self.go()
        before = self.data()
        r, d = self.go_dates()
        self.assertEqual(self.data(), before)
        self.assertEqual((r["counts"]["added"], r["counts"]["changed"], d["set"], d["moved"]), (0, 0, 0, 0))

    def test_an_undated_pack_is_not_written_and_is_named(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID]),
                    (DP12, "Double Pack Set Vol.12 [DP-12]", None, [])], T1)
        r, d = self.go_dates()
        self.assertIsNone(self.pack("DP-12"))
        self.assertEqual((d["double_packs"], d["double_packs_undated"], d["undated"]), (0, ["DP-12"], []))
        self.assertIn("double packs with no date on the index [DP-12]", run.date_line(d))

    def test_packs_leave_the_site_with_no_undated_product(self):
        import daily
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID]),
                    (DP02, "Double Pack Set Vol.2 [DP-02]", "2023-12-08", [])], T1)
        self.go()
        self.assertIsNotNone(self.pack("DP-02"))
        self.assertFalse(daily.undated(self.repo, "en", ""))
        # Control: the same check is True once a pack row has no date.
        path = self.repo / "data/products/en.jsonl"
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        for rec in rows:
            if rec["key"] == "en:DP-02":
                rec.pop("release_date"), rec.pop("release_date_source")
        path.write_text("".join(json.dumps(x) + "\n" for x in rows))
        self.assertTrue(daily.undated(self.repo, "en", ""))

    def test_a_moved_date_updates_the_pack(self):
        self.fetch(FIX, T1)
        self.index([(DP02, "Double Pack Set Vol.2 [DP-02]", "2023-12-08", [])], T1)
        self.go()
        self.fetch(FIX, T2)
        self.index([(DP02, "Double Pack Set Vol.2 [DP-02]", "2023-12-15", [])], T2)
        r, d = self.go_dates()
        self.assertEqual((self.pack("DP-02")["release_date"], self.pack("DP-02")["first_seen_at"]), ("2023-12-15", T1))
        self.assertEqual((d["moved"], r["counts"]["changed"]), (1, 1))

    def test_a_pack_dropped_from_the_index_keeps_its_row_and_date(self):
        self.fetch(FIX, T1)
        self.index([(DP02, "Double Pack Set Vol.2 [DP-02]", "2023-12-08", [])], T1)
        self.go()
        before = self.data()
        self.fetch(FIX, T2)
        self.index([(EN + "other.php", "SLEEVES", "2026-09-01", [])], T2)
        r, d = self.go_dates()
        self.assertEqual(self.pack("DP-02")["release_date"], "2023-12-08")
        self.assertEqual((d["double_packs"], d["kept"], r["counts"]["changed"]), (0, 1, 0))
        self.assertEqual(self.data(), before)

    def test_one_pack_under_two_dates_stops_the_run_and_writes_nothing(self):
        self.fetch(FIX, T1)
        self.index([(OP17, "BOOSTER PACK [OP-17]", "2026-08-22", [SID])], T1)
        self.go()
        before = self.data()
        self.fetch(FIX, T2)
        self.index([(DP02, "Double Pack Set Vol.2 [DP-02]", "2023-12-08", []),
                    (EN + "dp02.html", "Double Pack Set Vol.2 [DP-02]", "2023-12-15", [])], T2)
        with self.assertRaisesRegex(run.RunError, "DP-02 listed twice"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_one_pack_under_two_pages_stops_even_when_one_is_undated(self):
        # codex r1, PR 21: the undated listing must not skip the two-pages check.
        for order in ((None, "2023-12-08"), ("2023-12-08", None)):
            items = [{"href": DP02, "title": "Double Pack Set Vol.2 [DP-02]", "date": order[0]},
                     {"href": EN + "dp02.html", "title": "Double Pack Set Vol.2 [DP-02]", "date": order[1]}]
            with self.assertRaisesRegex(ValueError, "DP-02 listed twice"):
                products.double_packs(items)

    def test_control_a_title_without_the_bracketed_code_mints_nothing(self):
        dated, undated = products.double_packs([{"href": DP02, "title": "Double Pack Set Vol.2", "date": "2023-12-08"},
                                                {"href": DP02, "title": "DP-02 sleeves [DP-02-S]", "date": "2023-12-08"}])
        self.assertEqual((dated, undated), ({}, []))


class TcgcsvCrossCheck(unittest.TestCase):
    GROUPS = [{"abbreviation": "ST-01", "publishedOn": "2022-12-02T00:00:00"},
              {"abbreviation": "ST-01 PRE", "publishedOn": "2022-09-30T00:00:00"},
              {"abbreviation": "OP01", "publishedOn": "2022-12-02T00:00:00"},
              {"abbreviation": "OP14-EB04", "publishedOn": "2026-01-16T00:00:00"}]

    def rows(self, **dates):
        return [{"key": f"{site}:{code}", "site": site, "code": code, "release_date": d}
                for (site, code), d in dates.items()]

    def test_en_agrees_and_the_pre_group_is_skipped(self):
        rows = [{"key": "en:569001", "site": "en", "code": "ST-01", "release_date": "2022-12-02"},
                {"key": "en:569101", "site": "en", "code": "OP-01", "release_date": "2022-12-02"},
                {"key": "en:569114", "site": "en", "code": "OP14-EB04", "release_date": "2026-01-16"}]
        self.assertEqual(tcgcsv_check.compare(rows, self.GROUPS), (3, []))

    def test_a_disagreement_is_a_warning_line(self):
        rows = [{"key": "en:569101", "site": "en", "code": "OP-01", "release_date": "2022-11-01"}]
        self.assertEqual(tcgcsv_check.compare(rows, self.GROUPS),
                         (1, ["en:569101 OP-01: Bandai en 2022-11-01, tcgcsv 2022-12-02"]))

    def test_other_sites_are_never_compared(self):
        rows = [{"key": "jp:550101", "site": "jp", "code": "OP-01", "release_date": "2022-07-22"}]
        self.assertEqual(tcgcsv_check.compare(rows, self.GROUPS), (0, []))

    def test_an_unreachable_tcgcsv_is_a_warning_not_an_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "data/products").mkdir(parents=True)
            (Path(d) / "data/products/en.jsonl").write_text("{}\n")

            def boom():
                raise OSError("offline")
            self.assertEqual(tcgcsv_check.check(d, boom), ["warn tcgcsv en cross-check skipped: OSError: offline"])


if __name__ == "__main__":
    unittest.main()
