"""The fetch log is the done-check's source of truth (codex round 1 on PR 1).

A failed series exists only in the log, so a check that reads the HTML files on
disk passes a run that lost pages. These build a site directory the way
fetch.py does and break it the ways a real run breaks.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fetch
from donecheck import check_site

FIX = Path(__file__).resolve().parent / "fixtures"


class SiteDir:
    """A fetch.py-shaped site directory holding en_569117 as series 569117."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        self.dir = self.root / "en"
        self.dir.mkdir()
        shutil.copy(FIX / "en_569117.html", self.dir / "569117.html")
        self.log = [{"site": "en", "series_id": "569117", "label": "x", "status": 200, "fetched_at": "2026-10-08T00:00:00Z"}]

    def write_log(self):
        (self.dir / "_fetch_log.json").write_text(json.dumps(self.log))

    def close(self):
        shutil.rmtree(self.root)


class DoneCheckReadsTheLog(unittest.TestCase):
    def setUp(self):
        self.s = SiteDir()

    def tearDown(self):
        self.s.close()

    def test_clean_run_passes(self):
        self.s.write_log()
        self.assertEqual(check_site(self.s.dir)["failures"], [])

    def test_control_failed_series_fails(self):
        self.s.log.append({"site": "en", "series_id": "569118", "label": "y", "status": None,
                           "error": "HTTPError: 503", "fetched_at": "2026-10-08T00:00:00Z"})
        self.s.write_log()
        self.assertIn("569118: fetch failed (HTTPError: 503)", check_site(self.s.dir)["failures"])

    def test_control_stale_page_fails(self):
        shutil.copy(FIX / "en_569901.html", self.s.dir / "569901.html")
        self.s.write_log()
        self.assertIn("569901: page on disk with no entry in this run's log (stale)", check_site(self.s.dir)["failures"])

    def test_control_missing_log_fails(self):
        self.assertTrue(any("no _fetch_log.json" in f for f in check_site(self.s.dir)["failures"]))

    def test_control_empty_log_fails(self):
        self.s.log = []
        (self.s.dir / "569117.html").unlink()
        self.s.write_log()
        self.assertIn("_fetch_log.json lists no series", check_site(self.s.dir)["failures"])


class FetchRetryLog(unittest.TestCase):
    INDEX = (FIX / "en_569117.html").read_bytes()

    def run_fetch(self, outcomes, out):
        calls = iter(outcomes)

        def _get(url, timeout=90):
            if url.endswith("/cardlist/"):
                return 200, self.INDEX
            o = next(calls)
            if isinstance(o, Exception):
                raise o
            return 200, o

        with mock.patch.object(fetch, "_get", _get), mock.patch.object(fetch.time, "sleep"), \
                mock.patch.object(fetch, "series_options", lambda _: [("569117", "x")]):
            return fetch.fetch_site("en", out)

    def test_success_after_retry_carries_no_error(self):
        with tempfile.TemporaryDirectory() as out:
            [e] = self.run_fetch([OSError("reset"), b"<html/>"], out)
            self.assertEqual(e["status"], 200)
            self.assertNotIn("error", e)
            self.assertEqual(e["attempt_errors"], ["OSError: reset"])

    def test_control_exhausted_retries_drop_a_stale_page(self):
        with tempfile.TemporaryDirectory() as out:
            (Path(out) / "en").mkdir()
            (Path(out) / "en" / "569117.html").write_text("stale from an earlier run")
            [e] = self.run_fetch([OSError("a"), OSError("b"), OSError("c")], out)
            self.assertIsNone(e["status"])
            self.assertEqual(e["error"], "OSError: c")
            self.assertFalse((Path(out) / "en" / "569117.html").exists())


if __name__ == "__main__":
    unittest.main()
