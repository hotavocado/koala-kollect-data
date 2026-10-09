"""Split a site-local image id into base number and suffix.

Bandai sites use two suffix families, _pN and _rN (roberto 86390: 412 EN and
461 JP ids carry _rN). cn ids are the API's numeric ids and have no suffix;
tcgcsv ids (DON printings only) are a TCGplayer productId and the finish its
price row names, {productId}:{Normal|Foil}, and have no suffix either.
"""
import re

_BANDAI = re.compile(r"^(?P<base>[A-Z0-9]+-[A-Z0-9]+)(?:_(?P<family>[pr])(?P<n>[0-9]+))?$")
_CN = re.compile(r"^[0-9]+$")
_TCGCSV = re.compile(r"^[0-9]+:(?:Normal|Foil)$")


def parse_image_id(site: str, image_id: str) -> dict:
    """Return {base, suffix_family, suffix_n}; raise ValueError on an unknown shape."""
    if site == "cn":
        if not _CN.match(image_id):
            raise ValueError(f"{site} image id not numeric: {image_id!r}")
        return {"base": None, "suffix_family": None, "suffix_n": None}
    if site == "tcgcsv":
        if not _TCGCSV.match(image_id):
            raise ValueError(f"{site} image id not productId:finish: {image_id!r}")
        return {"base": None, "suffix_family": None, "suffix_n": None}
    m = _BANDAI.match(image_id)
    if not m:
        raise ValueError(f"unrecognised {site} image id: {image_id!r}")
    n = m.group("n")
    return {"base": m.group("base"), "suffix_family": m.group("family"), "suffix_n": int(n) if n else None}
