"""Client for the mainland-China card API (www.onepiece-cardgame.cn, run by Windo).

Read-only, no login. The list endpoint pages 100 rows at a time when given
`limit` (it ignores `pageSize`/`size`) and carries only id, image, product name
and a printed card number. Card facts need one detail call per id.

Identity: the numeric `id` is unique across the list (4,927 of 4,927 on
2026-10-08). The printed `cardNumber` is NOT (3,280 distinct): reprints repeat
it with no suffix, so it can never key a printing. Whether `id` is stable over
time is unmeasured; the ingest snapshots it and diffs later runs.
"""
import json
import time
import urllib.request

BASE = "https://webadmin.windoent.com/front/op-public"
UA = "Mozilla/5.0 (compatible; koala-kollect-ingest/0.1; +https://github.com/hotavocado/koala-kollect-data)"


def _get(path, timeout=60):
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.load(r)
    if body.get("code") != 0:
        raise RuntimeError(f"cn api {path}: code={body.get('code')} msg={body.get('msg')}")
    return body


def list_all(limit=100, pause=1.0):
    """Every row of the card list. Raises if the row count disagrees with totalCount."""
    rows, page = [], 1
    while True:
        p = _get(f"cardList/cardlist/weblist?page={page}&limit={limit}")["page"]
        rows.extend(p["list"])
        if page >= p["totalPage"]:
            total = p["totalCount"]
            break
        page += 1
        time.sleep(pause)
    if len(rows) != total or len({r["id"] for r in rows}) != total:
        raise RuntimeError(f"cn list: {len(rows)} rows, {len({r['id'] for r in rows})} unique ids, totalCount {total}")
    return rows


def detail(card_id):
    return _get(f"cardList/cardlist/webInfo/{card_id}")["info"]


def products():
    return _get("cardType/cardofferingtype/cachelist")["list"]


def card_types():
    return _get("cardType/cardtype/cachelist")["list"]
