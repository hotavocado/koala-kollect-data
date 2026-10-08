"""Keepalive controls on a throwaway git repo whose HEAD time is set by hand.

Each control names the decision it pins: no commit while main is fresh, exactly
one commit touching only state/keepalive.txt once it is 30 days quiet, nothing
written on a dry run, and nothing under data/, manifest.json or runs/ changed.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import keepalive

DAY = keepalive.DAY
T0 = 1_791_331_200  # 2026-10-07T00:00:00Z, HEAD's commit time


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


class Keepalive(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        for rel, body in [("data/cards.jsonl", "{}\n"), ("manifest.json", "{}\n"), ("runs/r.json", "{}\n")]:
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(body, "utf-8")
        stamp = f"@{T0} +0000"
        env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "seed", env=env)
        self.head = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.repo)

    def commits(self):
        return int(git(self.repo, "rev-list", "--count", "HEAD"))

    def test_fresh_main_makes_no_commit(self):
        done, msg = keepalive.keepalive(self.repo, 30, now=T0 + 30 * DAY - 1)
        self.assertFalse(done, msg)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        self.assertFalse((self.repo / keepalive.FILE).exists())

    def test_quiet_main_makes_one_commit_touching_only_the_file(self):
        done, msg = keepalive.keepalive(self.repo, 30, now=T0 + 30 * DAY)
        self.assertTrue(done, msg)
        self.assertEqual(self.commits(), 2)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD~1"), self.head)
        changed = git(self.repo, "diff", "--name-only", "HEAD~1", "HEAD").splitlines()
        self.assertEqual(changed, [keepalive.FILE])
        self.assertEqual((self.repo / keepalive.FILE).read_text("utf-8"), "2026-11-06\n")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_heartbeat_resets_the_clock(self):
        keepalive.keepalive(self.repo, 30, now=T0 + 40 * DAY)
        done, msg = keepalive.keepalive(self.repo, 30, now=T0 + 41 * DAY)
        self.assertFalse(done, msg)
        self.assertEqual(self.commits(), 2)

    def test_dry_run_writes_and_commits_nothing(self):
        done, msg = keepalive.keepalive(self.repo, 30, now=T0 + 90 * DAY, dry_run=True)
        self.assertFalse(done)
        self.assertIn("DRY RUN", msg)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(self.repo, "status", "--porcelain", "--untracked-files=all"), "")


if __name__ == "__main__":
    unittest.main()
