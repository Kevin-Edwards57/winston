"""Prevent prospect PII from re-entering version control.

Winston's Git history was rewritten on 2026-08-23 to remove 122 business email
addresses and 131 outreach message bodies (docs/PII-REMEDIATION.md). These tests
exist so that never silently happens again.
"""
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Artifacts that hold prospect PII and must never be tracked.
PII_ARTIFACTS = [
    "contacts.json", "social_leads.json", "emailed.json",
    "followups.json", "stats.json", "migration-report.json",
]

EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Addresses that legitimately appear in source: the owner's own identity,
# documentation examples, and test fixtures.
ALLOWED = re.compile(
    rb"(example\.(com|org|net)"           # RFC 2606 documentation domains
    rb"|\.example\b"                   # RFC 2606 reserved .example TLD
    rb"|@yardlinkstudio|yardlinkstudio\.com"  # YardLink's own identity
    rb"|deankevin14@"                      # repository owner
    rb"|your_gmail|user@|test@|noreply@"   # placeholders
    rb"|info@info\.com|hello@info\.com"    # junk-address blocklist literals
    rb"|info@theirbusiness\.com"           # docstring example
    rb")",
    re.IGNORECASE,
)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    ).stdout


class PIIGuardTests(unittest.TestCase):
    def test_pii_artifacts_are_not_tracked(self):
        tracked = set(git("ls-files").split())
        for artifact in PII_ARTIFACTS:
            self.assertNotIn(artifact, tracked, f"{artifact} is tracked and contains prospect PII")

    def test_pii_artifacts_are_gitignored(self):
        for artifact in PII_ARTIFACTS:
            result = subprocess.run(
                ["git", "-C", str(REPO), "check-ignore", "-q", artifact], check=False
            )
            self.assertEqual(result.returncode, 0, f"{artifact} is not covered by .gitignore")

    def test_images_are_gitignored(self):
        """Screenshots render prospect data; the dashboard PNG leaked 7 real addresses."""
        for image in ["winston_dashboard.png", "screenshot.jpg", "capture.jpeg"]:
            result = subprocess.run(
                ["git", "-C", str(REPO), "check-ignore", "-q", image], check=False
            )
            self.assertEqual(result.returncode, 0, f"{image} would be committable")

    def test_databases_are_gitignored(self):
        for db in ["winston.db", "winston.db-wal", "winston.db-shm", "prospects.sqlite3"]:
            result = subprocess.run(
                ["git", "-C", str(REPO), "check-ignore", "-q", db], check=False
            )
            self.assertEqual(result.returncode, 0, f"{db} would be committable")

    def test_env_is_gitignored_but_example_is_not(self):
        self.assertEqual(
            subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", ".env"], check=False).returncode,
            0, ".env must be ignored")
        self.assertNotEqual(
            subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", ".env.example"], check=False).returncode,
            0, ".env.example should stay tracked as documentation")

    def test_no_prospect_emails_in_tracked_files(self):
        """No tracked file may contain an email address outside the allowlist."""
        offenders = []
        for path in git("ls-files").split():
            full = REPO / path
            if not full.is_file():
                continue
            try:
                blob = full.read_bytes()
            except OSError:
                continue
            for match in EMAIL.findall(blob):
                if not ALLOWED.search(match):
                    offenders.append(f"{path}: {match.decode(errors='replace')}")
        self.assertEqual(offenders, [], f"Prospect emails found in tracked files: {offenders[:10]}")

    def test_pii_absent_from_reachable_git_history(self):
        """The 2026-08-23 rewrite must stay effective."""
        for artifact in PII_ARTIFACTS + ["winston_dashboard.png"]:
            log = git("log", "--all", "--oneline", "--", artifact).strip()
            self.assertEqual(log, "", f"{artifact} is still present in Git history")


if __name__ == "__main__":
    unittest.main()
