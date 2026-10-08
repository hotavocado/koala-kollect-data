"""Map raw Bandai blocks (bandai.parse_page) to contract records.

Every vocabulary below was measured on the full 2026-10-08 fetch of all four
sites. A value outside it raises: an unknown colour or label is a layout or
translation change, and the run must stop rather than guess.
"""
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from image_id import parse_image_id  # noqa: E402

HOSTS = {"en": "en", "asia-en": "asia-en", "jp": "www", "tc": "asia-tc"}
LANG = {"en": "en", "asia-en": "en", "jp": "ja", "tc": "zh-Hant", "cn": "zh-Hans"}

COLORS = {
    "Red": "red", "Green": "green", "Blue": "blue", "Purple": "purple", "Black": "black", "Yellow": "yellow",
    "赤": "red", "緑": "green", "青": "blue", "紫": "purple", "黒": "black", "黄": "yellow",
    # tc prints yellow both as 黃 and as 黄 (17 blocks on 2026-10-08).
    "紅": "red", "綠": "green", "藍": "blue", "黑": "black", "黃": "yellow",
}
ATTRIBUTES = {
    "Slash": "slash", "Strike": "strike", "Ranged": "ranged", "Special": "special", "Wisdom": "wisdom",
    "斬": "slash", "打": "strike", "射": "ranged", "特": "special", "知": "wisdom",
    # OP13-079 Imu prints "?" where an attribute goes: half-width on en and
    # asia-en, full-width on jp and tc. Both are stored half-width (CONTRACT.md).
    "?": "?", "？": "?",
}
LIFE_LABELS = {"Life", "ライフ", "生命值"}
COST_LABELS = {"Cost", "コスト", "費用"}
CATEGORIES = {"LEADER": "leader", "CHARACTER": "character", "EVENT": "event", "STAGE": "stage"}
# Bandai's jp page prints P-160's category as キャラクタークター (2026-10-08), a
# doubled-up typo of キャラクター. Mapped explicitly, never by fuzzy match, so
# a new oddity still stops the run.
CATEGORY_ALIASES = {"キャラクタークター": "character"}
# Series ids encode the product kind in their 4th digit on all four sites
# (measured 2026-10-08: en 569117 booster, jp 550901 promos, tc 554801 limited).
KIND_BY_DIGIT = {"0": "starter", "1": "booster", "2": "extra", "3": "premium",
                 "7": "family", "8": "limited", "9": "promo_bucket"}
VARIANT_BY_FAMILY = {None: "base", "p": "parallel", "r": "reprint"}

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def mint(kind, natural):
    """A key derived from the natural key, so a re-run (or a lost lookup) mints the same one."""
    n = int(hashlib.sha256(f"{kind}|{natural}".encode()).hexdigest(), 16)
    out = ""
    for _ in range(12):
        n, r = divmod(n, 36)
        out += _B36[r]
    return f"{kind}_{out}"


def category(raw):
    if raw in CATEGORIES:
        return CATEGORIES[raw]
    if raw in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[raw]
    raise ValueError(f"unknown category {raw!r}")


def _split(raw, table, what, skip=frozenset()):
    out = []
    for part in (raw or "").split("/"):
        if not part or part in skip:
            continue
        if part not in table:
            raise ValueError(f"unknown {what} {part!r} in {raw!r}")
        if table[part] not in out:
            out.append(table[part])
    return out


def _int(raw, what):
    if raw is None:
        return None
    if not raw.isdigit():
        raise ValueError(f"non-numeric {what} {raw!r}")
    return int(raw)


def observation_fields(site, b):
    """The card facts one block states, in schema order, absent values omitted."""
    rec = {"name": b["name"], "category": category(b["category"]),
           "colors": _split(b["color"], COLORS, "colour")}
    if b["cost_label"] in LIFE_LABELS:
        rec["life"] = _int(b["cost"], "life")
    elif b["cost_label"] in COST_LABELS or b["cost_label"] is None:
        rec["cost"] = _int(b["cost"], "cost")
    else:
        raise ValueError(f"unknown cost label {b['cost_label']!r}")
    rec["power"] = _int(b["power"], "power")
    rec["counter"] = _int(b["counter"], "counter")
    # Alt texts already join two attributes with "/" ("Slash/Special").
    rec["attributes"] = _split("/".join(b["attributes"]), ATTRIBUTES, "attribute")
    # No block_icon: it is a printing fact (printing_block_icon below), because
    # parallels of one number and sites differ on it.
    rec["types"] = [t for t in (b["types"] or "").split("/") if t]
    rec["effect"] = b["effect"]
    rec["trigger"] = b["trigger"]
    return {k: v for k, v in rec.items() if v is not None}


def printing_block_icon(raw):
    """As printed on this printing: an integer, "X" (never rotates out), or None where none is printed."""
    if raw is None:
        return None
    if raw == "X":
        return "X"
    return _int(raw, "block icon")


def observation_hash(fields):
    """First 16 hex of sha256 over the parsed facts, never over page HTML."""
    blob = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def image_url(site, page_url, src):
    """Absolute, with the site-wide deploy stamp (?260929) and any fragment removed."""
    parts = urlsplit(urljoin(page_url, html.unescape(src)))
    if parts.scheme != "https":
        raise ValueError(f"image url not https: {src!r}")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def page_url(site, series_id):
    return f"https://{HOSTS[site]}.onepiece-cardgame.com/cardlist/?series={series_id}"


def product_fields(site, series_id, label):
    kind = KIND_BY_DIGIT.get(series_id[3:4])
    if kind is None:
        raise ValueError(f"{site}:{series_id}: no product kind for series digit {series_id[3:4]!r}")
    rec = {"series_id": series_id}
    m = re.findall(r"[【\[]([A-Za-z0-9-]+)[】\]]", label)
    if m:
        rec["code"] = m[-1]
    rec.update(name=label, kind=kind, product_url=page_url(site, series_id))
    return rec


def locator_fields(site, image_id):
    p = parse_image_id(site, image_id)
    rec = {"image_id": image_id}
    if p["suffix_family"]:
        rec.update(suffix_family=p["suffix_family"], suffix_n=p["suffix_n"])
    return rec, p


def sibling_order(image_id, site):
    """The block a card's observation is read from: the unsuffixed printing, then _p before _r, lowest n.

    Sibling printings of one number disagree on some facts on every site
    (asia-en and tc append "(Parallel)" to parallel names; block icons differ on
    reprints), so the observation must come from one deterministic block.
    """
    p = parse_image_id(site, image_id)
    return (p["suffix_family"] is not None, p["suffix_family"] or "", p["suffix_n"] or 0)
