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
from pathlib import Path

import cnmodel

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


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
    tmp.replace(path)


def fetch_pageset(cn_dir, known_ids, pause=1.0):
    """Write the cn page set: the list, the products and a detail for every listed id not in known_ids.

    cn_dir/_list.json      {"fetched_at", "rows"}
    cn_dir/_products.json  {"fetched_at", "products"}
    cn_dir/detail/{id}.json {"fetched_at", "info"}
    A detail already on disk is kept, so a re-run after a failure fetches only
    what is missing. Returns the failures; any one fails the daily run.

    A held id is fetched again when a new id's list row equals it apart from
    the id: that is what a double-save looks like, and run_cn needs both
    details to tell (cnmodel.duplicate_signature).
    """
    cn_dir = Path(cn_dir)
    t = _utc()
    rows = list_all(pause=pause)
    _write(cn_dir / "_list.json", {"fetched_at": t, "rows": rows})
    _write(cn_dir / "_products.json", {"fetched_at": _utc(), "products": products()})
    new_sigs = {cnmodel.list_signature(r) for r in rows if r["id"] not in known_ids}
    failures = []
    for r in rows:
        out = cn_dir / "detail" / f"{r['id']}.json"
        twin = r["id"] in known_ids and cnmodel.list_signature(r) in new_sigs
        if (r["id"] in known_ids and not twin) or out.exists():
            continue
        try:
            _write(out, {"fetched_at": _utc(), "info": detail(r["id"])})
        except Exception as e:  # noqa: BLE001  one failed id is one failure, the rest still fetch
            failures.append(f"cn: detail {r['id']} failed: {type(e).__name__}: {e}")
        time.sleep(pause)
    return failures
