"""DON printings from tcgcsv.com, TCGplayer's catalogue mirror (CONTRACT.md, DON).

No official card list carries DON cards, so they mint from here. Read-only, no
login. Category 68 is One Piece: /groups lists the sets, /{groupId}/products the
products of one set, /{groupId}/prices one price row per product and finish.

What is measured (2026-10-09, 87 groups, 7,717 products):
- A DON is a product whose extendedData CardType is DON!! (240). Rarity is not
  the key: 17 DON promos carry Rarity PR. The 18 sealed Special DON!! Card Pack
  products carry no card data at all, so they are not DON cards.
- Normal and gold are separate products. A gold's name is its normal's name
  plus " (Gold)", in the same group (73 of 74). DON!! Card (Gold) in the promo
  group (482236) has no such normal: it sits in a colour series (Red, Yellow,
  Blue, ...), so by this rule it is a normal product, its own card.
- The finish is the price row's subTypeName, Normal or Foil, and one product can
  carry both (69 do). They are two physical cards (alyssa 87481), so the locator
  always carries it: tcgcsv:{productId}:{subType}.
- No DON carries a Number.
- imageCount is TCGplayer's own count of a product's images. Where it is 0 the
  CDN answers 403 (3 of 240, exactly those 3), so such a printing carries no
  image_url until it turns positive (upper 87556, alyssa 87559).

Release Event stamps (CONTRACT.md, Release Event stamps) come from the groups
in RE_GROUPS only: a product there with an extendedData Number is a stamped
copy of the set card with that Number. The group's sealed pack has no Number
and is not read. Every stamp measured prices Normal only (554 of 554), so a
stamp priced any other finish stops the run.
"""
import json
import re
import time
import urllib.request
from pathlib import Path

BASE = "https://tcgcsv.com/tcgplayer/68"
UA = "Mozilla/5.0 (compatible; koala-kollect-ingest/0.1; +https://github.com/hotavocado/koala-kollect-data)"
DON_TYPE = "DON!!"
GOLD_SUFFIX = " (Gold)"
# The finishes measured on DON price rows. Anything else stops the run.
FINISH = {"Normal": "normal", "Foil": "foil"}
# The Release Event groups whose stamps mint, by groupId, so a group joins the
# data by a change here and never by TCGplayer adding one (upper 88114: OP17 RE
# first; 88164: the other six). OP18 RE (24834) lists no stamp yet, and an
# admitted group with no stamp stops the run, so it waits for its own change.
RE_GROUPS = {24068, 24242, 24406, 24579, 24638, 24677, 24775}
# Bandai's own image of a DON, by TCGplayer productId, written as the printing's
# provenance_url on every finish of that product (CONTRACT.md, DON). Only
# measured matches: mean absolute pixel difference 4-6/255 against TCGplayer's
# {productId}_in_1000x1000.jpg, 2026-10-10. A product joins by a change here.
PROVENANCE = {
    677559: "https://en.onepiece-cardgame.com/renewal/images/products/boosters/eb03/EB03_DON_p1.webp",
    677560: "https://en.onepiece-cardgame.com/renewal/images/products/boosters/eb03/EB03_DON.webp",
    683969: "https://en.onepiece-cardgame.com/images/topics/028/01.png",
}


def _get(path, timeout=60):
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.load(r)
    if body.get("success") is not True or body.get("errors"):
        raise RuntimeError(f"tcgcsv {path}: success={body.get('success')} errors={body.get('errors')}")
    return body["results"]


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
    tmp.replace(path)


def is_don(product):
    return any(e.get("name") == "CardType" and e.get("value") == DON_TYPE for e in product.get("extendedData", []))


def fetch_pageset(tcg_dir, pause=0.3, attempts=3):
    """Write the tcgcsv page set. Raises when any request fails: the set is complete or absent.

    tcg_dir/_groups.json            {"fetched_at", "groups"}
    tcg_dir/products/{groupId}.json {"fetched_at", "products"}  every group
    tcg_dir/prices/{groupId}.json   {"fetched_at", "prices"}    groups that carry a DON, and RE_GROUPS
    """
    tcg_dir = Path(tcg_dir)

    def get(path):
        for i in range(attempts):
            try:
                return _get(path)
            except Exception:  # noqa: BLE001  retried, then raised
                if i == attempts - 1:
                    raise
                time.sleep(2)

    t = _utc()
    groups = get("groups")
    for g in groups:
        gid = g["groupId"]
        ft = _utc()
        prods = get(f"{gid}/products")
        _write(tcg_dir / "products" / f"{gid}.json", {"fetched_at": ft, "products": prods})
        if gid in RE_GROUPS or any(is_don(p) for p in prods):
            ft = _utc()
            _write(tcg_dir / "prices" / f"{gid}.json", {"fetched_at": ft, "prices": get(f"{gid}/prices")})
        time.sleep(pause)
    # Last, so a set with _groups.json is a set whose every group was written.
    _write(tcg_dir / "_groups.json", {"fetched_at": t, "groups": groups})
    return len(groups)


def variant(gold, sub_type):
    """THE finish rule, in one place, so a ruling on the finish is a change here only.

    A gold product is variant gold, and gold is always foil: a gold priced
    Normal has never been seen (74 of 74 Foil only, 2026-10-09) and stops the
    run. A normal product's printing is its finish: normal or foil.
    """
    if sub_type not in FINISH:
        raise ValueError(f"finish {sub_type!r} is not one of {sorted(FINISH)}")
    if gold:
        if sub_type != "Foil":
            raise ValueError(f"a gold DON priced {sub_type!r}; gold is always foil")
        return "gold"
    return FINISH[sub_type]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def design(abbreviation, name):
    """{group abbreviation}:{name slug}, e.g. OP09:don-card-alternate-art (ST-01 PRE becomes ST-01-PRE)."""
    return f"{re.sub(r'[^A-Za-z0-9-]+', '-', abbreviation).strip('-')}:{slug(name)}"


def image_url(product_id):
    return f"https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_1000x1000.jpg"


def plan(groups, products, prices):
    """(printings, unpriced, don_count) from one page set, before any key is looked up.

    groups is the /groups list; products and prices map groupId to their lists.
    Each printing is a dict: product_id, sub_type, variant, name, card_product_id
    (the normal product whose card it is), design (that card's don_design),
    group_id and has_image (TCGplayer's imageCount above 0). unpriced is the DON products with no price row: they have no
    finish and so no key, and never reach variant() (upper 87490). They mint
    the day a price row appears.
    """
    out, unpriced, don_count = [], [], 0
    for g in sorted(groups, key=lambda g: g["groupId"]):
        gid = g["groupId"]
        dons = [p for p in products.get(gid, []) if is_don(p)]
        if not dons:
            continue
        if gid not in prices:
            raise ValueError(f"group {gid} carries {len(dons)} DON but no price file")
        don_count += len(dons)
        normals = {}
        for p in dons:
            if not p["name"].endswith(GOLD_SUFFIX):
                if p["name"] in normals:
                    raise ValueError(f"group {gid}: two DON products named {p['name']!r}")
                normals[p["name"]] = p
        finishes = {}
        for r in prices[gid]:
            finishes.setdefault(r["productId"], set()).add(r["subTypeName"])
        for p in sorted(dons, key=lambda p: p["productId"]):
            partner = None
            if p["name"].endswith(GOLD_SUFFIX):
                partner = normals.get(p["name"][: -len(GOLD_SUFFIX)])
            card_product = partner or p
            subs = finishes.get(p["productId"])
            if not subs:
                unpriced.append(p["productId"])
                continue
            for sub in sorted(subs):
                out.append({"product_id": p["productId"], "sub_type": sub,
                            "variant": variant(partner is not None, sub), "name": p["name"],
                            "card_product_id": card_product["productId"],
                            "design": design(g["abbreviation"], card_product["name"]), "group_id": gid,
                            "has_image": bool(p.get("imageCount"))})
    return out, unpriced, don_count


def ext(product, name):
    """One extendedData value, or None."""
    return next((e.get("value") for e in product.get("extendedData", []) if e.get("name") == name), None)


def is_stamp(product):
    """A card product in a Release Event group: it has a Number (DON never do; the sealed pack has none)."""
    return bool(ext(product, "Number")) and not is_don(product)


def stamp_variant(sub_type):
    """THE stamp finish rule. Every stamp measured prices Normal only (554 of 554, 2026-10-09).

    A stamp priced Foil would be a second physical card, and that is a ruling,
    not a guess, so it stops the run (CONTRACT.md, Release Event stamps).
    """
    if sub_type != "Normal":
        raise ValueError(f"a Release Event stamp priced {sub_type!r}; every stamp so far is Normal only")
    return "stamped"


def plan_stamps(groups, products, prices):
    """(stamps, unpriced, stamp_count) for the RE_GROUPS in one page set.

    Each stamp is a dict: product_id, sub_type, variant, number (the set card
    it stamps, verbatim), rarity (verbatim), name, group_id, group_name and
    has_image. unpriced is the stamps with no price row, refused as a DON's
    are: no finish, no key. An allowlisted group missing from /groups, or
    holding no stamp, stops the run: that is TCGplayer's layout moving, not a
    quiet day.
    """
    by_id = {g["groupId"]: g for g in groups}
    out, unpriced, count = [], [], 0
    for gid in sorted(RE_GROUPS):
        g = by_id.get(gid)
        if g is None:
            raise ValueError(f"Release Event group {gid} is not in /groups")
        stamps = [p for p in products.get(gid, []) if is_stamp(p)]
        if not stamps:
            raise ValueError(f"Release Event group {gid} ({g['name']}) carries no stamped card")
        if gid not in prices:
            raise ValueError(f"Release Event group {gid} carries {len(stamps)} stamps but no price file")
        count += len(stamps)
        finishes = {}
        for r in prices[gid]:
            finishes.setdefault(r["productId"], set()).add(r["subTypeName"])
        for p in sorted(stamps, key=lambda p: p["productId"]):
            rarity = ext(p, "Rarity")
            if not rarity:
                raise ValueError(f"stamp {p['productId']} ({ext(p, 'Number')}) has no Rarity")
            subs = finishes.get(p["productId"])
            if not subs:
                unpriced.append(p["productId"])
                continue
            for sub in sorted(subs):
                out.append({"product_id": p["productId"], "sub_type": sub, "variant": stamp_variant(sub),
                            "number": ext(p, "Number"), "rarity": rarity, "name": p["name"], "group_id": gid,
                            "group_name": g["name"], "has_image": bool(p.get("imageCount"))})
    return out, unpriced, count


def read_pageset(tcg_dir):
    """(fetched_at, groups, products, prices, product_times) from fetch_pageset's output."""
    tcg_dir = Path(tcg_dir)
    head = json.loads((tcg_dir / "_groups.json").read_text("utf-8"))
    products, prices, times = {}, {}, {}
    for g in head["groups"]:
        gid = g["groupId"]
        path = tcg_dir / "products" / f"{gid}.json"
        if not path.exists():
            raise ValueError(f"group {gid} listed but its products were not fetched")
        d = json.loads(path.read_text("utf-8"))
        products[gid], times[gid] = d["products"], d["fetched_at"]
        pp = tcg_dir / "prices" / f"{gid}.json"
        if pp.exists():
            prices[gid] = json.loads(pp.read_text("utf-8"))["prices"]
    return head["fetched_at"], head["groups"], products, prices, times
