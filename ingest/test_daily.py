"""Step 4 daily-run controls on the verbatim 12-block cut of en series 569117.

The fetcher is faked (no network); run.py is the real one. Each control names
the decision it pins: commit only on a real change, fail and commit nothing on
a fetch failure or a refusal, and report a new dropdown option as a notice.
"""
import contextlib
import io
import itertools
import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cn
import daily
import fetch
import products
import run
import tcgcsv
from test_run import FIX, SID, T1, T2, without

LABEL = "BOOSTER PACK -THE WORLD’S STRONGEST WARRIORS- [OP-17]"
NEW_SID, NEW_LABEL = "569118", "BOOSTER PACK -NEW SET- [OP-18]"


def index(*options):
    opts = "".join(f'<option value="{sid}">{label}</option>' for sid, label in options)
    return f'<select name="series"><option value="">ALL</option>{opts}</select>'


CN_PRODUCT = {"id": 68, "name": "补充包 冒险的黎明【OPC-01】"}


def cn_pageset(d, rows, t):
    """cn.fetch_pageset-shaped output: the list, one product, and a detail for every row."""
    (d / "detail").mkdir(parents=True, exist_ok=True)
    full = [{"id": i, "cardImg": f"https://source.windoent.com/OnePiecePc/Picture/1{num}.png",
             "cardOfferType": CN_PRODUCT["name"], "cardNumber": num} for i, num in rows]
    (d / "_list.json").write_text(json.dumps({"fetched_at": t, "rows": full}, ensure_ascii=False), "utf-8")
    (d / "_products.json").write_text(json.dumps({"fetched_at": t, "products": [CN_PRODUCT]}, ensure_ascii=False), "utf-8")
    for i, num in rows:
        info = {"id": i, "cardNumber": num, "cardName": "罗罗诺亚·佐罗", "cardType": "领袖", "cardRarity": "领袖（L）",
                "cardOfferType": CN_PRODUCT["name"], "cardLife": "5", "cardAttribute": ["斩"], "cardColor": "红",
                "cardPower": "5000", "cardAttack": "-", "cardFeatures": "超新星/草帽一伙", "cardTextDesc": "-",
                "cardTrigger": None, "subscript": 1}
        (d / "detail" / f"{i}.json").write_text(json.dumps({"fetched_at": t, "info": info}, ensure_ascii=False), "utf-8")


class Harness:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo, self.pages, self.out = self.tmp / "repo", self.tmp / "pages", self.tmp / "out"
        self.repo.mkdir()
        self.clock = itertools.count(1_791_331_200)
        self.page, self.t, self.status = FIX, T1, 200
        self.sid, self.options = SID, [(SID, LABEL)]
        self.cn_rows, self.cn_t, self.fetch_failures = None, None, []
        self.with_products = True

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fetcher(self, pageset, sites, repo=None):
        """fetch.py- and products.py-shaped output for en only, from the harness state."""
        d = Path(pageset) / "en"
        d.mkdir(parents=True, exist_ok=True)
        (d / "_index.html").write_text(index(*self.options), "utf-8")
        entry = {"site": "en", "series_id": self.sid, "label": LABEL, "status": self.status, "fetched_at": self.t}
        if self.status == 200:
            (d / f"{self.sid}.html").write_text(self.page, "utf-8")
        else:
            entry["error"] = "HTTPError: 503"
            (d / f"{self.sid}.html").unlink(missing_ok=True)
        (d / "_fetch_log.json").write_text(json.dumps([entry]))
        if self.with_products:
            href = "https://en.onepiece-cardgame.com/products/boosters/op17.php"
            out = d / "products"
            (out / "pages").mkdir(parents=True, exist_ok=True)
            (out / "index-1.html").write_text(
                f'<li class="linkListColBox"><a href="{href}"><h4 class="linkListColTitle">{LABEL}</h4>'
                f'<time class="newsDate" datetime="2026-08-22">x</time></a></li><a href="?page=1">1</a>', "utf-8")
            (out / "pages" / products.page_file(href)).write_text(f'<a href="../../cardlist/?series={self.sid}">', "utf-8")
            (out / "_log.json").write_text(json.dumps({"site": "en", "index": [{"page": 1, "status": 200, "fetched_at": self.t}],
                                                      "pages": [{"href": href, "status": 200, "fetched_at": self.t}]}))
        if self.cn_rows is not None:
            cn_pageset(Path(pageset) / "cn", self.cn_rows, self.cn_t or self.t)
        return list(self.fetch_failures) + ([f"en:{self.sid} fetch failed: HTTPError: 503"] if self.status != 200 else [])

    def runner(self, pageset, repo, sites):
        return run.run(pageset, repo, sites, now=lambda: float(next(self.clock)))

    def go(self, **kw):
        kw.setdefault("runner", self.runner)
        return daily.daily(self.pages, self.repo, self.out, ["en"], fetcher=self.fetcher, **kw)

    def tree(self):
        return {p.relative_to(self.repo).as_posix(): p.read_bytes() for p in sorted(self.repo.rglob("*")) if p.is_file()}


class CommitPredicate(Harness, unittest.TestCase):
    def test_first_run_is_a_change(self):
        code, s = self.go()
        self.assertEqual((code, s["ok"], s["changed"]), (0, True, True))

    def test_next_day_with_no_change_does_not_commit(self):
        self.go()
        before = self.tree()
        self.t = T2
        code, s = self.go()
        self.assertEqual((code, s["ok"], s["changed"]), (0, True, False))
        # Only runs/ and state/pages (fetched_at) moved; data/ and the manifest
        # are byte-identical now that rows carry no last_seen_at.
        after = self.tree()
        self.assertNotEqual(before, after)
        keep = lambda t: {k: v for k, v in t.items() if k.startswith("data/") or k == "manifest.json"}  # noqa: E731
        self.assertEqual(keep(before), keep(after))
        self.assertEqual(s["sites"]["en"]["pages_unchanged"], 1)

    def test_control_a_clean_removal_is_a_change(self):
        # One block of 12 is under the 10% count_drop floor, so this page is clean.
        self.go()
        self.page, self.t = without(FIX, "OP17-001"), T2
        code, s = self.go()
        self.assertEqual((code, s["changed"], s["sites"]["en"]["removed"]), (0, True, 1))

    def test_control_a_new_cn_id_is_a_change(self):
        self.cn_rows = [(1, "OP01-001")]
        self.go()
        self.cn_rows, self.t = [(1, "OP01-001"), (2, "OP01-001P")], T2
        code, s = self.go()
        self.assertEqual((code, s["changed"], s["cn"]["added"], s["cn_run"]["added"] > 0), (0, True, 1, True))

    def test_a_cn_day_with_no_new_id_does_not_commit(self):
        self.cn_rows = [(1, "OP01-001")]
        self.go()
        self.t = T2
        code, s = self.go()
        self.assertEqual((code, s["changed"]), (0, False))

    def test_an_older_cn_list_stops_the_run(self):
        # The stale guard: a cn list fetched before the committed one is a replay.
        # Only cn goes back; en stays current, so its own replay guard cannot be what fires.
        self.cn_rows, self.t = [(1, "OP01-001")], T2
        self.go()
        self.cn_t = T1
        code, s = self.go()
        self.assertEqual((code, s["ok"]), (1, False))
        self.assertTrue(any("cn list fetched" in f and "older than the data" in f for f in s["failures"]), s["failures"])


class FailsLoudly(Harness, unittest.TestCase):
    def test_a_refused_page_fails_the_run(self):
        self.go()
        self.page, self.t = without(FIX, "OP17-001", "OP17-002"), T2  # 10 of 12: count_drop
        code, s = self.go()
        self.assertEqual((code, s["ok"], s["changed"]), (1, False, False))
        self.assertEqual(s["failures"], [f"en: 1 page(s) refused (count_drop): {SID}"])

    def test_a_fetch_failure_fails_and_never_runs(self):
        def runner(*a):
            raise AssertionError("run.py must not run over a failed fetch")
        self.status = 503
        code, s = self.go(runner=runner)
        self.assertEqual((code, s["ok"], s["changed"]), (1, False, False))
        self.assertIn(f"en:{SID} fetch failed: HTTPError: 503", s["failures"])
        self.assertFalse((self.repo / "data").exists())

    def test_a_cn_list_failure_fails(self):
        self.fetch_failures = ["cn: list failed: URLError: timed out"]
        code, s = self.go()
        self.assertEqual((code, s["ok"]), (1, False))
        self.assertFalse((self.repo / "data").exists())

    def test_a_run_stop_fails(self):
        self.assertEqual(self.go()[0], 0)  # positive control: the same pages pass
        self.t = "2026-10-07T00:00:00Z"  # older than the data: run.py refuses the replay
        code, s = self.go()
        self.assertEqual(code, 1)
        self.assertTrue(s["failures"][0].startswith("run stopped, nothing written:"), s["failures"])

    def test_committed_data_the_schema_refuses_fails_the_run(self):
        # A committed row carrying a field the closed schema refuses halts every
        # daily run until main is fixed, so it must exit 1 (a red Action), never skip.
        self.assertEqual(self.go()[0], 0)  # positive control
        p = next((self.repo / "data/products").glob("*.jsonl"))
        rows = p.read_text().splitlines()
        p.write_text("\n".join([rows[0][:-1] + ',"last_seen_at":"2026-10-01T00:00:00Z"}', *rows[1:]]) + "\n")
        before = {f: f.read_text() for f in (self.repo / "data").rglob("*.jsonl")}
        code, s = self.go()
        self.assertEqual((code, s["ok"], s["changed"]), (1, False, False))
        self.assertTrue(s["failures"][0].startswith("run stopped, nothing written: committed data:"), s["failures"])
        self.assertEqual({f: f.read_text() for f in (self.repo / "data").rglob("*.jsonl")}, before)


class ReleaseDates(Harness, unittest.TestCase):
    def test_the_daily_run_dates_the_product(self):
        code, s = self.go()
        self.assertEqual(code, 0)
        self.assertEqual(s["dates"]["en"]["dated"], 1)
        rec = json.loads((self.repo / "data/products/en.jsonl").read_text())
        self.assertEqual(rec["release_date"], "2026-08-22")

    def test_a_page_set_with_no_product_index_fails_the_run(self):
        self.with_products = False
        code, s = self.go()
        self.assertEqual(code, 1)
        self.assertIn("en: no product index in the page set, so no release dates were read", s["failures"])
        self.assertFalse(s["changed"])

    def test_undated_counts_a_new_dropdown_series_but_not_a_new_bucket(self):
        self.go()
        idx = index((SID, LABEL))
        self.assertFalse(daily.undated(self.repo, "en", idx))
        self.assertTrue(daily.undated(self.repo, "en", index((SID, LABEL), (NEW_SID, NEW_LABEL))))
        self.assertFalse(daily.undated(self.repo, "en", index((SID, LABEL), ("569901", "Promotion card"))))


class NewSeries(Harness, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.go()  # commits 569117, so it is a known product

    def test_known_options_make_no_notice(self):
        self.t = T2
        _, s = self.go()
        self.assertIsNone(s["notice_title"])
        self.assertFalse((self.out / "notice_title.txt").exists())

    def test_a_new_dropdown_option_makes_the_notice(self):
        self.options, self.t = [(SID, LABEL), (NEW_SID, NEW_LABEL)], T2
        _, s = self.go()
        self.assertEqual(s["new_series"], {"en": [(NEW_SID, NEW_LABEL)]})
        self.assertEqual((self.out / "notice_title.txt").read_text("utf-8"),
                         f"New series on the official card list: {NEW_LABEL}\n")
        body = (self.out / "notice_body.md").read_text("utf-8")
        self.assertIn(f"- en: {NEW_LABEL} (series {NEW_SID}) https://en.onepiece-cardgame.com/cardlist/?series={NEW_SID}",
                      body)

    def test_a_new_option_is_reported_even_when_the_run_fails(self):
        self.options, self.t, self.status = [(SID, LABEL), (NEW_SID, NEW_LABEL)], T2, 503
        code, s = self.go()
        self.assertEqual(code, 1)
        self.assertEqual(s["notice_title"], f"New series on the official card list: {NEW_LABEL}")

    def test_fake_option_control_needs_a_dry_run(self):
        with self.assertRaises(ValueError):
            self.go(fake=("en", "569999", "FAKE SET [XX-99]"))

    def test_fake_option_control_shows_the_post(self):
        self.t = T2
        code, s = self.go(dry_run=True, fake=("en", "569999", "FAKE SET [XX-99]"))
        self.assertEqual((code, s["dry_run"]), (0, True))
        self.assertEqual(s["notice_title"], "New series on the official card list: FAKE SET [XX-99]")

    def test_a_title_over_githubs_limit_is_shortened(self):
        found = {"en": [(str(569200 + i), f"BOOSTER PACK -A VERY LONG SET NAME NUMBER {i}- [OP-{i}]") for i in range(12)]}
        title, body = daily.notice(found)
        self.assertLessEqual(len(title), daily.TITLE_MAX)
        self.assertTrue(title.startswith("New series on the official card list: 12 new, first "), title)
        self.assertEqual(body.count("\n- en: "), 12)

    def test_parse_fake_refuses_a_bad_site(self):
        with self.assertRaises(ValueError):
            daily.parse_fake("xx:569999:FAKE")
        self.assertEqual(daily.parse_fake("jp:550999:A:B"), ("jp", "550999", "A:B"))


class FetchLine(unittest.TestCase):
    def test_counts_statuses_and_retries(self):
        log = [{"series_id": "1", "status": 200},
               {"series_id": "2", "status": 200, "attempt_errors": ["HTTPError: HTTP Error 429"]},
               {"series_id": "3", "status": None, "error": "HTTPError: HTTP Error 403",
                "attempt_errors": ["HTTPError: HTTP Error 403"] * 3}]
        self.assertEqual(daily.fetch_line("en", log, 61.4),
                         "fetch en: 3 pages, status {200: 2, failed: 1}, retried 1, 61s")

    def test_a_clean_site(self):
        self.assertEqual(daily.fetch_line("jp", [{"series_id": "1", "status": 200}] * 62, 120),
                         "fetch jp: 62 pages, status {200: 62}, retried 0, 120s")


class WorkflowOutputs(Harness, unittest.TestCase):
    """main() writes the two GITHUB_OUTPUT lines the commit and issue steps key on."""

    def main(self, *extra):
        gh_out = self.tmp / "gh_output"
        argv = ["daily.py", str(self.pages), str(self.repo), "en", "--out", str(self.out), *extra]
        real_run = run.run  # captured before the patch: the stand-in calls the real one with a fixed clock
        stand_in = lambda pageset, repo, sites: real_run(  # noqa: E731
            pageset, repo, sites, now=lambda: float(next(self.clock)))
        with mock.patch.object(daily, "fetch_all", self.fetcher), \
                mock.patch.object(run, "run", stand_in), \
                mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(gh_out)}), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            code = daily.main(argv)
        return code, gh_out.read_text(), printed.getvalue()

    def test_a_change_sets_changed_true(self):
        code, out, _ = self.main()
        self.assertEqual(code, 0)
        self.assertIn("changed=true\n", out)

    def test_no_change_sets_changed_false(self):
        self.main()
        self.t = T2
        code, out, printed = self.main()
        self.assertEqual((code, out.count("changed=false\n")), (0, 1), out)
        self.assertIn("ok=True changed=False dry_run=False", printed)

    def test_an_unmapped_product_kind_still_opens_the_notice(self):
        # Series digit 4 has no product kind: model.product_fields raises ValueError,
        # not RunError. The run must fail AND still say new_series=true.
        self.sid, self.options = "569417", [("569417", LABEL)]
        code, out, printed = self.main()
        self.assertEqual(code, 1)
        self.assertIn("new_series=true\n", out)
        self.assertIn("changed=false\n", out)
        self.assertIn("FAILED: run crashed, nothing committed: ValueError: en:569417: no product kind", printed)
        self.assertFalse((self.repo / "data").exists())

    def test_the_fake_control_prints_the_post_and_sets_new_series(self):
        self.main()
        self.t = T2
        code, out, printed = self.main("--dry-run", "--fake-option", "en:569999:FAKE SET [XX-99]")
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith("new_series=true\n"), out)
        self.assertIn("title: New series on the official card list: FAKE SET [XX-99]", printed)


class Don(Harness, unittest.TestCase):
    """The tcgcsv step inside the daily run: its changes count, a failed fetch fails the day."""

    def fetcher(self, pageset, sites, repo=None):
        out = super().fetcher(pageset, sites, repo)
        import test_tcgcsv
        g, p, pr = test_tcgcsv.fixture()
        d = Path(pageset) / "tcgcsv"
        for gid, rows in p.items():
            tcgcsv._write(d / "products" / f"{gid}.json", {"fetched_at": self.t, "products": rows})
        for gid, rows in pr.items():
            tcgcsv._write(d / "prices" / f"{gid}.json", {"fetched_at": self.t, "prices": rows})
        tcgcsv._write(d / "_groups.json", {"fetched_at": self.t, "groups": g})
        return out

    def test_don_lands_and_names_the_unpriced(self):
        code, s = self.go()
        self.assertEqual((code, s["changed"]), (0, True), s["failures"])
        self.assertEqual(s["tcgcsv_run"]["unpriced"], 2)
        self.assertEqual(s["tcgcsv_run"]["unpriced_ids"], [561656, 619595])
        self.assertTrue((self.repo / "data" / "printings" / "tcgcsv.jsonl").exists())

    def test_no_change_day(self):
        self.go()
        self.t = T2
        code, s = self.go()
        self.assertEqual((code, s["changed"]), (0, False), s["failures"])
        self.assertEqual((s["tcgcsv_run"]["added"], s["tcgcsv_run"]["changed"]), (0, 0))

    def test_a_failed_tcgcsv_fetch_fails_the_day(self):
        with mock.patch.object(fetch, "fetch_site", side_effect=AssertionError("no Bandai site in this control")), \
                mock.patch.object(cn, "fetch_pageset", return_value=[]), \
                mock.patch.object(tcgcsv, "fetch_pageset", side_effect=RuntimeError("503")), \
                contextlib.redirect_stdout(io.StringIO()):
            failures = daily.fetch_all(self.pages, [], self.repo)
        self.assertEqual(failures, ["tcgcsv: fetch failed: RuntimeError: 503"])


if __name__ == "__main__":
    unittest.main()
