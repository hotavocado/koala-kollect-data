"""Keep the daily schedule alive: commit a heartbeat when main has been quiet.

GitHub disables a public repo's scheduled workflows "when no repository activity
has occurred in 60 days" (docs: Disabling and enabling a workflow). The docs do
not say what counts as activity. A scheduled run cannot be it, or the rule could
never fire; a commit on the default branch is the form every other source
agrees on. The daily ingest commits only when the data changes, and new sets
come about every two months, so a quiet spell can outlast the 60 days.

So when HEAD's commit time is `--days` or more old (default 30, half the limit),
this writes today's UTC date to state/keepalive.txt and commits it. It never
pushes; the workflow pushes only if HEAD moved. Git identity is the caller's.

usage: python keepalive.py <repo> [--days 30] [--dry-run] [--now EPOCH]
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

FILE = "state/keepalive.txt"
DAY = 86400


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


def keepalive(repo, days=30, now=None, dry_run=False):
    """Return (committed, message). Commits only when HEAD is `days` or more old."""
    now = int(time.time()) if now is None else now
    head_ct = int(git(repo, "log", "-1", "--format=%ct"))
    age = (now - head_ct) // DAY
    if now - head_ct < days * DAY:
        return False, f"keepalive: HEAD is {age} days old, under {days}; nothing to do"
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    if dry_run:
        return False, f"keepalive: HEAD is {age} days old; DRY RUN, would write {FILE} = {today} and commit"
    path = Path(repo) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(today + "\n", "utf-8")
    git(repo, "add", FILE)
    stamp = f"@{now} +0000"  # the commit carries `now`, so the next check measures from it
    git(repo, "commit", "-m", f"Keepalive {today}: main quiet for {age} days",
        "-m", "The daily schedule is disabled after 60 days without repository activity; see ingest/keepalive.py.",
        env=dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp))
    return True, f"keepalive: HEAD was {age} days old; committed {FILE} = {today}"


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", type=int, help="epoch seconds, for tests")
    a = ap.parse_args(argv[1:])
    _, msg = keepalive(a.repo, a.days, a.now, a.dry_run)
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
