# PII remediation — 2026-08-23

Record of the removal of personally identifiable information from Winston's Git history.

## What was exposed

`https://github.com/Kevin-Edwards57/winston` contained prospect PII in tracked files,
present since the initial commit (`9752cd3`, "Initial commit - Winston B2B outreach automation").

| File | Records | Unique emails | Message bodies | Exposure |
|---|---|---|---|---|
| `followups.json` | 131 | 115 | **131 full outreach bodies** | Pushed to remote |
| `emailed.json` | 131 | 115 | — | Pushed to remote |
| `winston_dashboard.png` | 1 image | ~7 visible | visible draft bodies | Pushed to remote |
| `contacts.json` | 1,355 | 613 | — | Local checkpoint ref only |
| `social_leads.json` | 426 | — | — | Local checkpoint ref only |

**Union of exposed unique business email addresses: 122.**

The dashboard screenshot rendered live prospect data: seven business email addresses,
named individuals including real estate agents and CPAs, street addresses, and draft
message bodies. The specific addresses are deliberately not reproduced here — this
document is tracked, and restating them would recreate the exposure it records. They are
recoverable from the pre-filter backup if ever needed for a notification obligation.

`contacts.json` and `social_leads.json` were never committed to `main`, but were reachable
through a local editor checkpoint ref under `refs/codex/turn-diffs/checkpoints/…`, which
kept those blobs alive and GC-exempt.

## What was NOT exposed

- **No credentials.** `.env` was never tracked in any commit. A scan of every blob across
  all refs found no API key patterns (`AIza…`, `sk-ant-…`, `xoxb-…`, private key headers).
- **No database.** `winston.db` was covered by `*.db` in the original `.gitignore`.

Because no secret ever reached the repository, no key rotation was required. The
`ANTHROPIC_KEY` and `GMAIL_APP_PASSWORD` in the local `.env` were never at risk from this
exposure. Rotation remains advisable only if those values were shared through some other
channel.

## Remediation performed

1. **Backups taken before any destructive step**
   - `../Winston-backup-20260823-prefilter/` — full working tree including `.git`
   - `../winston-remote-mirror.git` — `git clone --mirror` of the remote as it stood
2. **Deleted the local `refs/codex/turn-diffs/checkpoints/…` ref** holding `contacts.json`
   and `social_leads.json`.
3. **Rewrote history with `git filter-repo --invert-paths`**, removing `emailed.json`,
   `followups.json`, `contacts.json`, `social_leads.json`, `stats.json`, and
   `winston_dashboard.png` from every commit.
4. **Re-added the `origin` remote** (`filter-repo` strips it by design).
5. **Force-pushed the rewritten history.**
6. **Replaced `.gitignore`** with comprehensive rules covering secrets, prospect data,
   databases, and all image formats (screenshots can render prospect data).
7. **Restored working-tree data files**, which the application still reads at runtime and
   which are now ignored rather than tracked.

### Note on `filter-repo` and uncommitted work

`git filter-repo` performs a hard reset. Uncommitted changes to `winston_app.py` (77,058
bytes) and `README.md` (6,738 bytes) were reverted to their older committed versions
(48,540 and 5,340 bytes). Both were restored from the pre-filter backup and verified: the
full test suite passed immediately after restoration. **Commit or stash all work before
running `filter-repo`.**

## Verification

Commit hash changed `9752cd3` → `551bb81`, confirming the rewrite.

| Check | Result |
|---|---|
| `git log --all -- <each PII path>` | **0 commits** for all six paths |
| Blobs >100KB reachable from any ref | **none** |
| Business email addresses in any reachable blob | **none** |
| Remaining email in history | `deankevin14@gmail.com` — the committer identity in commit metadata, not prospect data |
| `git check-ignore` on all data artifacts | all **ignored** |
| Test suite after remediation | **passing** |

## Residual risk

Force-pushing rewrites the remote, but does not reach copies made before the rewrite:

- **GitHub may retain unreferenced objects.** Rewritten commits can stay accessible by
  direct SHA through the API until GitHub garbage-collects. Opening a support request to
  force GC removes this.
- **Forks and clones keep the old history.** Anyone who cloned or forked before
  2026-08-23 still holds the PII.
- **Third-party mirrors and code-search indexes** may have cached the old content.

Given the repository is single-author and was not widely distributed, residual exposure is
considered low. If the repository was ever public and indexed, treat the 122 affected
business addresses as disclosed and follow the applicable breach-notification assessment
under NY SHIELD.

## Prevention

- `.gitignore` now blocks `*.json` prospect artifacts by name and pattern, all databases,
  and all image formats.
- `tests/test_pii_guard.py` fails the build if a known PII artifact becomes tracked or if
  any tracked file contains email-shaped strings.
