"""Step 3 run controls on a verbatim 12-block cut of en series 569117.

Each test builds a fetch.py-shaped page set, runs once to set the baseline, then
breaks the page the way a live fetch breaks and runs again. A refused page must
change nothing in data/, so those controls compare the whole data tree byte for
byte, not only the refusal counter.
"""
import hashlib
import itertools
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import model
import run

FIX = (Path(__file__).resolve().parent / "fixtures" / "en_569117_run.html").read_text("utf-8")
SID = "569117"
T1, T2 = "2026-10-08T00:00:00Z", "2026-10-09T00:00:00Z"
BLOCK = re.compile(r'<dl class="modalCol" id="([^"]+)">.*?</dl>', re.S)


def without(html, *image_ids):
    return BLOCK.sub(lambda m: "" if m.group(1) in image_ids else m.group(0), html)


class Harness:
    """A temp repo and a fetch.py-shaped page set for series 569117."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo, self.pages = self.tmp / "repo", self.tmp / "pages" / "en"
        self.pages.mkdir(parents=True)
        self.repo.mkdir()
        # One second per call, so two runs in one test never share a run_id.
        self.clock = itertools.count(1_791_331_200)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fetch(self, html, t, status=200):
        (self.pages / f"{SID}.html").write_text(html, "utf-8")
        entry = {"site": "en", "series_id": SID, "label": "BOOSTER PACK -THE WORLD’S STRONGEST WARRIORS- [OP-17]",
                 "status": status, "fetched_at": t}
        if status != 200:
            entry["error"] = "HTTPError: 503"
            (self.pages / f"{SID}.html").unlink()
        (self.pages / "_fetch_log.json").write_text(json.dumps([entry]))

    def go(self):
        return run.run(self.tmp / "pages", self.repo, ["en"], now=lambda: float(next(self.clock)))["sites"]["en"]

    def data(self):
        return {p.relative_to(self.repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((self.repo / "data").rglob("*.jsonl"))} | {
            "manifest.json": hashlib.sha256((self.repo / "manifest.json").read_bytes()).hexdigest()}

    def rows(self, rtype):
        return [json.loads(x) for x in (self.repo / "data" / run.PER_SITE.get(rtype, "") / "en.jsonl").read_text().splitlines()]

    def baseline(self):
        self.fetch(FIX, T1)
        r = self.go()
        self.assertEqual(r["counts"]["blocks_parsed"], 12)
        return r


class Run(Harness, unittest.TestCase):
    def test_baseline_writes_every_record_type(self):
        self.baseline()
        self.assertEqual(len(self.rows("printing")), 12)
        self.assertEqual(len(self.rows("printing_product")), 12)
        self.assertEqual(len(self.rows("product")), 1)
        manifest = json.loads((self.repo / "manifest.json").read_text())
        self.assertEqual(manifest["generated_at"], T1)
        self.assertEqual(manifest["files"]["data/printings/en.jsonl"]["rows"], 12)

    def test_rerun_on_the_same_pages_is_byte_identical(self):
        self.baseline()
        before = self.data()
        r = self.go()
        self.assertEqual((r["counts"]["added"], r["counts"]["changed"], r["counts"]["removed"]), (0, 0, 0))
        self.assertEqual(r["counts"]["pages_unchanged"], 1)
        self.assertEqual(self.data(), before)

    def test_same_second_rerun_is_refused(self):
        self.baseline()
        before = self.data()
        runs = sorted((self.repo / "runs").rglob("*.json"))
        first = runs[0].read_bytes()
        self.clock = itertools.count(1_791_331_200)  # replay the baseline's start second
        with self.assertRaisesRegex(run.RunError, "already exists"):
            self.go()
        self.assertEqual(sorted((self.repo / "runs").rglob("*.json")), runs)
        self.assertEqual(runs[0].read_bytes(), first)
        self.assertEqual(self.data(), before)

    def test_older_page_after_newer_data_is_refused(self):
        self.fetch(FIX, T2)
        self.go()
        before = self.data()
        self.fetch(without(FIX, "OP17-019"), T1)  # an older snapshot, and it differs
        with self.assertRaisesRegex(run.RunError, "older than the data"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_redeploy_stamp_changes_no_row(self):
        self.baseline()
        urls = sorted(p["image_url"] for p in self.rows("printing"))
        self.fetch(FIX.replace("?260929", "?261001"), T2)
        r = self.go()
        self.assertEqual((r["counts"]["added"], r["counts"]["changed"]), (0, 0))
        self.assertEqual(r["counts"]["pages_unchanged"], 1)
        self.assertEqual(sorted(p["image_url"] for p in self.rows("printing")), urls)
        self.assertTrue(all("?" not in u for u in urls))

    def test_control_keeping_the_stamp_would_change_every_printing(self):
        # Proves the test above can go red: with the query kept in image_url,
        # the same redeploy changes all 12 printings.
        keep = lambda site, page, src: model.urljoin(page, src)  # noqa: E731
        with mock.patch.object(model, "image_url", keep), \
                mock.patch.object(run, "validate", lambda store: None):
            self.baseline()
            self.fetch(FIX.replace("?260929", "?261001"), T2)
            self.assertEqual(self.go()["counts"]["changed"], 12)

    def place_name_en(self, name):
        # The hand edit: name_en set on the product row in the committed data.
        path = self.repo / "data" / "products" / "en.jsonl"
        (row,) = self.rows("product")
        path.write_text(run.line("product", dict(row, name_en=name)) + "\n", "utf-8")

    def test_hand_placed_name_en_survives_a_rewalk(self):
        self.baseline()
        self.place_name_en("EGGHEAD CRISIS")
        self.fetch(FIX.replace("?260929", "?261001"), T2)
        r = self.go()
        self.assertEqual(r["counts"]["changed"], 0)
        self.assertEqual(self.rows("product")[0]["name_en"], "EGGHEAD CRISIS")

    def test_control_without_the_carry_over_the_rewalk_drops_name_en(self):
        # Proves the test above can go red: the walk rebuilds the product row
        # from the page, so a field it does not carry is gone after one run.
        self.baseline()
        self.place_name_en("EGGHEAD CRISIS")
        self.fetch(FIX.replace("?260929", "?261001"), T2)
        with mock.patch.object(run, "PRODUCT_CARRIED", ("release_date", "release_date_source")):
            r = self.go()
        self.assertEqual(r["counts"]["changed"], 1)
        self.assertNotIn("name_en", self.rows("product")[0])

    def test_clean_removal_stamps_the_listing_not_the_printing(self):
        self.baseline()
        self.fetch(without(FIX, "OP17-007"), T2)  # 11 of 12: under the 10% floor
        r = self.go()
        self.assertEqual(r["counts"]["removed"], 1)
        self.assertEqual(sum(r["refusals"].values()), 0)
        gone = [x for x in self.rows("printing_product") if "removed_at" in x]
        self.assertEqual([x["removed_at"] for x in gone], [T2])
        self.assertTrue(all("removed_at" not in p for p in self.rows("printing")))

    def test_relisting_reopens_the_listing(self):
        self.baseline()
        self.fetch(without(FIX, "OP17-007"), T2)
        self.go()
        self.fetch(FIX, "2026-10-10T00:00:00Z")
        r = self.go()
        self.assertEqual(r["counts"]["changed"], 1)
        self.assertTrue(all("removed_at" not in x for x in self.rows("printing_product")))

    def test_erratum_supersedes_the_observation(self):
        self.baseline()
        self.fetch(FIX.replace("If you have 1 or less Life cards", "If you have 2 or less Life cards", 1), T2)
        r = self.go()
        self.assertGreaterEqual(r["counts"]["changed"], 1)
        obs = self.rows("card_observation")
        closed = [o for o in obs if "superseded_at" in o]
        self.assertEqual([o["superseded_at"] for o in closed], [T2])
        self.assertEqual(len({o["card_key"] for o in closed}), 1)

    # Each guard arm: the page is refused for its named reason, and data/ is
    # byte-identical to the baseline, so no removal and no upsert happened.
    GUARD = [
        ("http_error", lambda: (FIX, 503)),
        ("zero_parse", lambda: (without(FIX, *BLOCK.findall(FIX)), 200)),
        ("count_drop", lambda: (without(FIX, "OP17-006", "OP17-007"), 200)),  # 10 of 12
        ("count_mismatch", lambda: (FIX[: FIX.index("</dl>", FIX.index('id="OP17-019"'))], 200)),
    ]

    def test_each_guard_arm_refuses_and_changes_nothing(self):
        for expect, page in self.GUARD:
            with self.subTest(expect=expect):
                self.tearDown()
                self.setUp()
                self.baseline()
                before = self.data()
                html, status = page()
                self.fetch(html, T2, status)
                r = self.go()
                self.assertEqual(r["page_refusals"], {SID: expect})
                self.assertEqual(r["refusals"][expect], 1)
                self.assertEqual(sum(r["refusals"].values()), 1)
                self.assertEqual(r["counts"]["removed"], 0)
                self.assertEqual(self.data(), before)

    def test_blank_required_field_stops_the_run_and_writes_nothing(self):
        self.baseline()
        before = self.data()
        self.fetch(FIX.replace('class="cardName"', 'class="cardTitle"'), T2)
        with self.assertRaisesRegex(run.RunError, r"blank \['name'\]"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_unknown_colour_stops_the_run(self):
        self.fetch(re.sub(r'(<div class="color"><h3>[^<]*</h3>)Red', r"\1Crimson", FIX, count=1), T1)
        with self.assertRaisesRegex(run.RunError, "unknown colour 'Crimson'"):
            self.go()
        self.assertFalse((self.repo / "data").exists())


class RetiredSites(Harness, unittest.TestCase):
    """asia-en and tc are retired (CONTRACT.md, Retired sites): never walked, never written again."""

    def test_red_a_retired_site_stops_the_run_and_writes_nothing(self):
        self.baseline()
        before = self.data()
        for sites in (["asia-en"], ["tc"], ["en", "asia-en"]):
            with self.assertRaisesRegex(run.RunError, "is retired"):
                run.run(self.tmp / "pages", self.repo, sites, now=lambda: float(next(self.clock)))
        self.assertEqual(self.data(), before)

    def test_the_daily_site_list_has_no_retired_site(self):
        self.assertFalse(set(run.SITES) & run.RETIRED_SITES)

    def test_red_a_site_retired_printing_is_never_written_again(self):
        image_id = BLOCK.search(FIX).group(1)
        key = model.mint("prt", f"en:{image_id}")
        # The guard is keyed on the printing key alone, so a key minted by the
        # en walk stands in for any retired key coming back. The row itself
        # must still be a valid site_retired row, naming a retired site's locator.
        (self.repo / "data").mkdir()
        (self.repo / "data" / "retired_printings.jsonl").write_text(json.dumps(
            {"key": key, "printing_key": key, "reason": "site_retired", "retired_at": T1,
             "source_ids": [f"asia-en:{image_id}"]}) + "\n", "utf-8")
        self.fetch(FIX, T2)
        with self.assertRaisesRegex(run.RunError, rf"{key} is retired \(site_retired\)"):
            self.go()
        self.assertFalse((self.repo / "data" / "printings").exists())


class ReplayGuard(Harness, unittest.TestCase):
    """The stale-page guard reads fetched_at in state/pages, not a data row."""

    def state(self):
        return json.loads((self.repo / "state/pages/en.json").read_text())

    def test_clean_run_stamps_fetched_at(self):
        self.fetch(FIX, T2)
        self.go()
        self.assertEqual(self.state()[SID]["fetched_at"], T2)

    def test_control_without_fetched_at_the_older_page_gets_through(self):
        # Proves test_older_page_after_newer_data_is_refused reads state/pages:
        # take fetched_at out and the same replay is written.
        self.fetch(FIX, T2)
        self.go()
        st = self.state()
        del st[SID]["fetched_at"]
        (self.repo / "state/pages/en.json").write_text(json.dumps(st))
        self.fetch(without(FIX, "OP17-019"), T1)
        self.go()
        self.assertEqual(self.state()[SID]["fetched_at"], T1)

    def test_no_data_row_carries_last_seen_at(self):
        self.baseline()
        self.go()
        for f in (self.repo / "data").rglob("*.jsonl"):
            self.assertNotIn("last_seen_at", f.read_text(), f.name)



BI_EN = (Path(__file__).resolve().parent / "fixtures" / "block_icon_en.html").read_text("utf-8")
BI_JP = (Path(__file__).resolve().parent / "fixtures" / "block_icon_jp.html").read_text("utf-8")


class PrintingFacts(unittest.TestCase):
    """block_icon per printing per site and the ? attribute, on verbatim blocks (CONTRACT.md)."""

    PAGES = {"en": ("569117", BI_EN), "jp": ("550901", BI_JP)}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.clock = itertools.count(1_791_331_200)
        for site, (sid, html) in self.PAGES.items():
            d = self.tmp / "pages" / site
            d.mkdir(parents=True)
            (d / f"{sid}.html").write_text(html, "utf-8")
            (d / "_fetch_log.json").write_text(json.dumps([{"site": site, "series_id": sid, "label": "fixture",
                                                             "status": 200, "fetched_at": T1}]))
        run.run(self.tmp / "pages", self.repo, list(self.PAGES), now=lambda: float(next(self.clock)))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rows(self, d, site=None):
        f = self.repo / "data" / (f"{d}/{site}.jsonl" if site else f"{d}.jsonl")
        return [json.loads(x) for x in f.read_text().splitlines()]

    def icon(self, site, image_id):
        loc = {r["image_id"]: r["printing_key"] for r in self.rows("printing_locators", site)}
        return {r["key"]: r for r in self.rows("printings", site)}[loc[image_id]]["block_icon"]

    def test_sites_disagree_per_printing_and_both_survive(self):
        self.assertEqual((self.icon("en", "EB04-061_p2"), self.icon("jp", "EB04-061_p2")), ("X", 4))
        self.assertEqual((self.icon("en", "OP01-016_p8"), self.icon("jp", "OP01-016_p8")), (1, "X"))
        self.assertEqual((self.icon("en", "EB04-061"), self.icon("jp", "EB04-061")), (4, 4))

    def test_card_block_icon_is_the_facts_site_base_number(self):
        cards = {c["number"]: c for c in self.rows("cards")}
        self.assertEqual({n: (c["facts_site"], c.get("block_icon")) for n, c in cards.items()},
                         {"EB04-061": ("jp", 4), "OP01-016": ("jp", 1), "OP13-079": ("jp", 4)})

    def test_observations_carry_no_block_icon(self):
        for site in self.PAGES:
            self.assertTrue(all("block_icon" not in o for o in self.rows("card_observations", site)))

    def test_question_mark_is_stored_half_width_from_both_widths(self):
        self.assertIn("？", BI_JP)  # the jp page really prints the full-width form
        for site in self.PAGES:
            imu = [o for o in self.rows("card_observations", site) if o["name"] in ("Imu", "イム")]
            self.assertEqual([o["attributes"] for o in imu], [["?"]], site)

    def test_control_unmapped_full_width_question_mark_stops_the_run(self):
        table = {k: v for k, v in model.ATTRIBUTES.items() if k != "？"}
        with mock.patch.object(model, "ATTRIBUTES", table), self.assertRaisesRegex(ValueError, "unknown attribute"):
            model.observation_fields("jp", next(b for b in run.parse_page(BI_JP) if b["image_id"] == "OP13-079"))


class NoBasePrinting(PrintingFacts):
    """card.block_icon when the facts site lists no base printing (alyssa, general 86859/86869)."""

    PAGES = {"jp": ("550901", (Path(__file__).resolve().parent / "fixtures" / "no_base_jp.html").read_text("utf-8"))}

    def cards(self):
        return {c["number"]: c for c in self.rows("cards")}

    def test_every_printing_agrees_so_the_value_carries(self):
        # P-081: jp lists _p1, _p2 and _r1 and no base; all three print 3.
        self.assertEqual((self.cards()["P-081"]["facts_site"], self.cards()["P-081"].get("block_icon")), ("jp", 3))

    def test_printings_disagree_so_there_is_no_card_value(self):
        # OP01-055 with its base left out: _p1 and _r1 print 1, _p2 prints 4.
        # First-in-sibling-order would say 1, which is an arbitrary pick.
        self.assertEqual(sorted(self.icon("jp", i) for i in ("OP01-055_p1", "OP01-055_p2", "OP01-055_r1")), [1, 1, 4])
        self.assertNotIn("block_icon", self.cards()["OP01-055"])

    # Inherited tests are about the en/jp fixture; they do not apply to this page set.
    test_sites_disagree_per_printing_and_both_survive = None
    test_card_block_icon_is_the_facts_site_base_number = None
    test_question_mark_is_stored_half_width_from_both_widths = None
    test_control_unmapped_full_width_question_mark_stops_the_run = None


class ClosedShape(Harness, unittest.TestCase):
    """After the closing change a legacy-shaped observation stops the run; it is never reshaped quietly (roberto, general 86946)."""

    def legacy_row(self, path="data/card_observations/en.jsonl", **extra):
        p = self.repo / path
        rows = [json.loads(l) for l in p.read_text().splitlines()]
        rows[0].update(extra)
        p.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows))
        return rows[0]["key"]

    def test_observation_block_icon_is_refused_and_nothing_is_written(self):
        self.baseline()
        key = self.legacy_row(block_icon=1)
        before = self.data()
        self.fetch(FIX, T2)
        with self.assertRaisesRegex(run.RunError, rf"committed data: .*card_observation {re.escape(key)}: .*block_icon"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_observation_last_seen_at_is_refused_and_nothing_is_written(self):
        self.baseline()
        key = self.legacy_row(last_seen_at=T1)
        before = self.data()
        self.fetch(FIX, T2)
        with self.assertRaisesRegex(run.RunError, rf"committed data: .*card_observation {re.escape(key)}: .*last_seen_at"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_product_last_seen_at_is_refused_and_nothing_is_written(self):
        # It used to be dropped on read and seed the page's fetched_at; every
        # committed page carries fetched_at now, so a row with it is refused.
        self.baseline()
        key = self.legacy_row("data/products/en.jsonl", last_seen_at=T1)
        before = self.data()
        self.fetch(FIX, T2)
        with self.assertRaisesRegex(run.RunError, rf"committed data: .*product {re.escape(key)}: .*last_seen_at"):
            self.go()
        self.assertEqual(self.data(), before)

    def test_control_the_same_run_without_the_legacy_field_succeeds(self):
        self.baseline()
        self.fetch(FIX, T2)
        self.go()


class DataCheck(Harness, unittest.TestCase):
    """scripts/data_check.py on a real run's output, then broken one way at a time."""

    def check(self):
        from data_check import check_data
        return check_data(self.repo, run.SCHEMA)

    def test_clean_run_output_passes(self):
        self.baseline()
        self.assertEqual(self.check(), [])

    def test_controls_each_go_red_for_their_reason(self):
        def edit(rel, fn):
            p = self.repo / rel
            p.write_text(fn(p.read_text("utf-8")), "utf-8")

        def add_file(rel, rtype, row):
            # A listed, correctly hashed file, so only the row itself can go red.
            body = json.dumps(row, separators=(",", ":")) + "\n"
            (self.repo / rel).write_text(body, "utf-8")
            m = json.loads((self.repo / "manifest.json").read_text("utf-8"))
            m["files"][rel] = {"type": rtype, "rows": 1, "sha256": hashlib.sha256(body.encode()).hexdigest()}
            (self.repo / "manifest.json").write_text(json.dumps(m, indent=1) + "\n", "utf-8")

        def a_printing():
            return json.loads((self.repo / "data/printings/en.jsonl").read_text().splitlines()[0])["key"]

        cases = [
            ("sha256 mismatch", lambda: edit("data/products/en.jsonl", lambda t: t.replace("BOOSTER", "B00STER"))),
            ("row count mismatch", lambda: edit("manifest.json", lambda t: t.replace('"rows": 12', '"rows": 13', 1))),
            ("not sorted", lambda: edit("data/printings/en.jsonl",
                                        lambda t: "\n".join(reversed(t.splitlines())) + "\n")),
            ("has no card row", lambda: edit("data/printings/en.jsonl",
                                             lambda t: re.sub(r'"card_key":"card_[0-9a-z]{12}"',
                                                              '"card_key":"card_zzzzzzzzzzzz"', t, count=1))),
            ("on disk but not in manifest.json", lambda: (self.repo / "data/printing_links.jsonl").write_text("")),
            ("markup in name", lambda: edit("data/products/en.jsonl",
                                            lambda t: t.replace("BOOSTER ", "BOOSTER <br>", 1))),
            ("markup in name", lambda: edit("data/products/en.jsonl", lambda t: t.replace("BOOSTER ", "BOOSTER &lt;", 1))),
            ("README.md: not a data path the app sync accepts", lambda: edit(
                "manifest.json", lambda t: t.replace('"files": {', '"files": {"README.md": {"type": "card", "rows": 1, "sha256": "' + "0" * 64 + '"}, ', 1))),
            ("markup in types", lambda: edit("data/card_observations/en.jsonl",
                                             lambda t: t.replace('"types":["', '"types":["&lt;br&gt;', 1))),
            ("printing_b prt_zzzzzzzzzzzz has no printing row", lambda: add_file(
                "data/printing_links.jsonl", "printing_link",
                {"key": f"{a_printing()}=prt_zzzzzzzzzzzz", "printing_a": a_printing(), "printing_b": "prt_zzzzzzzzzzzz",
                 "method": "manual", "confidence": "inferred"})),
            ("distribution_key dst_zzzzzzzzzzzz has no distribution row", lambda: add_file(
                "data/printing_distributions.jsonl", "printing_distribution",
                {"key": "ev_0000000000000000", "printing_key": a_printing(), "distribution_key": "dst_zzzzzzzzzzzz",
                 "source": "official_cardlist", "source_url": "https://example.org/", "quote": "x",
                 "confidence": "inferred", "observed_at": "2026-10-08T00:00:00Z"})),
        ]
        for expect, breakit in cases:
            with self.subTest(expect=expect):
                self.tearDown()
                self.setUp()
                self.baseline()
                breakit()
                self.assertTrue(any(expect in e for e in self.check()), self.check()[:3])


class Mapping(unittest.TestCase):
    def test_jp_category_typo_maps_to_character(self):
        self.assertEqual(model.category("キャラクタークター"), "character")

    def test_control_unmapped_category_raises(self):
        with self.assertRaises(ValueError):
            model.category("キャラクター!")

    def test_mint_is_stable_and_well_formed(self):
        self.assertEqual(model.mint("card", "OP01-001"), model.mint("card", "OP01-001"))
        self.assertRegex(model.mint("prt", "en:OP01-001"), r"^prt_[0-9a-z]{12}$")
        self.assertNotEqual(model.mint("card", "OP01-001"), model.mint("card", "OP01-002"))

    def test_image_url_drops_the_deploy_stamp(self):
        self.assertEqual(model.image_url("en", "https://en.onepiece-cardgame.com/cardlist/?series=569117",
                                         "../images/cardlist/card/OP17-001.png?260929"),
                         "https://en.onepiece-cardgame.com/images/cardlist/card/OP17-001.png")


if __name__ == "__main__":
    unittest.main()
