"""Release dates from each site's own product index. Read-only fetch; the run applies it.

Each Bandai site lists its products at /products/?page=N, newest first. Every
item carries the product page's URL and, when the site gives one, a machine
date: <time datetime="YYYY-MM-DD">. The date says nothing about which card list
it belongs to; the product page does, by linking cardlist/?series=NNNNNN. So the
date comes from the index and the series from the product page, and the join is
that series link equal to product.series_id on the same site. Never a code
parsed from the title: the ST-01 to ST-04 bundle page names only ST-01 and
links four series, and the en op14-eb04 page links 569114 under a compound code
(survey, roberto-internal 86970).

The index sorts by date with no tiebreak and sorts again on every request, so
items sharing a date that straddle a page boundary can be listed twice in one
walk and not at all (measured 2026-10-08: en OP-06 and dp03 share 2024-03-15
across pages 13 and 14; one walk listed dp03 twice and OP-06 never). So the
index is walked more than once and the items unioned, until a walk adds nothing.
That lowers the chance of a miss; it cannot prove none, and a missed product
stays undated until a later day's walk lists it.

A product page's series links are cached in state/product_pages/{site}.json, so
a daily run fetches only the index and the product pages it has not read. A page
that linked no series is read again while the site still has a product with no
date, because a page published before its card list goes live links nothing yet.
"""
import hashlib
import html as htmllib
import json
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

HOSTS = {"en": "en", "asia-en": "asia-en", "jp": "www", "tc": "asia-tc"}
UA = "Mozilla/5.0 (compatible; koala-kollect-ingest/0.1; +https://github.com/hotavocado/koala-kollect-data)"
# Kinds that are card pools, not products: no single date is right for them
# (x801 is linked by several premium collection pages with different dates).
UNDATED_KINDS = {"limited", "promo_bucket"}
# Double Pack Sets are sealed products with no card list of their own (their
# cards are a booster's), so no product page links a series and the card-list
# walk never mints them. The index is their only record, and a DON packed in one
# needs a product to name as its origin (don_sets). en only: measured 2026-10-10
# over three walks of en's 18 index pages, twelve items titled
# "Double Pack Set Vol.N [DP-NN]", DP-01 to DP-12, every one dated.
DOUBLE_PACK_SITES = {"en"}
_DOUBLE_PACK = re.compile(r"\[(DP-[0-9]{2})\]$")
# Index walks: at least MIN_WALKS, then stop at the first walk that lists no
# product page the earlier walks missed, and never more than MAX_WALKS.
MIN_WALKS, MAX_WALKS = 2, 4

_ITEM = re.compile(r'<li class="linkListColBox"[^>]*>(.*?)</li>', re.S)
_HREF = re.compile(r'<a href="([^"]+)"')
_TITLE = re.compile(r'<h4 class="linkListColTitle">(.*?)</h4>', re.S)
_TIME = re.compile(r'<time[^>]*\bdatetime="([^"]*)"')
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_PAGER = re.compile(r'href="\?page=([0-9]+)"')
_SERIES = re.compile(r"cardlist/\?series=([0-9]+)")


def index_url(site, page):
    return f"https://{HOSTS[site]}.onepiece-cardgame.com/products/?page={page}"


def product_page_prefix(site):
    return f"https://{HOSTS[site]}.onepiece-cardgame.com/products/"


def parse_index(html, site):
    """[{href, title, date}] in page order. date is None when the site gives none.

    Raises ValueError on anything outside the measured shape (an item with no
    link, a link off the site's products path, two different dates on one item,
    a date that is not YYYY-MM-DD), so a layout change stops the run.
    """
    items = []
    for li in _ITEM.findall(html):
        href = _HREF.search(li)
        if not href:
            raise ValueError(f"{site}: index item with no link")
        href = href.group(1)
        if not href.startswith(product_page_prefix(site)) or urlsplit(href).query:
            raise ValueError(f"{site}: index link off the products path: {href!r}")
        dates = {d for d in _TIME.findall(li) if d}
        if len(dates) > 1:
            raise ValueError(f"{site}: {href} carries two dates {sorted(dates)}")
        date = dates.pop() if dates else None
        if date is not None and not _DATE.match(date):
            raise ValueError(f"{site}: {href} date {date!r} is not YYYY-MM-DD")
        title = _TITLE.search(li)
        title = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", title.group(1)))).strip() if title else ""
        items.append({"href": href, "title": title, "date": date})
    return items


def last_page(html):
    pages = [int(p) for p in _PAGER.findall(html)]
    return max(pages) if pages else None


def series_links(html):
    """Every series id a product page links, sorted. Empty for accessories."""
    return sorted(set(_SERIES.findall(html)))


def is_pre_release(href, title):
    """A pre-release edition page: its date is never the product's date.

    Measured 2026-10-08, one page on all four sites: en decks/st01-04_pre.php,
    titled "STARTER DECK -Straw Hat Crew- [ST-01 PRE]", 2022-09-30, linking
    569001, whose retail page st01-04.php is 2022-12-02.
    """
    stem = urlsplit(href).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem.lower().endswith("_pre") or bool(re.search(r"\bPRE\]", title))


def index_file(page, walk=1):
    return f"index-{page}.html" if walk == 1 else f"index-{page}.w{walk}.html"


def page_file(href):
    return hashlib.sha256(href.encode()).hexdigest()[:16] + ".html"


def _get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _fetch(url, path, attempts, pause, get):
    entry, errors = {}, []
    for attempt in range(attempts):
        try:
            status, body = get(url)
            path.write_bytes(body)
            entry.update(status=status, sha256=hashlib.sha256(body).hexdigest())
            break
        except Exception as e:  # recorded, never swallowed
            errors.append(f"{type(e).__name__}: {e}")
            time.sleep(pause * (attempt + 1))
    else:
        entry.update(status=None, error=errors[-1])
        path.unlink(missing_ok=True)
    if errors:
        entry["attempt_errors"] = errors
    entry["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return entry


def needs_fetch(cache, items, undated):
    """The product pages to read this run, in index order.

    A page never read is fetched. A page that linked a series is never fetched
    again. A page that linked nothing is fetched again only while the site has
    an undated product, because that page may be the one that will date it.
    """
    out = []
    for it in items:
        known = cache.get(it["href"])
        if known is None or (undated and not known["series"]):
            if it["href"] not in out:
                out.append(it["href"])
    return out


def fetch_site(site, out_dir, cache, undated, pause=1.5, attempts=3, get=_get):
    """Walk the index MIN_WALKS to MAX_WALKS times, then fetch the product pages needs_fetch names.

    Writes {out_dir}/{site}/products/: index-{N}.html (walk 1),
    index-{N}.w{W}.html (later walks), pages/{hash}.html and _log.json. Returns
    the log; log["walks"] says how many items each walk listed and how many
    product pages it listed that no earlier walk had. A failed page is recorded
    with its error, and the caller treats it as a failed fetch.
    """
    out = Path(out_dir) / site / "products"
    (out / "pages").mkdir(parents=True, exist_ok=True)
    log = {"site": site, "index": [], "walks": [], "pages": []}
    first = _fetch(index_url(site, 1), out / index_file(1), attempts, pause, get)
    log["index"].append(dict(first, page=1, walk=1))
    n = last_page((out / index_file(1)).read_text("utf-8", "replace")) if first.get("status") == 200 else None
    if first.get("status") == 200 and n is None:
        log["index"][0]["error"] = "no pager on index page 1 (layout change?)"
    items, seen = [], set()
    for walk in range(1, MAX_WALKS + 1):
        for page in range(1, (n or 1) + 1):
            if (walk, page) == (1, 1):
                continue
            time.sleep(pause)
            entry = _fetch(index_url(site, page), out / index_file(page, walk), attempts, pause, get)
            log["index"].append(dict(entry, page=page, walk=walk))
        listed = []
        for e in log["index"]:
            if e["walk"] == walk and e.get("status") == 200:
                listed += parse_index((out / index_file(e["page"], walk)).read_text("utf-8", "replace"), site)
        new = {it["href"] for it in listed} - seen
        seen |= new
        items += listed
        log["walks"].append({"walk": walk, "items": len(listed), "new": len(new)})
        if n is None or (walk >= MIN_WALKS and not new):
            break
    for href in needs_fetch(cache, items, undated):
        time.sleep(pause)
        log["pages"].append(dict(_fetch(href, out / "pages" / page_file(href), attempts, pause, get), href=href))
    (out / "_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), "utf-8")
    return log


def failures(log):
    """Every failed fetch in a products log, as text. Empty means the log is complete."""
    site = log["site"]
    out = [f"{site}: products index page {e['page']}: {e['error']}" for e in log["index"] if "error" in e]
    out += [f"{site}: product page {e['href']}: {e['error']}" for e in log["pages"] if "error" in e]
    if not any(e.get("status") == 200 for e in log["index"]):
        out.append(f"{site}: no products index page fetched")
    return out


def read(site_dir, site):
    """(index items, {href: (series list, fetched_at)} for the pages fetched, index fetched_at).

    Reads the products part of a page set. The items are the union of every
    walk, one per (product page, title), in the order first listed: a bundle
    page is listed once per deck it holds, under that deck's title. Raises
    ValueError when the log is incomplete, so a run never dates from half an
    index, and when one page is listed with two different dates.
    """
    out = Path(site_dir) / "products"
    log = json.loads((out / "_log.json").read_text("utf-8"))
    bad = failures(log)
    if bad:
        raise ValueError("; ".join(bad))
    items, seen, date_of = [], set(), {}
    for e in sorted(log["index"], key=lambda e: (e.get("walk", 1), e["page"])):
        for it in parse_index((out / index_file(e["page"], e.get("walk", 1))).read_text("utf-8", "replace"), site):
            if date_of.setdefault(it["href"], it["date"]) != it["date"]:
                raise ValueError(f"{site}: {it['href']} listed with two dates {date_of[it['href']]} and {it['date']}")
            if (it["href"], it["title"]) not in seen:
                seen.add((it["href"], it["title"]))
                items.append(it)
    if not items:
        raise ValueError(f"{site}: products index parsed no items (layout change?)")
    pages = {}
    for e in log["pages"]:
        html = (out / "pages" / page_file(e["href"])).read_text("utf-8", "replace")
        pages[e["href"]] = (series_links(html), e["fetched_at"])
    index_at = min(e["fetched_at"] for e in log["index"])
    return items, pages, index_at


def dates_by_series(items, links):
    """{series_id: [(date, href, pre)]} for every dated index item whose page links that series.

    links: {href: [series ids]}. An item whose page has not been read links
    nothing, so it dates nothing: there is no fallback to the title.
    """
    out = {}
    for it in items:
        if not it["date"]:
            continue
        pre = is_pre_release(it["href"], it["title"])
        for sid in links.get(it["href"], []):
            hit = (it["date"], it["href"], pre)
            if hit not in out.setdefault(sid, []):
                out[sid].append(hit)
    return out


def double_packs(items):
    """({code: index item}, [code of each undated item]) for the items titled "... [DP-NN]".

    An undated item is named and not returned: a product row carries a date or
    is not written (an undated row would make the daily run re-read every
    unlinked product page, daily.undated). One code listed with two pages or
    two dates has no right answer, so that raises rather than picks one.
    """
    dated, undated = {}, []
    for it in items:
        m = _DOUBLE_PACK.search(it["title"])
        if not m:
            continue
        code = m.group(1)
        if not it["date"]:
            undated.append(code)
            continue
        old = dated.setdefault(code, it)
        if (old["href"], old["date"]) != (it["href"], it["date"]):
            raise ValueError(f"{code} listed twice: {old['href']} {old['date']} and {it['href']} {it['date']}")
    return dated, sorted(set(undated) - set(dated))


def retail_date(hits):
    """(date, source href) from one series' index hits, or None when no retail page dates it.

    A pre-release page never dates a product. Two retail pages with different
    dates have no right answer, so that raises rather than picks one. When
    several retail pages agree, the source is the first by URL, so a re-run
    names the same one.
    """
    retail = [(d, h) for d, h, pre in hits if not pre]
    if not retail:
        return None
    dates = {d for d, _ in retail}
    if len(dates) > 1:
        raise ValueError(f"retail pages disagree: {sorted(retail)}")
    return min(retail, key=lambda x: x[1])
