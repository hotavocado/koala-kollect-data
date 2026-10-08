"""Map cn API rows (cn.list_all, cn.detail, cn.products) to contract records.

The cn site is not a Bandai site: one JSON row per printing, keyed by a numeric
id, with the card facts behind a per-id detail call. Every vocabulary below was
measured on the 2026-10-08 list (4,927 rows) and detail sample. A value outside
it raises, as on the Bandai sites, except where a ruling says to keep it.

Variant (alyssa 87295). cn's card number does not say base or parallel on its
own, and neither does anything else on its own. Three signals are read, and any
one of them present makes the printing a parallel:
  - the number token: what cn writes after the base number, either `_NN`
    (P-084_01) or inline (OP01-001P, ST13-001LP, OP06-050-03, OP01-016P-R);
  - the image token: what Windo's image file name carries after the base
    number (OP06-050P.png, OP01-016SSP.png, OP01-016_06.png);
  - the name marker （异画） ("alt art").
cn never emits reprint: `_NN` is a parallel on one card and a reprint on
another (P-084_01 is jp P-084_p1, OP12-026_02 is jp OP12-026_r1), and no
signal tells them apart. jp stays the variant authority, through
printing_links. The tokens are kept verbatim on the printing.
"""
import re

from model import _int

SITE = "cn"
DETAIL_URL = "https://webadmin.windoent.com/front/op-public/cardList/cardlist/webInfo/{}"

# Base number, then whatever cn appends. Measured forms of the remainder on
# 4,927 rows: "" 4463, _NN 389, P 51, CP 11, -NN 6, SP 3, LP 2, P-R 1, P-SR 1.
_NUMBER = re.compile(r"^(?P<base>(?:[A-Z]+[0-9]*-[0-9]{3})|P-[0-9]{3})(?P<token>.*)$")
_TOKEN = re.compile(r"^(?:_[0-9]{2}|-[0-9]{2}|P|SP|LP|CP|P-R|P-SR)$")
MARKER = "（异画）"

# The rarity label as cn prints it -> the code the Bandai sites print. cn
# spells one rarity several ways (推广卡（P）, 宣传（P）, P on promos alone).
RARITY = {
    "领袖（L）": "L", "L": "L",
    "普通（C）": "C", "C": "C",
    "罕见（UC）": "UC", "罕见（U）": "UC", "UC": "UC",
    "稀有（R）": "R", "R": "R",
    "超稀有（SR）": "SR", "SR": "SR",
    "隐藏稀有（SEC）": "SEC", "隐藏稀有（sec）": "SEC", "SEC": "SEC",
    "推广卡（P）": "P", "宣传（P）": "P", "P": "P",
}
CATEGORY = {"领袖": "leader", "角色": "character", "事件": "event", "舞台": "stage"}
# Single rows where cn typed something else into the category field, keyed by
# cn id and checked against the card on the Bandai sites. Explicit, never a
# fuzzy match, so a new slip still stops the run.
CATEGORY_BY_ID = {
    2932: "character",  # EB01-003P: category field reads 稀有（R）; jp EB01-003 is a character
}
CN_COLORS = {"红": "red", "绿": "green", "蓝": "blue", "紫": "purple", "黑": "black", "黄": "yellow"}
ATTRIBUTES = {"斩": "slash", "打": "strike", "射": "ranged", "特": "special", "知": "wisdom"}
NO_VALUE = {None, "", "-"}

# Product kind from the product name's leading word (47 products, 2026-10-08).
# Longest prefix first: 特别补充包 and 豪华补充包 also start with 补充包's characters.
KIND_BY_PREFIX = [
    ("特别补充包", "extra"), ("豪华补充包", "premium"), ("补充包", "booster"),
    ("究极进阶卡组", "starter"), ("进阶卡组", "starter"), ("基本卡组", "starter"),
]
KIND_BY_NAME = {"宣传卡": "promo_bucket", "限定商品收录卡牌": "limited"}


def split_number(raw):
    """(base number, token) for a cn card number; the token is "" when cn appends nothing.

    A remainder outside the measured shapes is still split off and returned
    verbatim (alyssa 87274: keep the cn token where no variant matches), so a
    new cn spelling never mints a new card. Only a number with no recognisable
    base raises.
    """
    m = _NUMBER.match(raw or "")
    if not m:
        raise ValueError(f"cn card number with no base number: {raw!r}")
    return m.group("base"), m.group("token")


def known_token(token):
    return token == "" or bool(_TOKEN.match(token))


def image_token(image_url, base):
    """What the image file name carries after the base number, or None when the name does not contain it.

    Windo names files {digits}{number}{token}.{ext} (1765183OP06-050P.png) or
    with a bare hash (b153b99b...png). A hash says nothing, so None, never "".
    """
    name = image_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    at = name.rfind(base)
    if at < 0:
        return None
    return name[at + len(base):]


# Windo re-uploads leave copy marks in the file name: "(1)", "(2)", URL-encoded
# (EB02-046%281%29.png), sometimes stacked, and a trailing _D (P-026_D.png). They
# are re-uploads, not art (alyssa 87340): on 2026-10-08, 18 rows were the only
# cn id for their number with jp listing it base only, and every one wore one.
# Lowercase _d is not stripped: it reads as an art mark.
_COPY_MARK = re.compile(r"(?:%28[0-9]+%29|\([0-9]+\))")


def art_token(img_token):
    """The image token the variant rule reads: copy marks and a trailing _D removed.

    The printing keeps the raw image_token verbatim, so a re-rule costs nothing.
    None (an unread image) stays None.
    """
    if img_token is None:
        return None
    return _COPY_MARK.sub("", img_token).removesuffix("_D")


def has_marker(name):
    return MARKER in (name or "")


def variant(number_token, img_token, marker):
    """parallel when any signal is present, base when none is (alyssa 87295).

    img_token is the raw image token; it is read through art_token.
    """
    return "parallel" if (number_token or art_token(img_token) or marker) else "base"


def disagree(number_token, img_token, marker):
    """True when the signals that could be read do not all say the same thing.

    An image name with no number in it (img_token None) is unread, not absent.
    """
    said = [bool(number_token), bool(marker)]
    if img_token is not None:
        said.append(bool(art_token(img_token)))
    return len(set(said)) > 1


def source_text(info):
    """What the printing came in, as cn names it: `type` when it holds a name, else the product.

    `type` names the promo pack on promos (特别宣传包) and holds a bare
    number on some booster rows (OP02-067, id 1925: "2"), which names nothing.
    """
    t = info.get("type")
    if t and not str(t).isdigit():
        return t
    return info.get("cardOfferType") or ""


def rarity(raw):
    if raw not in RARITY:
        raise ValueError(f"unknown cn rarity {raw!r}")
    return RARITY[raw]


def block_icon(raw):
    """cn's `subscript` is the block icon: an integer, or null where none is printed."""
    if raw is None:
        return None
    if isinstance(raw, int) and raw >= 0:
        return raw
    raise ValueError(f"unknown cn block icon {raw!r}")


def product_kind(name):
    if name in KIND_BY_NAME:
        return KIND_BY_NAME[name]
    for prefix, kind in KIND_BY_PREFIX:
        if name.startswith(prefix):
            return kind
    return "other"


def product_fields(product):
    """A cn product record's fields, keyed on the API's numeric product id."""
    rec = {"series_id": str(product["id"])}
    m = re.findall(r"【([A-Za-z0-9-]+)】", product["name"])
    if m:
        rec["code"] = m[-1]
    rec.update(name=product["name"], kind=product_kind(product["name"]))
    return rec


def _split(raw, table, what):
    out = []
    for part in raw:
        if part in NO_VALUE:
            continue
        if part not in table:
            raise ValueError(f"unknown cn {what} {part!r} in {raw!r}")
        if table[part] not in out:
            out.append(table[part])
    return out


def _num(raw, what):
    return None if raw in NO_VALUE else _int(str(raw), what)


def counter(raw):
    """cn prints a counter as 1000 or as 反击+1000 ("counter +1000")."""
    if raw in NO_VALUE:
        return None
    return _int(str(raw).removeprefix("反击+"), "counter")


def observation_fields(info):
    """The card facts one cn detail states, in schema order, absent values omitted.

    cardLife is life on a leader and cost on everything else; cardAttack is
    the counter. The name drops the alt-art marker, which is a printing fact.
    """
    if info.get("id") in CATEGORY_BY_ID:
        cat = CATEGORY_BY_ID[info["id"]]
    elif info.get("cardType") in CATEGORY:
        cat = CATEGORY[info["cardType"]]
    else:
        raise ValueError(f"unknown cn category {info.get('cardType')!r}")
    rec = {"name": (info.get("cardName") or "").replace(MARKER, "").strip(), "category": cat,
           "colors": _split((info.get("cardColor") or "").split("/"), CN_COLORS, "colour")}
    rec["life" if cat == "leader" else "cost"] = _num(info.get("cardLife"), "life/cost")
    rec["power"] = _num(info.get("cardPower"), "power")
    rec["counter"] = counter(info.get("cardAttack"))
    # Events and stages have no attribute (0 of 459 on the four Bandai sites,
    # 2026-10-08), and cn's field on them is filler: "-", or the colour again
    # (OP02-067, id 1925: ["蓝"]). Read it on leaders and characters only.
    raw_attrs = (info.get("cardAttribute") or []) if cat in ("leader", "character") else []
    rec["attributes"] = _split(raw_attrs, ATTRIBUTES, "attribute")
    # Types are split on "/", and on "," where cn typed one (6 of the first 742
    # details: 纯毛族,和之国/赤鞘九人男). A space is part of a name (GERMA 66).
    rec["types"] = [t for t in re.split(r"[/,]", info.get("cardFeatures") or "") if t]
    rec["effect"] = None if info.get("cardTextDesc") in NO_VALUE else info["cardTextDesc"]
    rec["trigger"] = None if info.get("cardTrigger") in NO_VALUE else info["cardTrigger"]
    if not rec["name"]:
        raise ValueError("blank cn card name")
    return {k: v for k, v in rec.items() if v is not None}
