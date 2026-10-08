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
