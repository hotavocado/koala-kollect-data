"""Draft checks: examples validate, controls fail for their stated reason, image ids parse."""
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).parent))
from image_id import parse_image_id
from data_check import check_data, retired_errors, stamped_finish_errors
from tcgcsv_check import check as tcgcsv_check

root = Path(__file__).resolve().parent.parent
schema = json.loads((root / "schema/v1.schema.json").read_text())
Draft202012Validator.check_schema(schema)
bad = 0


def validator(t):
    return Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{t}"})


def repeated_keys(rows):
    """(type, key) pairs on more than one line. A per-record schema check cannot
    see this, and the app sync upserts by key, so a repeat silently overwrites."""
    seen, out = set(), []
    for row in rows:
        k = (row["type"], row["record"].get("key", row["record"].get("run_id")))
        if k in seen:
            out.append(k)
        seen.add(k)
    return out


valid_rows = [json.loads(line) for line in (root / "examples/valid.jsonl").read_text().splitlines()]
for k in repeated_keys(valid_rows):
    bad += 1
    print("FAIL valid key repeated across lines:", k)
# Control: the two cn:6987 locator lines 5abe44b shipped (alyssa, general 87334).
twice = [{"type": "printing_locator", "record": {"key": "cn:6987", "printing_key": p}} for p in ("prt_000000000002", "prt_w95hkvuf16bf")]
if repeated_keys(twice) == [("printing_locator", "cn:6987")]:
    print("ok   red   a key on two lines")
else:
    bad += 1
    print("FAIL control passed: a key on two lines")

for row in valid_rows:
    errs = list(validator(row["type"]).iter_errors(row["record"]))
    if errs:
        bad += 1
        print("FAIL valid", row["type"], errs[0].message)
    else:
        print("ok   valid", row["type"])

for line in (root / "examples/invalid.jsonl").read_text().splitlines():
    row = json.loads(line)
    errs = list(validator(row["type"]).iter_errors(row["record"]))
    # A control proves something only if it fails for its OWN reason, so each
    # names the error it must produce (any error alone would pass a broken rule).
    # A list names one error in each spelling jsonschema has used for it (CI
    # installs the latest: minLength 1 reads "is too short" on 4.10, "should be
    # non-empty" on 4.26).
    msgs = [e.message for e in errs]
    expects = row["expect"] if isinstance(row["expect"], list) else [row["expect"]]
    if not errs:
        bad += 1
        print("FAIL control passed:", row["why"])
    elif not any(x in m for x in expects for m in msgs):
        bad += 1
        print("FAIL control red for the wrong reason:", row["why"], "->", msgs)
    else:
        print("ok   red  ", row["why"])

cases = [
    ("en", "OP14-108_p3", ("OP14-108", "p", 3)),
    ("jp", "OP14-108_p1", ("OP14-108", "p", 1)),
    ("en", "P-001_p5", ("P-001", "p", 5)),
    ("jp", "OP01-001_r1", ("OP01-001", "r", 1)),
    ("en", "ST20-004", ("ST20-004", None, None)),
    ("en", "EB01-012", ("EB01-012", None, None)),
    ("tcgcsv", "512345:Foil", (None, None, None)),
    ("tcgcsv", "512345:Normal", (None, None, None)),
]
for site, iid, want in cases:
    got = parse_image_id(site, iid)
    got = (got["base"], got["suffix_family"], got["suffix_n"])
    print("ok   id   " if got == want else "FAIL id   ", site, iid, got)
    bad += got != want
for site, iid in [("en", "OP01-001_x1"), ("en", "op01-001"), ("cn", "OP01-001"), ("tcgcsv", "512345_p1"), ("tcgcsv", "512345"), ("tcgcsv", "512345:foil"), ("jp", "OP01-001_p")]:
    try:
        parse_image_id(site, iid)
        bad += 1
        print("FAIL id accepted", site, iid)
    except ValueError:
        print("ok   id red", site, iid)

# Controls: a key both live and retired, and a retired row whose key is not its printing_key.
for why, live, rec in [
    ("a retired key that is also a live printing", {"prt_4648aaaaaaaa"},
     {"key": "prt_4648aaaaaaaa", "printing_key": "prt_4648aaaaaaaa"}),
    ("a retired row whose key differs from its printing_key", set(),
     {"key": "prt_4648aaaaaaaa", "printing_key": "prt_4649aaaaaaaa"}),
]:
    if retired_errors(live, [rec]):
        print("ok   red  ", why)
    else:
        bad += 1
        print("FAIL control passed:", why)

# Control: a stamped printing on a Foil locator, and its Normal twin passing.
stamp = [{"key": "prt_op17002stamp", "variant": "stamped"}]
foil = [{"key": "tcgcsv:712666:Foil", "printing_key": "prt_op17002stamp", "image_id": "712666:Foil"}]
normal = [{"key": "tcgcsv:712666:Normal", "printing_key": "prt_op17002stamp", "image_id": "712666:Normal"}]
if stamped_finish_errors(stamp, foil) and not stamped_finish_errors(stamp, normal):
    print("ok   red   a stamped printing on a Foil locator")
else:
    bad += 1
    print("FAIL control: a stamped printing on a Foil locator")

# The data itself, when the ingest has written any: manifest, files, schema, references.
data_errors = check_data(root, schema)
for e in data_errors[:20]:
    print("FAIL data", e)
if (root / "manifest.json").exists():
    print("ok   data " if not data_errors else f"FAIL data {len(data_errors)} errors", "manifest.json and every file it lists")
bad += len(data_errors)

# tcgcsv cross-check of en release dates: printed, never counted. tcgcsv is a
# cross-check and never a source (CONTRACT.md), and a network failure here
# must not turn a push red.
for ln in tcgcsv_check(root):
    print(ln)

print("RESULT", "GREEN" if bad == 0 else f"RED ({bad})")
sys.exit(1 if bad else 0)
