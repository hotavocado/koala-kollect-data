"""Promo origin: the card-list name join, event-page claims and their controls.

alyssa 87163: the string join gets a CONTROL, not a sample. One known-true and
one known-false pair, and a sabotage test proving the false pair goes red when
the match is loosened. Without the sabotage the false pair could pass by never
being able to match at all.
"""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

import events as E
import model
import run


def hit(name, line):
    return E.find(line, E.matcher(name))


class NameJoinControl(unittest.TestCase):
    def test_known_true(self):
        self.assertEqual(hit("Event Pack Vol.1", "Event Pack Vol.1 x1"), "exact")

    def test_known_false_vol1_inside_vol15(self):
        self.assertIsNone(hit("Event Pack Vol.1", "Event Pack Vol.15 x1"))

    def test_sabotage_loosened_match_goes_red(self):
        # The false pair is only a control if a loosened match DOES hit it.
        loose = re.compile(re.escape(E.bare(E.core("Event Pack Vol.1"))))
        self.assertTrue(loose.search(E.bare("Event Pack Vol.15 x1")))

    def test_jp_vol4_over_a_line_break(self):
        # Vol.4 then 1パック on the next line must not read as Vol.41, and must still name Vol.4.
        self.assertEqual(hit("スタンダードバトルパックVol.4 カードリスト", "スタンダードバトルパックVol.4\n1パック"), "exact")
        self.assertIsNone(hit("スタンダードバトルパックVol.41", "スタンダードバトルパックVol.4\n1パック"))

    def test_spacing_and_brand_are_exact(self):
        self.assertEqual(hit("ONE PIECE CARD GAME Event Pack Vol.1", "Event Pack Vol. 1"), "exact")

    def test_case_and_width_are_normalised_for_review(self):
        self.assertEqual(hit("Event Pack Vol.1", "EVENT PACK VOL.1"), "normalised")

    def test_too_short_to_join(self):
        self.assertIsNone(E.matcher("P-1"))


class Dates(unittest.TestCase):
    def test_date_and_time_label(self):
        self.assertEqual(E.event_dates(["UK Games Expo", "Date & Time", "June 2, 2023 – June 4, 2023", "June 2, 2023:",
                                        "June 3, 2023:", "June 4, 2023:"]), ("2023-06-02", "2023-06-04"))

    def test_entry_window_is_not_the_event(self):
        self.assertEqual(E.event_dates(["応募期間", "2024年8月1日(木)～8月10日(土)", "開催日", "2024年9月1日(日)"]),
                         ("2024-09-01", None))

    def test_schedule_page_is_undated(self):
        self.assertIsNone(E.event_dates(["Philippines", "June 11, 2023", "Singapore", "July 15, 2023",
                                         "Indonesia", "July 29, 2023"]))

    def test_two_date_lines_still_date(self):
        self.assertEqual(E.event_dates(["Event", "June 11, 2023", "June 12, 2023"]), ("2023-06-11", None))

    def test_magazine_name_reads_only_its_on_sale_date(self):
        self.assertEqual(E.name_dates("Vジャンプ1月特大号付録（2023年11月21日売） カードリスト"), ("2023-11-21", None))

    def test_name_without_a_date(self):
        self.assertIsNone(E.name_dates("Event Pack Vol.1"))
        self.assertEqual(E.name_dates("Treasure Cup February 2025"), ("2025-02", None))


class Kinds(unittest.TestCase):
    def test_event_family_wins_over_the_pack_word(self):
        self.assertEqual(E.kind_of("CS 25-26 Event Pack"), "championship")
        self.assertEqual(E.kind_of("Standard Battle Pack Vol.9"), "store_tournament")
        self.assertEqual(E.kind_of("Online Regional Participation Pack Vol.1"), "online")
        self.assertEqual(E.kind_of("ONE PIECE HEROINES CAMPAIGN Dash Pack"), "retail_tieup")
        self.assertEqual(E.kind_of("Vジャンプ10月特大号付録（2024年8月21日売）"), "magazine_insert")

    def test_no_match_is_other(self):
        self.assertEqual(E.kind_of("OP-11 Release Event"), "other")


T0, T1, T2 = "2026-10-08T10:00:00Z", "2026-10-08T11:00:00Z", "2026-10-08T12:00:00Z"
URL = "https://en.onepiece-cardgame.com/cardlist/?series=569901"
EVENT = "https://en.onepiece-cardgame.com/events/2025/store_tournament_vol1.php"
PAGE = """<html><header>Event Pack Vol.1</header><main>
<h2>Store Tournament</h2><p>Date</p><p>June 1, 2025</p>
<h3>Participation Pack</h3><p>Event Pack Vol.1 x1</p>
<p>EVENT PACK VOL.2</p>
<h4>Event Pack Vol.1</h4><div data-type="component-opcg-cards"><img src="/images/cardlist/card/OP01-003.png"></div>
</main></html>"""


class Origin(unittest.TestCase):
    """link_site and events_site over a store with three promo printings."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "repo").mkdir()
        self.store = run.Store(self.tmp / "repo")
        r = self.store.recs
        r["product"]["en:569901"] = {"key": "en:569901", "site": "en", "kind": "promo_bucket", "product_url": URL}
        for i, (num, text) in enumerate([("OP01-001", "Event Pack Vol.1"), ("OP01-002", "Event Pack Vol.2"),
                                         ("OP01-003", "Event Pack Vol.2")]):
            prt, card = f"prt_00000000000{i}", f"card_00000000000{i}"
            r["printing"][prt] = {"key": prt, "card_key": card, "site": "en", "source_text": text}
            r["printing_product"][f"{prt}@en:569901"] = {"key": f"{prt}@en:569901", "printing_key": prt,
                                                         "product_key": "en:569901", "first_seen_at": T0}
            self.store.card_by_number[num] = card
        self.counts = {"added": 0, "changed": 0}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def pageset(self, index_at=T1, html=PAGE):
        d = self.tmp / "pages" / "en" / "events"
        (d / "pages").mkdir(parents=True, exist_ok=True)
        (d / "pages" / E.page_file(EVENT)).write_text(html, "utf-8")
        log = {"site": "en", "index": [{"walk": 1, "name": "events", "status": 200, "fetched_at": index_at}],
               "walks": [], "pages": [{"href": EVENT, "status": 200, "fetched_at": index_at}], "current": []}
        (d / "_log.json").write_text(json.dumps(log), "utf-8")
        return self.tmp / "pages" / "en"

    def valid(self):
        for rtype in ("distribution", "printing_distribution"):
            v = Draft202012Validator({"$schema": run.SCHEMA["$schema"], "$defs": run.SCHEMA["$defs"], "$ref": f"#/$defs/{rtype}"})
            for rec in self.store.recs[rtype].values():
                self.assertEqual([e.message for e in v.iter_errors(rec)], [], rec)

    def claims(self, source):
        return sorted((c["printing_key"], self.store.recs["distribution"][c["distribution_key"]]["name"], c["confidence"])
                      for c in self.store.recs["printing_distribution"].values() if c["source"] == source)

    def test_card_list_mints_one_distribution_per_name(self):
        by_name = run.link_site(self.store, "en", self.counts)
        self.assertEqual(sorted(by_name), ["Event Pack Vol.1", "Event Pack Vol.2"])
        self.assertEqual(len(self.store.recs["distribution"]), 2)
        d = self.store.recs["distribution"][model.mint("dist", "en|Event Pack Vol.1")]
        self.assertEqual((d["site"], d["region"], d["kind"]), ("en", "en", "event_pack"))
        self.assertEqual(self.claims("official_cardlist"), [
            ("prt_000000000000", "Event Pack Vol.1", "authoritative"), ("prt_000000000001", "Event Pack Vol.2", "authoritative"),
            ("prt_000000000002", "Event Pack Vol.2", "authoritative")])
        self.valid()

    def test_event_page_claims(self):
        by_name = run.link_site(self.store, "en", self.counts)
        out = run.events_site(self.store, "en", self.pageset(), {}, by_name, self.counts)
        self.assertEqual(self.claims("official_event"), [
            ("prt_000000000000", "Event Pack Vol.1", "authoritative"),  # the page prints the card list's string
            ("prt_000000000001", "Event Pack Vol.2", "inferred"),       # only case and width folded: review
            ("prt_000000000002", "Event Pack Vol.1", "inferred"),       # card block contradicts the card list
            ("prt_000000000002", "Event Pack Vol.2", "inferred")])
        self.assertEqual((out["claims"], out["inferred"], out["contradictions"]), (4, 3, 1))
        vol1 = next(c for c in self.store.recs["printing_distribution"].values()
                    if c["source"] == "official_event" and c["printing_key"] == "prt_000000000000")
        self.assertEqual((vol1["tier"], vol1["starts_on"], vol1["quantity_note"], vol1["quote"], vol1["observed_at"]),
                         ("participant", "2025-06-01", "Event Pack Vol.1 x1", "Event Pack Vol.1 x1", T1))
        self.assertNotIn("ends_on", vol1)
        self.valid()

    def test_rerun_changes_nothing_and_keeps_the_earliest_sighting(self):
        by_name = run.link_site(self.store, "en", self.counts)
        state = {}
        run.events_site(self.store, "en", self.pageset(T1), state, by_name, self.counts)
        before = json.dumps(self.store.recs["printing_distribution"], sort_keys=True)
        counts = {"added": 0, "changed": 0}
        run.link_site(self.store, "en", counts)
        run.events_site(self.store, "en", self.pageset(T2), state, by_name, counts)
        self.assertEqual(counts, {"added": 0, "changed": 0})
        self.assertEqual(json.dumps(self.store.recs["printing_distribution"], sort_keys=True), before)
        self.assertEqual(state, {"index_fetched_at": T2, "pages": {EVENT: T2}})

    def test_older_event_lists_than_the_data_stop_the_run(self):
        by_name = run.link_site(self.store, "en", self.counts)
        with self.assertRaisesRegex(run.RunError, "older than the data"):
            run.events_site(self.store, "en", self.pageset(T0), {"index_fetched_at": T1}, by_name, self.counts)

    def test_an_incomplete_event_log_stops_the_run(self):
        by_name = run.link_site(self.store, "en", self.counts)
        site = self.pageset()
        log = json.loads((site / "events" / "_log.json").read_text())
        log["pages"][0] = {"href": EVENT, "status": None, "error": "HTTPError: 503", "fetched_at": T1}
        (site / "events" / "_log.json").write_text(json.dumps(log))
        with self.assertRaisesRegex(run.RunError, "events: en: event page"):
            run.events_site(self.store, "en", site, {}, by_name, self.counts)

    def test_removed_listing_is_not_linked(self):
        self.store.recs["printing_product"]["prt_000000000000@en:569901"]["removed_at"] = T1
        self.assertNotIn("Event Pack Vol.1", run.link_site(self.store, "en", self.counts))


class ListFailure(unittest.TestCase):
    """A failed list page fails the run whatever its page number (codex P1 on #12).

    The pager stops at a page that lists nothing new. A failure on page 2 also
    stops it, so before this rule a 503 there truncated the event set silently.
    """

    def walk(self, page2):
        root = E.root_url("en")

        def get(url):
            if url == E.index_urls("en", "list_end", 2) and page2 == "503":
                raise OSError("HTTP Error 503: Service Unavailable")
            body = {
                E.index_urls("en", "list_end", 1): '<a href="/events/a.php">a</a>',
                E.index_urls("en", "list_end", 2): '<a href="/events/b.php">b</a>',
                E.index_urls("en", "list_end", 3): '<a href="/events/b.php">b</a>',
            }.get(url, "<p>nothing listed</p>")
            if url.startswith(root + "/events/") and url.endswith(".php") and "?" not in url:
                body = "<p>event</p>"
            return 200, body.encode()

        with tempfile.TemporaryDirectory() as d:
            return E.fetch_site("en", d, {}, pause=0, attempts=1, get=get)

    def test_clean_pager_passes(self):
        log = self.walk("ok")
        self.assertEqual(E.failures(log), [])
        self.assertIn(E.root_url("en") + "/events/b.php", [p["href"] for p in log["pages"]])

    def test_failed_second_page_fails_the_run(self):
        bad = E.failures(self.walk("503"))
        self.assertTrue(any("event list list_end-2" in f for f in bad), bad)


if __name__ == "__main__":
    unittest.main()
