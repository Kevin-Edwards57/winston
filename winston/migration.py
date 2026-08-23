"""Idempotent JSON-to-SQLite migration for Winston's legacy data."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .repository import WinstonRepository, normalize_email, stable_id, utc_now


JSON_SOURCES = ("contacts.json", "social_leads.json", "emailed.json", "followups.json", "stats.json")


def _rows(data: Any) -> list[Any]:
    return data if isinstance(data, list) else [data]


def create_backup(source_dir: Path, backup_root: Path | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = backup_root or source_dir / "backups"
    destination = root / f"json-{stamp}"
    suffix = 1
    while destination.exists():
        destination = root / f"json-{stamp}-{suffix}"
        suffix += 1
    destination.mkdir(parents=True)
    for name in JSON_SOURCES:
        source = source_dir / name
        if source.exists():
            shutil.copy2(source, destination / name)
    return destination


def migrate_json(repository: WinstonRepository, source_dir: str | Path = ".",
                 *, backup: bool = True, report_path: str | Path | None = None) -> dict[str, Any]:
    source_dir = Path(source_dir)
    repository.initialize()
    starting_counts = repository.counts()
    backup_dir = create_backup(source_dir) if backup else None
    run_id = str(uuid.uuid4())
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": utc_now(),
        "database": str(repository.database_path),
        "backup_directory": str(backup_dir) if backup_dir else None,
        "sources": {},
        "duplicates": {},
        "errors": [],
    }
    with repository.transaction() as connection:
        connection.execute("INSERT INTO migration_runs(id,started_at) VALUES(?,?)", (run_id, report["started_at"]))

    loaded: dict[str, Any] = {}
    for name in JSON_SOURCES:
        path = source_dir / name
        if not path.exists():
            report["sources"][name] = {"status": "missing", "rows": 0, "new_imports": 0}
            continue
        try:
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report["errors"].append({"source": name, "error": str(exc)})
            continue
        new_imports = 0
        for index, payload in enumerate(_rows(loaded[name])):
            _, inserted = repository.record_legacy_row(name, index, payload)
            new_imports += int(inserted)
        report["sources"][name] = {"status": "ok", "rows": len(_rows(loaded[name])), "new_imports": new_imports}

    contacts = loaded.get("contacts.json", [])
    social_leads = loaded.get("social_leads.json", [])
    followups = loaded.get("followups.json", [])
    emailed = loaded.get("emailed.json", [])

    email_counts = Counter(normalize_email(row.get("email")) for row in contacts if isinstance(row, dict) and row.get("email"))
    emailed_counts = Counter(normalize_email(value) for value in emailed if value)
    followup_counts = Counter(
        normalize_email(row.get("email")) for row in followups
        if isinstance(row, dict) and row.get("email")
    )
    report["duplicates"] = {
        "contact_emails": {email: count for email, count in email_counts.items() if count > 1},
        "emailed_addresses": {email: count for email, count in emailed_counts.items() if count > 1},
        "followup_addresses": {email: count for email, count in followup_counts.items() if count > 1},
    }

    created_contacts = 0
    for source_name, records in (("contacts.json", contacts), ("social_leads.json", social_leads), ("followups.json", followups)):
        for payload in records:
            if not isinstance(payload, dict):
                continue
            try:
                _, created = repository.upsert_contact(payload, source_name)
                created_contacts += int(created)
            except sqlite3.IntegrityError as exc:
                report["errors"].append({"source": source_name, "identity": payload.get("place_id") or payload.get("email"), "error": str(exc)})

    imported_messages = 0
    for index, payload in enumerate(followups):
        if not isinstance(payload, dict) or not payload.get("email"):
            continue
        contact_id, _ = repository.upsert_contact(payload, "followups.json")
        source_record_id = stable_id("followup", index, normalize_email(payload.get("email")), payload.get("sent_date", ""))
        with repository.transaction() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO sent_messages
                   (id,contact_id,email,subject,body,sent_at,followup_sent,source,source_record_id)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (source_record_id, contact_id, payload.get("email", ""), payload.get("subject", ""),
                 payload.get("original_body", ""), payload.get("sent_date"), int(bool(payload.get("followup_sent"))),
                 "followups.json", source_record_id),
            )
            imported_messages += int(cursor.rowcount == 1)

    stats = loaded.get("stats.json")
    if isinstance(stats, dict):
        with repository.transaction() as connection:
            connection.execute(
                """INSERT INTO settings(key,value_json,updated_at) VALUES('legacy_stats',?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (json.dumps(stats, sort_keys=True), utc_now()),
            )

    repository.add_event("migration.completed", entity_type="migration", entity_id=run_id,
                         details={"new_contacts": created_contacts, "new_sent_messages": imported_messages})
    report["completed_at"] = utc_now()
    final_counts = repository.counts()
    report["results"] = {
        **final_counts,
        "net_new_contacts": final_counts["contacts"] - starting_counts["contacts"],
        "contact_insert_operations": created_contacts,
        "new_sent_messages": imported_messages,
    }
    with repository.transaction() as connection:
        connection.execute("UPDATE migration_runs SET completed_at=?,report_json=? WHERE id=?",
                           (report["completed_at"], json.dumps(report, sort_keys=True), run_id))

    destination = Path(report_path) if report_path else source_dir / "migration-report.json"
    destination.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    report["report_path"] = str(destination)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Winston JSON data to SQLite")
    parser.add_argument("--database", default="winston.db")
    parser.add_argument("--source-dir", default=".")
    parser.add_argument("--no-backup", action="store_true", help="Only for repeat verification after a backup already exists")
    parser.add_argument("--report", default="migration-report.json")
    args = parser.parse_args()
    result = migrate_json(WinstonRepository(args.database), args.source_dir,
                          backup=not args.no_backup, report_path=args.report)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
