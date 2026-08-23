# JSON to SQLite migration

The migration never deletes or edits source JSON.

```bash
python3 -m winston.migration --database winston.db
```

This creates:

- `backups/json-YYYYMMDD-HHMMSS/` containing copies of every JSON source
- `winston.db` containing immutable legacy rows and canonical operational tables
- `migration-report.json` with source counts, duplicate identities, errors, and result counts

The migration is idempotent. Re-running it records a new migration run, but unchanged legacy rows and sent records are not duplicated. A new backup is created on every normal run. Use `--no-backup` only for automated idempotency verification against disposable data.

To inspect the result:

```bash
python3 -m unittest discover -v
python3 -c "from winston.repository import WinstonRepository; print(WinstonRepository('winston.db').counts())"
```
