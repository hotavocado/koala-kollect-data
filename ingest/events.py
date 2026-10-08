"""Promo origin from each site's event and topic pages. Read-only fetch; the run applies it.

The card list already names where every promo printing came from: its
"Card Set(s)" / 入手情報 string (printing.source_text). That string is the
distribution, and the run links each promo printing to it from the card list
alone. These pages add what the card list does not carry: when it was handed
out, to whom (the tier) and how many. One claim per page that names the pack.

The join is the card list's string found on the page, never a card number: one
number covers several printings, and en pages show card blocks that are not
prizes at all (a Prohibited Cards list). The match is boundary-safe, so the
card list's "Tournament Pack Vol.1" is not found inside a page's
"Tournament Pack Vol.15". A claim is authoritative when the page prints the
card list's string as it stands; when it only matches after normalising width,
case, spacing and punctuation, it is inferred, for review.

Measured 2026-10-08 on all four sites: the event lists are sorted by date and
sorted again on every request, so one walk can list an event twice and two
walks list the pages in different orders (jp: 4 of 10 list pages differed
between two walks, and one walk listed 56 entries for 54 events). So the lists
are walked more than once and unioned, the same as the product index.

Pages are cached in state/event_pages/{site}.json. A page is read once, and
read again only while it is on the site's current events list, because an
event that has not ended can still change its prize list.
"""
import hashlib
import html as htmllib
import json
import re
import time
import unicodedata
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

HOSTS = {"en": "en", "asia-en": "asia-en", "jp": "www", "tc": "asia-tc"}
UA = "Mozilla/5.0 (compatible; koala-kollect-ingest/0.1; +https://github.com/hotavocado/koala-kollect-data)"
MIN_WALKS, MAX_WALKS = 2, 4
# The two paginated lists stop at the first page that lists nothing new; this
# caps a pager that never ends.
MAX_LIST_PAGES = 60
SECTIONS = ("events", "topics", "news")
LIST_PAGES = {"", "list_end.php", "list_archive.php", "official-shop.html", "index.php"}

_A = re.compile(r'<a\b[^>]*\bhref="([^"]+)"', re.S)
_DROP = re.compile(r"<script.*?</script>|<style.*?</style>|<header.*?</header>|<footer.*?</footer>|<nav.*?</nav>", re.S)
_TAG = re.compile(r"<[^>]+>")
_MAIN = re.compile(r"<main\b.*?</main>", re.S)

# --- the name join ----------------------------------------------------------

# Decoration the card list adds around the distribution's name: jp ends every
# string with カードリスト (the label of its card list button), en often opens
# with "Included in". Removed before matching, kept in the distribution's name.
_DECORATION = re.compile(r"\s*カードリスト\s*$|^\s*Included in\s+", re.I)
# The game's own name, which pages and card lists add or drop freely.
_BRAND = re.compile(r"one ?piece ?card ?game|one ?piece ?カードゲーム|one ?piece ?卡牌對戰|one ?piece ?卡片對戰")
_PUNCT = re.compile(r"[\s\-‐–—_~〜・『』「」【】\[\]()（）!！?？.,、。:：'\"“”’]")


def core(name):
    """The card list's string with its own decoration removed: what a page prints."""
    return _DECORATION.sub("", name).strip()


def norm(s):
    """Width, case, spacing and punctuation folded. A line break, or space between
    two digits, survives as | so "Vol.4" over "1パック" cannot read as "Vol.41"."""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"\n+", "|", s)
    s = re.sub(r"(?<=\d)\s+(?=\d)", "|", s)
    s = _BRAND.sub("", s)
    return _PUNCT.sub("", s)


def bare(s):
    """Whitespace and the game's own name removed, nothing else: pages and card
    lists space "Vol.4" both ways and add or drop the brand freely, and neither
    changes which pack is meant. A line break, or space between two digits,
    survives as | for the same reason as in norm."""
    s = re.sub(r"\n+", "|", s)
    s = re.sub(r"(?<=\d)\s+(?=\d)", "|", s)
    s = _BRAND.sub("", s.replace("ONE PIECE", "one piece").replace("One Piece", "one piece"))
    return re.sub(r"\s+", "", s)


# A distribution's kind, from its card-list name. First match wins, so the event
# family (championship, store battle) wins over the pack word inside it ("CS
# 25-26 Event Pack" is a championship). No match is "other", never a guess.
KINDS = [
    ("online", re.compile(r"\bonline\b", re.I)),
    ("championship", re.compile(r"championship|\bcs ?\d|regionals?\b|world tour|チャンピオンシップ|冠軍錦標賽|亞洲錦標賽|grand asia open", re.I)),
    ("pre_release", re.compile(r"pre-?release", re.I)),
    ("movie", re.compile(r"film red|映画|劇場|電影", re.I)),
    ("magazine_insert", re.compile(r"ジャンプ|magazine|付録|応募者全員|雜誌", re.I)),
    ("store_tournament", re.compile(r"store tournament|treasure cup|flagship|tournament (?:pack|kit)|winner pack|winner prize|sealed battle"
                                    r"|standard battle|grand battle|store qualifier|store \d-on-\d|フラッグシップ|スタンダードバトル"
                                    r"|エクストラグランドバトル|8パックバトル|旗艦戰|常規賽|8包現開賽|特規大獎賽", re.I)),
    ("meetup", re.compile(r"pirates party|meet-?up|交流会|交流會", re.I)),
    ("retail_tieup", re.compile(r"一番くじ|dodgers|dortmund|\bpsa\b|campaign|キャンペーン|購入者特典|獲取活動", re.I)),
    ("bundle", re.compile(r"box topper|整盒購買特典", re.I)),
    ("promo_pack", re.compile(r"promotion (?:pack|card set)|プロモーションパック|プロモーションカードセット|推廣卡包|推廣卡套組|推廣包", re.I)),
    ("event_pack", re.compile(r"event pack|celebration pack|battle pack|dash pack|top player pack", re.I)),
]


def kind_of(name):
    for kind, pat in KINDS:
        if pat.search(name):
            return kind
    return "other"


def matcher(name):
    """(exact pattern, normalised pattern) for one distribution name, or None when too short to join on.

    exact compares bare() forms: the card list's string as the page prints it,
    give or take spacing and the brand. normalised also folds width, case and
    punctuation; a claim that needs it is inferred, for review (alyssa 87163).
    Both refuse a digit straight after the name, so Vol.1 never matches inside Vol.15.
    """
    c = core(name)
    n = norm(c)
    if len(n) < 4:
        return None
    return re.compile(re.escape(bare(c)) + r"(?![0-9０-９])"), re.compile(re.escape(n) + r"(?![0-9])")


def find(line, pats):
    """"exact", "normalised" or None: how one page line names the distribution."""
    exact, normed = pats
    if exact.search(bare(line)):
        return "exact"
    if normed.search(norm(line)):
        return "normalised"
    return None


# --- page text ---------------------------------------------------------------

def lines_of(html):
    """The page's main content as stripped text lines, in document order."""
    m = _MAIN.search(html)
    body = m.group(0) if m else html
    body = _DROP.sub("", body)
    text = htmllib.unescape(_TAG.sub("\n", body)).replace("​", "")
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


# --- dates ---------------------------------------------------------------------

DATE_LABELS = re.compile(
    r"^(?:date|dates|date (?:&|and) time|event date|event period|period|schedule|distribution period"
    r"|開催日|開催日時|開催期間|開催日程|日程|配布期間|実施期間"
    r"|舉辦日期|舉辦期間|活動日期|活動期間)\s*[:：]?$", re.I)
# Not labels, measured on the survey pages: 応募期間 / 事前応募期間 (the entry
# window, not the event), 開催日程／会場 (heads a per-venue list), Legal Date
# (when a card becomes legal).
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
_MON = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?"
# Every date-ish piece in a value, in order: a CJK y/m/d, an English month (with
# an optional day), a bare day after a range mark, or a year.
_PIECE = re.compile(
    r"(?P<cy>\d{4})年\s*(?P<cm>\d{1,2})月(?:\s*(?P<cd>\d{1,2})日)?"
    r"|(?<![\d年])(?P<cm2>\d{1,2})月(?:\s*(?P<cd2>\d{1,2})日)?"
    r"|(?<![\d月])(?P<cd3>\d{1,2})日"
    r"|(?P<em>" + _MON + r")(?:\s+(?P<ed>\d{1,2})(?:st|nd|rd|th)?\b)?"
    r"|(?<=[-–～~])\s*(?P<ed2>\d{1,2})\b(?!\s*年)"
    r"|(?P<y>(?<!\d)20\d{2}(?!\d))", re.I)


def _short_month(s):
    s = s.lower().rstrip(".")
    for full, i in _MONTHS.items():
        if full.startswith(s[:3]):
            return i
    raise ValueError(s)


def parse_dates(value):
    """(starts_on, ends_on) from one date value, partial dates allowed, or None.

    Handles the shapes measured on the four sites, among them
    "2023年8月26日(土) ～ 2023年9月3日(日)", "2026年9月1日(火) ～ 30日(水)",
    "2026年5月～2026年7月", "October 29-November 2, 2025", "May 1 – June 30, 2026",
    "December 2, 2022 – February 2023" and "October 10-11, 2026". A year the
    value leaves out is taken from the nearest piece that has one (jp prints it
    first, en last). No year anywhere means no date: never a guess.
    """
    pts = []  # [year or None, month or None, day or None]
    for m in _PIECE.finditer(value):
        g = m.groupdict()
        if g["cy"]:
            pts.append([int(g["cy"]), int(g["cm"]), int(g["cd"]) if g["cd"] else None])
        elif g["cm2"]:
            pts.append([None, int(g["cm2"]), int(g["cd2"]) if g["cd2"] else None])
        elif g["cd3"]:
            pts.append([None, None, int(g["cd3"])])
        elif g["em"]:
            pts.append([None, _short_month(g["em"]), int(g["ed"]) if g["ed"] else None])
        elif g["ed2"]:
            pts.append([None, None, int(g["ed2"])])
        elif g["y"]:
            # A bare year closes the English pieces before it that have none
            # ("May 1 – June 30, 2026"). With none open it dates nothing.
            y = int(g["y"])
            for p in reversed(pts):
                if p[0] is not None:
                    break
                p[0] = y
    pts = [p for p in pts if any(v is not None for v in p)]
    if not pts:
        return None
    # Fill the gaps forward: a bare day takes the month and year before it, a
    # bare month the year before it.
    for i, p in enumerate(pts):
        if i and p[1] is None:
            p[1] = pts[i - 1][1]
        if i and p[0] is None:
            p[0] = pts[i - 1][0]
    # en prints the year last ("May 1 – June 30, 2026"): fill backwards too.
    for i in range(len(pts) - 2, -1, -1):
        if pts[i][0] is None:
            pts[i][0] = pts[i + 1][0]
    pts = [p for p in pts if p[0] is not None and p[1] is not None]
    if not pts:
        return None

    def fmt(p):
        y, mth, d = p
        if not (1 <= mth <= 12) or (d is not None and not 1 <= d <= 31):
            raise ValueError(f"impossible date {p} in {value!r}")
        return f"{y:04d}-{mth:02d}" + (f"-{d:02d}" if d else "")

    starts, ends = fmt(pts[0]), fmt(pts[-1])
    if ends < starts[: len(ends)]:
        return None  # out of order: some other date on the line, not a range
    return starts, (ends if ends != starts else None)


_SALE = re.compile(r"[（(]([^（）()]*?)売[）)]")


def name_dates(name):
    """(starts_on, ends_on) the card list's own pack name carries, or None.

    A magazine's name carries its issue month beside its on-sale date in
    brackets ("Vジャンプ1月特大号付録（2023年11月21日売）"). The issue month is
    not a date the card came out on, so when an on-sale date is there it is the
    only thing read.
    """
    c = core(name)
    m = _SALE.search(c)
    try:
        return parse_dates(m.group(1) if m else c)
    except ValueError:
        return None


_INLINE_LABEL = re.compile(r"^(?:date|dates|event date|event period|period|日程|開催日|開催日程|開催期間|舉辦日期|活動日期|活動期間)\s*[:：]\s*(.+)$", re.I)
# What may sit beside the date on a line that is nothing but a date: weekdays,
# range marks, "until stocks last", 予定 (planned).
_DATE_NOISE = re.compile(
    r"\((?:[月火水木金土日]|祝|mon|tue|wed|thu|fri|sat|sun)[^)]*\)|（[^）]*）|予定|until stocks last|onwards?|from|to|and"
    r"|[\s,.\-–～~:：/]|\d|年|月|日|st|nd|rd|th|" + _MON, re.I)


def _try(value):
    try:
        return parse_dates(value)
    except ValueError:
        return None


def event_dates(lines):
    """(starts_on, ends_on or None) for the page, or None.

    In order: the first date label (alone on its line, the value on one of the
    next two lines, or "Date: value" on one line); else the first line that is
    nothing but a date. A page with neither is undated, never guessed.
    """
    for i, ln in enumerate(lines):
        if DATE_LABELS.match(ln):
            for value in lines[i + 1:i + 3]:
                got = _try(value)
                if got:
                    return got
            return None
        m = _INLINE_LABEL.match(ln)
        if m:
            got = _try(m.group(1))
            if got:
                return got
    # A page with more than two date-only lines is a schedule of several events
    # (en "Products and Events Schedule"), and its first date is not the date of
    # whatever it hands out.
    dated = [g for g in (_try(ln) for ln in lines
                         if len(ln) <= 60 and re.search(r"20\d\d", ln) and not _DATE_NOISE.sub("", ln)) if g]
    if 0 < len(set(dated)) <= 2:
        return dated[0]
    return None


# --- tier and quantity ---------------------------------------------------------

# A heading line maps to a tier only when the WHOLE line is a tier word, so a
# sentence that mentions winners does not set the tier of the pack under it.
TIERS = [
    ("participant", re.compile(r"^(?:participation(?: prizes?| pack)?|participants?|参加記念品|参加賞|參加紀念品|参加者全員)$", re.I)),
    ("winner", re.compile(r"^(?:winner(?: prize)?|champion|1st place|優勝記念品|優勝賞|優勝紀念品)$", re.I)),
    ("finalist", re.compile(r"^(?:finalists?|finalist prize|ファイナリスト記念品)$", re.I)),
    ("top_cut", re.compile(r"^(?:top ?\d+(?: prizes?)?|best ?\d+(?: prizes?)?|2nd place|3rd place|上位記念品|ベスト\d+記念品|高名次紀念品|前\d+名紀念品)$", re.I)),
    ("judge", re.compile(r"^(?:judge(?: prizes?| compensation| pack)?|ジャッジ記念品)$", re.I)),
]
TIER_LOOKBACK = 8
_QTY = re.compile(
    r"\bx\s?\d+\b|\d+\s?(?:pcs|cards?)\s?(?:each|per)\s?(?:pack|set)|\(\d+ cards? per pack\)"
    r"|\d+パック\d+枚入り|\d+枚入り|全\d+種|參加者全員|参加者全員に|\d+張|每人", re.I)


def tier_of(lines, i):
    """The tier of the heading nearest above line i, within TIER_LOOKBACK lines, or None."""
    for j in range(i - 1, max(-1, i - 1 - TIER_LOOKBACK), -1):
        h = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", lines[j])).strip().rstrip(":：")
        for tier, pat in TIERS:
            if pat.match(h):
                return tier
    return None


def quantity_of(lines, i):
    """The quantity as the page prints it: on the naming line itself, else the line after it."""
    for ln in lines[i:i + 2]:
        if _QTY.search(ln):
            return ln
    return None


def claims(lines, names):
    """[{name, line, match, tier, quantity}] for every line that names a distribution.

    names: {distribution name: matcher(name)}. A line that names two
    distributions gives one hit for each; a distribution named on several lines
    gives one hit per line, and the run keeps one claim per page (the first).
    """
    out = []
    for i, ln in enumerate(lines):
        for name, pats in names.items():
            how = find(ln, pats)
            if how:
                out.append({"name": name, "line": ln, "match": how, "tier": tier_of(lines, i),
                            "quantity": quantity_of(lines, i)})
    return out


# --- card blocks: the contradiction check -----------------------------------

_CARDS = re.compile(r'data-type="component-opcg-cards"(.*?)(?=data-type="component-text"|<h[1-6]\b|$)', re.S)
_HEAD = re.compile(r"<h4[^>]*>(.*?)</h4>", re.S)
_NUM = re.compile(r"/((?:OP|ST|EB|PRB)\d{2}-\d{3}|P-\d{3})(?:_[^/\"]*)?\.(?:webp|png|jpe?g)")


def card_blocks(html):
    """[(pack heading, [card numbers])] for the CMS card blocks, in page order.

    Newer pages show a pack's cards as images named by card number under an h4
    with the pack's name. The card list is the authority on which pack a
    printing came from; a block that disagrees with it is a claim for review.
    """
    out, head, pos = [], "", 0
    for m in _CARDS.finditer(html):
        before = html[pos:m.start()]
        heads = _HEAD.findall(before)
        if heads:
            head = re.sub(r"\s+", " ", htmllib.unescape(_TAG.sub(" ", heads[-1]))).strip()
        pos = m.end()
        nums = []
        for n in _NUM.findall(unquote(m.group(1))):
            if n not in nums:
                nums.append(n)
        if nums and head:
            out.append((head, nums))
    return out


# --- fetch ----------------------------------------------------------------------

def root_url(site):
    return f"https://{HOSTS[site]}.onepiece-cardgame.com"


def index_urls(site, list_name, page):
    return f"{root_url(site)}/events/{list_name}.php?page={page}"


def links(base, html):
    """Every event, topic or news page a list page links, as absolute URLs, in order."""
    host = urlsplit(base).netloc
    out = []
    for raw in _A.findall(html):
        u = urljoin(base, htmllib.unescape(raw)).split("#")[0]
        p = urlsplit(u)
        if p.netloc != host or p.query:
            continue
        parts = p.path.split("/")
        if len(parts) < 3 or parts[1] not in SECTIONS:
            continue
        if "/".join(parts[2:]) in LIST_PAGES:
            continue
        if u not in out:
            out.append(u)
    return out


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


def needs_fetch(cache, hrefs, current):
    """The pages to read this run: never read, or still on the current events list."""
    return [h for h in hrefs if h not in cache or h in current]


def fetch_site(site, out_dir, cache, pause=1.5, attempts=3, get=_get):
    """Walk the event lists MIN_WALKS to MAX_WALKS times, then fetch the pages needs_fetch names.

    One walk reads events/ (the current list), events/list_end.php?page=N until a
    page lists nothing new, events/list_archive.php and topics/. Writes
    {out_dir}/{site}/events/: walk{W}-{name}.html, pages/{hash}.html and
    _log.json. log["walks"] says how many pages each walk listed and how many no
    earlier walk had.
    """
    out = Path(out_dir) / site / "events"
    (out / "pages").mkdir(parents=True, exist_ok=True)
    root = root_url(site)
    log = {"site": site, "index": [], "walks": [], "pages": [], "current": []}
    seen, current = [], set()

    def read_list(walk, name, url):
        f = out / f"walk{walk}-{name}.html"
        if log["index"]:
            time.sleep(pause)
        e = _fetch(url, f, attempts, pause, get)
        log["index"].append(dict(e, walk=walk, name=name, url=url))
        return links(url, f.read_text("utf-8", "replace")) if e.get("status") == 200 else None

    for walk in range(1, MAX_WALKS + 1):
        listed = []
        got = read_list(walk, "events", f"{root}/events/")
        if got is not None:
            listed += got
            current |= set(got)
        for lst in ("list_end", "list_archive"):
            for page in range(1, MAX_LIST_PAGES + 1):
                got = read_list(walk, f"{lst}-{page}", index_urls(site, lst, page))
                if got is None:
                    break
                fresh = [h for h in got if h not in listed]
                listed += fresh
                if not fresh:
                    break
        got = read_list(walk, "topics", f"{root}/topics/")
        if got is not None:
            listed += got
        new = [h for h in dict.fromkeys(listed) if h not in seen]
        seen += new
        log["walks"].append({"walk": walk, "pages": len(set(listed)), "new": len(new)})
        if walk >= MIN_WALKS and not new:
            break
    log["current"] = sorted(current)
    for href in needs_fetch(cache, seen, current):
        time.sleep(pause)
        log["pages"].append(dict(_fetch(href, out / "pages" / page_file(href), attempts, pause, get), href=href))
    (out / "_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), "utf-8")
    return log


def failures(log):
    """Every failed fetch in an events log, as text. Empty means the log is complete.

    A list page past the end of a pager can fail without harm; a failed first
    page of any list, or any failed event page, fails the run.
    """
    site = log["site"]
    out = []
    for e in log["index"]:
        if "error" in e and not re.search(r"-(?:[2-9]|\d{2,})$", e["name"]):
            out.append(f"{site}: event list {e['name']} (walk {e['walk']}): {e['error']}")
    out += [f"{site}: event page {e['href']}: {e['error']}" for e in log["pages"] if "error" in e]
    out += [f"{site}: event page {e['href']}: HTTP {e['status']}" for e in log["pages"]
            if e.get("status") not in (200, None)]
    if not any(e.get("status") == 200 for e in log["index"]):
        out.append(f"{site}: no event list fetched")
    return out


def read(site_dir, site):
    """({href: (html, fetched_at)} for the pages this page set fetched, the list fetched_at).

    Raises ValueError when the log is incomplete, so a run never reads half a set.
    """
    out = Path(site_dir) / "events"
    log = json.loads((out / "_log.json").read_text("utf-8"))
    bad = failures(log)
    if bad:
        raise ValueError("; ".join(bad))
    pages = {}
    for e in log["pages"]:
        pages[e["href"]] = ((out / "pages" / page_file(e["href"])).read_text("utf-8", "replace"), e["fetched_at"])
    index_at = min(e["fetched_at"] for e in log["index"])
    return pages, set(log["current"]), index_at
