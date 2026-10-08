"""Split a site-local image id into base number and suffix.

Bandai sites use two suffix families, _pN and _rN (roberto 86390: 412 EN and
461 JP ids carry _rN). cn ids are the API's numeric ids and have no suffix.
"""
import re

_BANDAI = re.compile(r"^(?P<base>[A-Z0-9]+-[A-Z0-9]+)(?:_(?P<family>[pr])(?P<n>[0-9]+))?$")
_CN = re.compile(r"^[0-9]+$")


def parse_image_id(site: str, image_id: str) -> dict:
    """Return {base, suffix_family, suffix_n}; raise ValueError on an unknown shape."""
    if site == "cn":
        if not _CN.match(image_id):
            raise ValueError(f"cn image id not numeric: {image_id!r}")
        return {"base": None, "suffix_family": None, "suffix_n": None}
    m = _BANDAI.match(image_id)
    if not m:
        raise ValueError(f"unrecognised {site} image id: {image_id!r}")
    n = m.group("n")
    return {"base": m.group("base"), "suffix_family": m.group("family"), "suffix_n": int(n) if n else None}
