"""cn list client on 12 verbatim rows from the live list endpoint (2026-10-08).

Only the paging envelope is synthesised; the rows are byte-for-byte what the API
served. The controls break the run the ways a live read breaks and assert
list_all refuses rather than returning a short or doubled list.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

import cn

ROWS = json.loads((Path(__file__).resolve().parent / "fixtures" / "cn_list_rows.json").read_text("utf-8"))


def fake_api(rows, limit, total=None, drop_from_last=0):
    pages = [rows[i:i + limit] for i in range(0, len(rows), limit)]
    if drop_from_last:
        pages[-1] = pages[-1][:-drop_from_last]

    def _get(path):
        page = int(path.split("page=")[1].split("&")[0])
        return {"code": 0, "page": {"totalCount": total if total is not None else len(rows),
                                    "totalPage": len(pages), "currPage": page, "list": pages[page - 1]}}
    return _get


class CnList(unittest.TestCase):
    def run_list(self, **kw):
        with mock.patch.object(cn, "_get", fake_api(**kw)), mock.patch.object(cn.time, "sleep"):
            return cn.list_all(limit=5)

    def test_reads_every_page(self):
        rows = self.run_list(rows=ROWS, limit=5)
        self.assertEqual([r["id"] for r in rows], [r["id"] for r in ROWS])

    def test_card_number_is_not_a_key(self):
        # The printed number carries cn's own suffixes (P-084_01); only id keys a row.
        self.assertTrue(all(isinstance(r["id"], int) for r in ROWS))
        self.assertIn("_", "".join(r["cardNumber"] for r in ROWS))

    def test_control_short_last_page_refuses(self):
        with self.assertRaises(RuntimeError):
            self.run_list(rows=ROWS, limit=5, drop_from_last=1)

    def test_control_repeated_id_refuses(self):
        doubled = ROWS[:-1] + [ROWS[0]]
        with self.assertRaises(RuntimeError):
            self.run_list(rows=doubled, limit=5)


if __name__ == "__main__":
    unittest.main()
