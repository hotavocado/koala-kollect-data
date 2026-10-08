"""Check data/ and manifest.json the way the app sync reads them, plus the contract.

Returns a list of errors; empty means the data is clean. Checks: every data
file is listed in the manifest and every listed file exists with its sha256 and
row count; LF, trailing newline, lines sorted by unique key, object keys in
schema order; every record valid for its type; no HTML tag or entity left in
any string; and references resolve (card_key, printing_key, product_key).
"""
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

TYPE_OF_DIR = {"cards": "card", "card_observations": "card_observation", "printings": "printing",
               "printing_locators": "printing_locator", "products": "product",
               "printing_products": "printing_product", "distributions": "distribution",
               "printing_distributions": "printing_distribution", "printing_links": "printing_link"}

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
                if isinstance(val, str) and MARKUP.search(val):
                    errors.append(f"{path}:{i}: markup in {field}: {MARKUP.search(val).group(0)!r}")
            if k in keys[rtype]:
                errors.append(f"{path}:{i}: key {k!r} repeated across files")
            keys[rtype].add(k)
            records.append((path, i, rtype, rec))
    refs = {"card_key": "card", "printing_key": "printing", "product_key": "product"}
    for path, i, rtype, rec in records:
        for field, target in refs.items():
            if field in rec and rec[field] not in keys[target]:
                errors.append(f"{path}:{i}: {field} {rec[field]} has no {target} row")
    return errors
