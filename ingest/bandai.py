"""Parse one official Bandai card-list series page into raw card blocks.

The four Bandai sites (en, asia-en, jp, tc) serve the same markup: one
<dl class="modalCol" id="{image_id}"> per printing. Fields are read by their
div class, never by the visible label, because labels are translated. Values
are kept as the page prints them; mapping to schema vocabularies happens later.
"""
import html
import re

_BLOCK = re.compile(r'<dl class="modalCol" id="(?P<id>[^"]+)">(?P<body>.*?)</dl>', re.S)
_INFO = re.compile(
    r'<div class="infoCol">\s*<span>(?P<number>[^<]*)</span>\s*\|\s*<span>(?P<rarity>[^<]*)</span>'
    r'\s*\|\s*<span>(?P<category>[^<]*)</span>',
    re.S,
)
_NAME = re.compile(r'<div class="cardName">(.*?)</div>', re.S)
_IMG = re.compile(r'<div class="frontCol">\s*<img[^>]*?(?:data-src|src)="(?P<src>[^"]*?/images/cardlist/card/[^"]+)"', re.S)
_ATTR_BLOCK = re.compile(r'<div class="attribute">(.*?)</div>', re.S)
_ATTR_ALT = re.compile(r'<img[^>]*alt="([^"]*)"')
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# Independent count: image ids in card-image URLs, not the block regex above.
_IMG_COUNT = re.compile(r'(?:data-src|src)="[^"]*/images/cardlist/card/([^"./?]+)\.png')


def _text(fragment):
    fragment = re.sub(r"<br[^>]*>", "\n", fragment)
    return _WS.sub(" ", html.unescape(_TAG.sub("", fragment))).strip()


def _field(body, cls):
    """Return (label, value) for <div class="{cls}"><h3>label</h3>value</div>, or None."""
    m = re.search(rf'<div class="{cls}"><h3>(.*?)</h3>(.*?)</div>', body, re.S)
    if not m:
        return None
    return _text(m.group(1)), _text(m.group(2))


def _dash(v):
    return None if v in (None, "", "-") else v


def parse_page(page_html):
    """Return a list of raw block dicts, in page order."""
    out = []
    for m in _BLOCK.finditer(page_html):
        body = m.group("body")
        info = _INFO.search(body)
        name = _NAME.search(body)
        img = _IMG.search(body)
        attr = _ATTR_BLOCK.search(body)
        cost = _field(body, "cost")
        rec = {
            "image_id": m.group("id"),
            "number": _text(info.group("number")) if info else None,
            "rarity": _text(info.group("rarity")) if info else None,
            "category": _text(info.group("category")) if info else None,
            "name": _text(name.group(1)) if name else None,
            "image_src": img.group("src") if img else None,
            # The "cost" div carries Life on leaders; keep its label so the
            # mapping step can tell which one it is.
            "cost_label": cost[0] if cost else None,
            "cost": _dash(cost[1]) if cost else None,
            "attributes": [html.unescape(a) for a in _ATTR_ALT.findall(attr.group(1))] if attr else [],
            "power": _dash((_field(body, "power") or (None, None))[1]),
            "counter": _dash((_field(body, "counter") or (None, None))[1]),
            "color": _dash((_field(body, "color") or (None, None))[1]),
            "block_icon": _dash((_field(body, "block") or (None, None))[1]),
            "types": _dash((_field(body, "feature") or (None, None))[1]),
            "effect": _dash((_field(body, "text") or (None, None))[1]),
            "trigger": _dash((_field(body, "trigger") or (None, None))[1]),
            # Empty string, not None, when the site prints no provenance
            # (CONTRACT.md; the run counts these in blocks_without_source_text).
            "source_text": (_field(body, "getInfo") or (None, ""))[1],
        }
        out.append(rec)
    return out


def independent_count(page_html):
    """Distinct image ids referenced by card-image URLs. Shares no regex with parse_page."""
    return len(set(_IMG_COUNT.findall(page_html)))


def series_options(page_html):
    """Series ids and labels from the series dropdown, in page order."""
    sel = re.search(r'<select[^>]*name="series"[^>]*>(.*?)</select>', page_html, re.S)
    if not sel:
        return []
    opts = re.findall(r'<option value="(\d+)"[^>]*>(.*?)</option>', sel.group(1), re.S)
    seen, out = set(), []
    for sid, label in opts:
        if sid not in seen:
            seen.add(sid)
            out.append((sid, _text(label)))
    return out
