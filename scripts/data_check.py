"""Check data/ and manifest.json the way the app sync reads them, plus the contract.

Returns a list of errors; empty means the data is clean. Checks: every manifest
path is one the app sync accepts; every data file is listed in the manifest and
every listed file exists with its sha256 and row count; LF, trailing newline,
lines sorted by unique key, object keys in schema order; every record valid for
its type; no HTML tag or entity left in any string, however nested; every
field the schema types as a reference resolves to a row of that type; no
retired printing is also a live one; a stamped printing's locator is Normal;
a stamped printing has a tcgcsv claim, and every tcgcsv claim is a stamp's,
corroborated and undated; every DON card has one don_set row that agrees
with its card, printings and the en product codes; and every run record under
runs/ is a valid ingest_run, none of a retired site started after its retirement.
"""
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

TYPE_OF_DIR = {"cards": "card", "card_observations": "card_observation", "printings": "printing",
               "printing_locators": "printing_locator", "products": "product",
               "printing_products": "printing_product", "distributions": "distribution",
               "printing_distributions": "printing_distribution", "printing_links": "printing_link",
               "retired_printings": "retired_printing", "don_sets": "don_set"}

# The app sync's own path rule (koala-kollect, alyssa/2026-10-08-data-sync).
SYNC_PATH = re.compile(r"^data/(?:[a-z_]+/)?[a-z_-]+\.jsonl$")

# Lowercase tag names only: effect text carries attribute markers like <Slash>.
MARKUP = re.compile(r"</?[a-z][a-z0-9]*\b[^>]*>|&(?:[a-z]+|#[0-9]+|#x[0-9a-f]+);")


def check_data(root, schema):
    root = Path(root)
    errors = []
    mpath = root / "manifest.json"
    on_disk = {p.relative_to(root).as_posix() for p in (root / "data").rglob("*.jsonl")} if (root / "data").exists() else set()
    if not mpath.exists():
        return [f"data files with no manifest.json: {sorted(on_disk)[:3]}"] if on_disk else []
    manifest = json.loads(mpath.read_text("utf-8"))
    mv = Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/manifest"})
    errors += [f"manifest.json: {e.message}" for e in mv.iter_errors(manifest)]
    files = manifest.get("files", {})
    for path in sorted(on_disk - files.keys()):
        errors.append(f"{path}: on disk but not in manifest.json")
    keys = {t: set() for t in TYPE_OF_DIR.values()}
    records = []
    for path, entry in sorted(files.items()):
        if not SYNC_PATH.match(path):
            errors.append(f"{path}: not a data path the app sync accepts")
            continue
        p = root / path
        if not p.exists():
            errors.append(f"{path}: in manifest.json but missing")
            continue
        raw = p.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            errors.append(f"{path}: sha256 mismatch")
        text = raw.decode("utf-8")
        if (text and not text.endswith("\n")) or "\r" in text:
            errors.append(f"{path}: not LF with a trailing newline")
        lines = text.splitlines()
        if len(lines) != entry.get("rows"):
            errors.append(f"{path}: row count mismatch, manifest {entry.get('rows')}, file {len(lines)}")
        rtype = TYPE_OF_DIR.get(path.split("/")[1].removesuffix(".jsonl"))
        if rtype != entry.get("type"):
            errors.append(f"{path}: manifest type {entry.get('type')!r}, layout says {rtype!r}")
            continue
        v = Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{rtype}"})
        order = list(schema["$defs"][rtype]["properties"])
        prev = None
        for i, ln in enumerate(lines, 1):
            rec = json.loads(ln)
            k = rec.get("key")
            if prev is not None and not k > prev:
                errors.append(f"{path}:{i}: key {k!r} not sorted after {prev!r} (or repeated)")
            prev = k
            if list(rec) != [f for f in order if f in rec]:
                errors.append(f"{path}:{i}: object keys not in schema order")
            for e in v.iter_errors(rec):
                errors.append(f"{path}:{i}: {e.message}")
            for field, val in rec.items():
                for text in strings(val):
                    if MARKUP.search(text):
                        errors.append(f"{path}:{i}: markup in {field}: {MARKUP.search(text).group(0)!r}")
            if k in keys[rtype]:
                errors.append(f"{path}:{i}: key {k!r} repeated across files")
            keys[rtype].add(k)
            records.append((path, i, rtype, rec))
    for path, i, rtype, rec in records:
        for field, target in references(schema, rtype).items():
            if field in rec and rec[field] not in keys[target]:
                errors.append(f"{path}:{i}: {field} {rec[field]} has no {target} row")
    errors += retired_errors(keys["printing"], [rec for _, _, rtype, rec in records if rtype == "retired_printing"])
    runs = [(p.relative_to(root).as_posix(), json.loads(p.read_text("utf-8"))) for p in sorted((root / "runs").rglob("*.json"))]
    errors += run_errors(runs, schema, [rec for _, _, rtype, rec in records if rtype == "retired_printing"])
    errors += stamped_finish_errors([rec for _, _, rtype, rec in records if rtype == "printing"],
                                    [rec for _, _, rtype, rec in records if rtype == "printing_locator"])
    errors += stamped_claim_errors([rec for _, _, rtype, rec in records if rtype == "printing"],
                                   [rec for _, _, rtype, rec in records if rtype == "distribution"],
                                   [rec for _, _, rtype, rec in records if rtype == "printing_distribution"])
    by_type = {t: [rec for _, _, rtype, rec in records if rtype == t] for t in ("card", "printing", "product", "don_set")}
    errors += don_set_errors(by_type["card"], by_type["printing"], by_type["product"], by_type["don_set"])
    return errors


def retired_errors(printing_keys, retired):
    """A retired printing is gone: its key may not also be a live printing, and its key is its printing_key.

    The app sync applies the file as a delete, so a key that is both would be
    written and deleted by one sync (alyssa 87774 refuses the same thing on her side).
    """
    out = []
    for rec in retired:
        if rec.get("key") != rec.get("printing_key"):
            out.append(f"retired_printing {rec.get('key')}: key differs from printing_key {rec.get('printing_key')}")
        if rec.get("printing_key") in printing_keys:
            out.append(f"retired_printing {rec.get('printing_key')}: also a live printing")
    return out


def run_errors(runs, schema, retired):
    """Each (path, record) under runs/ is a valid ingest_run, and no retired site ran after its retirement.

    A site's retirement is the earliest retired_at of its site_retired rows; the
    site is the prefix of the row's own locator. Its earlier runs are the audit
    record and stay (CONTRACT.md, Retired sites).
    """
    v = Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/ingest_run"})
    since = {}
    for rec in retired:
        if rec.get("reason") == "site_retired" and rec.get("source_ids"):
            site = rec["source_ids"][0].split(":")[0]
            since[site] = min(since.get(site, rec["retired_at"]), rec["retired_at"])
    out = []
    for path, rec in runs:
        out += [f"{path}: {e.message}" for e in v.iter_errors(rec)]
        site = rec.get("site")
        if site in since and rec.get("started_at", "") >= since[site]:
            out.append(f"{path}: a {site} run started {rec.get('started_at')}, after {site} was retired at {since[site]}")
    return out


def stamped_finish_errors(printings, locators):
    """A stamped printing has a locator, and every locator it has ends :Normal.

    Every Release Event stamp prices Normal only (554 of 554, 2026-10-09) and a
    stamp priced Foil stops the walker (CONTRACT, Release Event stamps). The
    locator is a stamp's natural key, so one with none is refused too. The
    schema cannot see either, because a locator does not carry its printing's
    variant (codex, PR 17, rounds 1 and 2).
    """
    stamped = {rec["key"] for rec in printings if rec.get("variant") == "stamped"}
    out, located = [], set()
    for rec in locators:
        if rec.get("printing_key") not in stamped:
            continue
        located.add(rec["printing_key"])
        if not rec.get("image_id", "").endswith(":Normal"):
            out.append(f"printing_locator {rec.get('key')}: stamped printing {rec['printing_key']} on a finish other than Normal")
    out += [f"printing {k}: stamped with no locator" for k in sorted(stamped - located)]
    return out


def stamped_claim_errors(printings, distributions, claims):
    """A stamped printing has at least one tcgcsv claim, and every tcgcsv claim is a stamp's, as the contract states it.

    A tcgcsv claim names a stamped printing and a tcgcsv distribution, is
    corroborated (a third-party catalogue's own grouping, never authoritative),
    and carries no starts_on or ends_on (tcgcsv is never a date source). The
    count per printing is left open: several claims per printing are normal
    (CONTRACT, Release Event stamps; upper 88125, off codex round 3 on PR 17).
    """
    stamped = {rec["key"] for rec in printings if rec.get("variant") == "stamped"}
    tcg_dists = {rec["key"] for rec in distributions if rec.get("site") == "tcgcsv"}
    out, claimed = [], set()
    for rec in claims:
        if rec.get("source") != "tcgcsv":
            continue
        k, prt = rec.get("key"), rec.get("printing_key")
        if prt in stamped:
            claimed.add(prt)
        else:
            out.append(f"printing_distribution {k}: tcgcsv claim on {prt}, which is not a stamped printing")
        if rec.get("distribution_key") not in tcg_dists:
            out.append(f"printing_distribution {k}: tcgcsv claim on {rec.get('distribution_key')}, which is not a tcgcsv distribution")
        if rec.get("confidence") != "corroborated":
            out.append(f"printing_distribution {k}: tcgcsv claim is {rec.get('confidence')!r}, not 'corroborated'")
        dated = [f for f in ("starts_on", "ends_on") if f in rec]
        if dated:
            out.append(f"printing_distribution {k}: tcgcsv claim carries {', '.join(dated)}; tcgcsv is never a date source")
    out += [f"printing {k}: stamped with no tcgcsv claim" for k in sorted(stamped - claimed)]
    return out


def don_set_errors(cards, printings, products, don_sets):
    """Every DON card has one don_set row, and each row agrees with the data it summarises.

    The row's key is a DON card (one with a don_design) and its don_design is
    the card's; its printing_keys are exactly the card's tcgcsv printings,
    sorted; a set_slug other than promo is an en product code lowercased, the
    app's own slug rule; source promo always means set_slug promo, and source
    group never does (CONTRACT.md, DON sets). The schema can see none of this.
    """
    design = {c["key"]: c["don_design"] for c in cards if "don_design" in c}
    prints = {}
    for p in printings:
        if p.get("site") == "tcgcsv" and p.get("card_key") in design:
            prints.setdefault(p["card_key"], []).append(p["key"])
    slugs = {p["code"].lower() for p in products if p.get("site") == "en" and "code" in p}
    out, seen = [], set()
    for rec in don_sets:
        k = rec.get("key")
        seen.add(k)
        if k not in design:
            out.append(f"don_set {k}: not a DON card")
            continue
        if rec.get("don_design") != design[k]:
            out.append(f"don_set {k}: don_design {rec.get('don_design')!r}, the card's is {design[k]!r}")
        if rec.get("printing_keys") != sorted(prints.get(k, [])):
            out.append(f"don_set {k}: printing_keys differ from the card's tcgcsv printings")
        slug, source = rec.get("set_slug"), rec.get("source")
        if slug != "promo" and slug not in slugs:
            out.append(f"don_set {k}: set_slug {slug!r} is no en product code")
        if source == "promo" and slug != "promo":
            out.append(f"don_set {k}: source promo on set_slug {slug!r}")
        if source == "group" and slug == "promo":
            out.append(f"don_set {k}: source group on set_slug promo")
    out += [f"card {k}: DON card with no don_set row" for k in sorted(design.keys() - seen)]
    return out


def strings(val):
    """Every string in a field, however deeply it sits in lists or objects."""
    if isinstance(val, str):
        yield val
    elif isinstance(val, list):
        for v in val:
            yield from strings(v)
    elif isinstance(val, dict):
        for v in val.values():
            yield from strings(v)


def references(schema, rtype):
    """{field: target type} for every field whose schema is a $ref to #/$defs/{target}_key."""
    out = {}
    for field, spec in schema["$defs"][rtype]["properties"].items():
        ref = spec.get("$ref", "")
        if field != "key" and ref.startswith("#/$defs/") and ref.endswith("_key"):
            out[field] = ref[len("#/$defs/"):-len("_key")]
    return out
