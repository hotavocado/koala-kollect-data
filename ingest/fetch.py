"""Fetch every series page of one Bandai site. Read-only; writes only to out_dir.

A page that fails is recorded with its error and NOT retried silently past three
attempts; the caller decides what a failed page means (the removal guard treats
it as http_error and refuses removals from it).
"""
import hashlib
import json
import time
import urllib.request
from pathlib import Path

from bandai import series_options

HOSTS = {"en": "en", "asia-en": "asia-en", "jp": "www", "tc": "asia-tc"}
UA = "Mozilla/5.0 (compatible; koala-kollect-ingest/0.1; +https://github.com/hotavocado/koala-kollect-data)"


def _get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def cardlist_url(site, series_id=None):
    base = f"https://{HOSTS[site]}.onepiece-cardgame.com/cardlist/"
    return base if series_id is None else f"{base}?series={series_id}"


def fetch_site(site, out_dir, pause=1.5, attempts=3):
    """Fetch the dropdown, then every series page. Returns the fetch log (list of dicts)."""
    out = Path(out_dir) / site
    out.mkdir(parents=True, exist_ok=True)
    _, index = _get(cardlist_url(site))
    (out / "_index.html").write_bytes(index)
    options = series_options(index.decode("utf-8", "replace"))
    if not options:
        raise RuntimeError(f"{site}: series dropdown not found")
    log = []
    for series_id, label in options:
        entry = {"site": site, "series_id": series_id, "label": label}
        errors = []
        for attempt in range(attempts):
            try:
                status, body = _get(cardlist_url(site, series_id))
                entry.update(status=status, bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
                (out / f"{series_id}.html").write_bytes(body)
                break
            except Exception as e:  # recorded, never swallowed
                errors.append(f"{type(e).__name__}: {e}")
                time.sleep(5 * (attempt + 1))
        else:
            # Only a fetch that never succeeded carries `error`; a stale page from
            # an earlier run must not be read as this run's copy.
            entry.update(status=None, error=errors[-1])
            (out / f"{series_id}.html").unlink(missing_ok=True)
        if errors:
            entry["attempt_errors"] = errors
        entry["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log.append(entry)
        time.sleep(pause)
    (out / "_fetch_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1))
    return log
