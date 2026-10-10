"""Draft checks: examples validate, controls fail for their stated reason, image ids parse."""
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).parent))
from image_id import parse_image_id
from data_check import check_data, don_set_errors, retired_errors, run_errors, stamped_claim_errors, stamped_finish_errors
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

# Controls: runs. A retired site's run from before its retirement passes (the
# audit record stays); the same run started after it, and a run of a site that
# never existed, go red.
run_rec = {"run_id": "20261008T175529Z-asia-en", "site": "asia-en", "started_at": "2026-10-08T17:55:29Z",
           "finished_at": "2026-10-08T17:58:00Z", "pages_fetched": 1, "pages_unchanged": 0, "blocks_parsed": 1,
           "blocks_without_source_text": 0, "added": 0, "changed": 0, "removed": 0,
           "refusals": {"http_error": 0, "zero_parse": 0, "count_drop": 0, "count_mismatch": 0}}
site_retired = [{"key": "prt_000000000009", "printing_key": "prt_000000000009", "reason": "site_retired",
                 "retired_at": "2026-10-10T18:02:46Z", "source_ids": ["asia-en:OP01-001_p1"]}]
if run_errors([("runs/a.json", run_rec)], schema, site_retired):
    bad += 1
    print("FAIL control red: a retired site's run from before its retirement")
else:
    print("ok   green a retired site's run from before its retirement")
for why, rec in [
    ("a retired site's run started after its retirement", dict(run_rec, started_at="2026-10-11T04:23:00Z")),
    ("a run of a site the schema has never had", dict(run_rec, site="kr")),
]:
    if run_errors([("runs/a.json", rec)], schema, site_retired):
        print("ok   red  ", why)
    else:
        bad += 1
        print("FAIL control passed:", why)

# Controls: a stamped printing on a Foil locator, and one with no locator; its Normal twin passes.
stamp = [{"key": "prt_op17002stamp", "variant": "stamped"}]
foil = [{"key": "tcgcsv:712666:Foil", "printing_key": "prt_op17002stamp", "image_id": "712666:Foil"}]
normal = [{"key": "tcgcsv:712666:Normal", "printing_key": "prt_op17002stamp", "image_id": "712666:Normal"}]
for why, locs in [("a stamped printing on a Foil locator", foil), ("a stamped printing with no locator", [])]:
    if stamped_finish_errors(stamp, locs) and not stamped_finish_errors(stamp, normal):
        print("ok   red  ", why)
    else:
        bad += 1
        print("FAIL control:", why)

# Controls: the stamped-claim invariants (upper 88125). Each red case is the
# passing claim with one thing changed, so a red here is that one thing.
dists = [{"key": "dist_op17re000000", "site": "tcgcsv"}, {"key": "dist_enop17re0000", "site": "en"}]
good = {"key": "ev_0000000000000001", "printing_key": "prt_op17002stamp", "distribution_key": "dist_op17re000000",
        "source": "tcgcsv", "confidence": "corroborated"}
plain = [{"key": "prt_op17002base0", "variant": "base"}]
if stamped_claim_errors(stamp, dists, [good]):
    bad += 1
    print("FAIL control: the passing stamped claim is red:", stamped_claim_errors(stamp, dists, [good]))
for why, printings, claims in [
    ("a stamped printing with no tcgcsv claim", stamp, [dict(good, source="official_cardlist")]),
    ("a tcgcsv claim that is not corroborated", stamp, [dict(good, confidence="authoritative")]),
    ("a tcgcsv claim with starts_on", stamp, [dict(good, starts_on="2026-08-21")]),
    ("a tcgcsv claim with ends_on", stamp, [dict(good, ends_on="2026-08-31")]),
    ("a tcgcsv claim on a printing that is not stamped", stamp + plain, [good, dict(good, key="ev_0000000000000002", printing_key="prt_op17002base0")]),
    ("a tcgcsv claim on an official site's distribution", stamp, [dict(good, distribution_key="dist_enop17re0000")]),
]:
    if stamped_claim_errors(printings, dists, claims):
        print("ok   red  ", why)
    else:
        bad += 1
        print("FAIL control passed:", why)
# The count is open: two tcgcsv claims on one stamp are not an error.
if stamped_claim_errors(stamp, dists, [good, dict(good, key="ev_0000000000000003")]):
    bad += 1
    print("FAIL control: two claims on one stamp read red, but the count is open")
else:
    print("ok   green two tcgcsv claims on one stamped printing (count open)")

# Controls: the don_set rules (CONTRACT.md, DON sets). Each red case is the
# passing set with one thing changed, so a red here is that one thing.
d_cards = [{"key": "card_d0d0d0d0d0d0", "don_design": "PRB-01:don-card-luffy"}, {"key": "card_op01001aaaaa", "number": "OP01-001"}]
d_prints = [{"key": "prt_00000000d0d2", "card_key": "card_d0d0d0d0d0d0", "site": "tcgcsv"},
            {"key": "prt_00000000d0d1", "card_key": "card_d0d0d0d0d0d0", "site": "tcgcsv"},
            {"key": "prt_op01001base0", "card_key": "card_op01001aaaaa", "site": "en"}]
d_prods = [{"key": "en:569301", "site": "en", "code": "PRB-01"}, {"key": "jp:550301", "site": "jp", "code": "DP-99"}]
d_good = {"key": "card_d0d0d0d0d0d0", "printing_keys": ["prt_00000000d0d1", "prt_00000000d0d2"],
          "don_design": "PRB-01:don-card-luffy", "set_slug": "prb-01", "source": "group"}
for why, rows in [("the passing don_set", [d_good]), ("an override onto promo", [dict(d_good, set_slug="promo", source="override")]),
                  ("a promo group's DON", [dict(d_good, set_slug="promo", source="promo")])]:
    if don_set_errors(d_cards, d_prints, d_prods, rows):
        bad += 1
        print("FAIL control: green case is red:", why, don_set_errors(d_cards, d_prints, d_prods, rows))
    else:
        print("ok   green", why)
for why, rows in [
    ("a DON card with no don_set row", []),
    ("a don_set row on a numbered card", [d_good, dict(d_good, key="card_op01001aaaaa")]),
    ("a don_set row whose don_design is not the card's", [dict(d_good, don_design="PRB-01:don-card-uta")]),
    ("printing_keys missing one of the card's printings", [dict(d_good, printing_keys=["prt_00000000d0d1"])]),
    ("printing_keys unsorted", [dict(d_good, printing_keys=["prt_00000000d0d2", "prt_00000000d0d1"])]),
    ("printing_keys with another card's printing", [dict(d_good, printing_keys=["prt_00000000d0d1", "prt_00000000d0d2", "prt_op01001base0"])]),
    ("a set_slug that is no en product code", [dict(d_good, set_slug="prb-09")]),
    ("a set_slug that is only a jp product code", [dict(d_good, set_slug="dp-99")]),
    ("source promo off the promo page", [dict(d_good, source="promo")]),
    ("source group on the promo page", [dict(d_good, set_slug="promo")]),
]:
    if don_set_errors(d_cards, d_prints, d_prods, rows):
        print("ok   red  ", why)
    else:
        bad += 1
        print("FAIL control passed:", why)

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
