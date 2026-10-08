"""Draft checks: examples validate, controls fail for their stated reason, image ids parse."""
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).parent))
from image_id import parse_image_id

root = Path(__file__).resolve().parent.parent
schema = json.loads((root / "schema/v1.schema.json").read_text())
Draft202012Validator.check_schema(schema)
bad = 0


def validator(t):
    return Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{t}"})


for line in (root / "examples/valid.jsonl").read_text().splitlines():
    row = json.loads(line)
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
    msgs = [e.message for e in errs]
    if not errs:
        bad += 1
        print("FAIL control passed:", row["why"])
    elif not any(row["expect"] in m for m in msgs):
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
    ("tcgcsv", "512345", (None, None, None)),
]
for site, iid, want in cases:
    got = parse_image_id(site, iid)
    got = (got["base"], got["suffix_family"], got["suffix_n"])
    print("ok   id   " if got == want else "FAIL id   ", site, iid, got)
    bad += got != want
for site, iid in [("en", "OP01-001_x1"), ("en", "op01-001"), ("cn", "OP01-001"), ("tcgcsv", "512345_p1"), ("jp", "OP01-001_p")]:
    try:
        parse_image_id(site, iid)
        bad += 1
        print("FAIL id accepted", site, iid)
    except ValueError:
        print("ok   id red", site, iid)

print("RESULT", "GREEN" if bad == 0 else f"RED ({bad})")
sys.exit(1 if bad else 0)
