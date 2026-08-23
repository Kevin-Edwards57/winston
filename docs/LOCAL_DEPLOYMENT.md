# Running Winston on a Mac

Winston is a local application. It binds to `127.0.0.1` and is not reachable from
anywhere else on the network, which is deliberate: it holds prospect contact details and
has an SMTP credential, and neither belongs on an open port.

## Daily use

```bash
./winstonctl open       # start if needed, then open the browser
./winstonctl status     # running, healthy, and what state it is in
./winstonctl doctor     # diagnose everything
```

Winston runs at **http://127.0.0.1:5001**.

Port 5001 rather than 5000 because macOS ControlCenter binds `*:5000` for AirPlay
Receiver on every modern Mac. Override with `WINSTON_PORT`.

## Start automatically at login

```bash
./winstonctl install
```

This writes a LaunchAgent to `~/Library/LaunchAgents/studio.yardlink.winston.plist` and
loads it. Winston then starts when you log in and restarts if it crashes.

Paths are resolved at install time rather than hardcoded, so the same script works for
any user or checkout location.

`ThrottleInterval` is 15 seconds and `KeepAlive` restarts only on a crash, not on a
clean exit. Together they mean a broken configuration fails visibly in the log instead
of spinning in a restart loop.

To remove it:

```bash
./winstonctl uninstall
```

Your database, logs, and backups are left untouched.

## Commands

| Command | What it does |
|---|---|
| `./winstonctl start` | Start Winston |
| `./winstonctl stop` | Stop Winston |
| `./winstonctl restart` | Stop then start |
| `./winstonctl status` | Health, dry-run state, AI cost, record counts |
| `./winstonctl open` | Start if needed and open the browser |
| `./winstonctl logs` | Follow the application log |
| `./winstonctl errors` | Recent errors |
| `./winstonctl doctor` | Full diagnostic |
| `./winstonctl backup` | Timestamped SQLite backup with integrity check |
| `./winstonctl install` | Install autostart |
| `./winstonctl uninstall` | Remove autostart |

## Doctor

`./winstonctl doctor` checks Python, the virtual environment, dependencies, `.env`,
SQLite integrity and journal mode, backups, Ollama reachability, both models, Gemini and
Claude configuration, the LaunchAgent, port conflicts, and live application health.

Every check runs for real. Nothing reports healthy because it usually is, and a check
that cannot run counts as a warning rather than passing quietly.

Output is `PASS`, `WARN`, or `FAIL`, and the exit code is non-zero only on `FAIL`, so it
works in a script.

## Backups

```bash
./winstonctl backup
```

Writes `data/backups/winston-YYYYMMDD-HHMMSS.db` and runs `PRAGMA integrity_check` on
the copy. Fifteen backups are retained.

It uses SQLite's `.backup` rather than `cp`, because the database runs in WAL mode and a
plain copy taken while a write is in flight produces a torn file.

Restore by stopping Winston and putting the backup in place:

```bash
./winstonctl stop
cp data/backups/winston-20260823-161736.db winston.db
./winstonctl start
```

## Logs

```
logs/winston.log         stdout
logs/winston.error.log   stderr and tracebacks
```

Both are written by launchd when autostart is installed, and by the CLI otherwise.

## Local AI

Winston expects Ollama on `http://localhost:11434` with two models:

```bash
ollama pull qwen3:8b        # primary, drafting and reasoning
ollama pull llama3.2:3b     # light, classification and extraction
```

If Ollama is unreachable, `doctor` reports `FAIL` and says so plainly. Winston does not
silently fall back to a paid provider, because a transient local outage should not
become a recurring bill.

## Troubleshooting

**"Address already in use"** — something holds the port. On macOS that is usually
ControlCenter on 5000. Check with `lsof -nP -iTCP:5001 -sTCP:LISTEN`.

**Winston starts then stops** — read `logs/winston.error.log`. launchd backs off 15
seconds between restarts, so a config error shows as repeated entries rather than a
spin.

**The dashboard looks stale after an update** — static assets are versioned by
modification time, so a hard reload should not be necessary. If it persists, confirm the
served HTML contains `command-center.js?v=` with a changing number.

**Generation fails** — run `./winstonctl doctor`. The usual cause is Ollama not running
or a model not pulled.

## Moving to a VPS later

The architecture is deliberately portable: Winston, Ollama, and SQLite on a Mac is the
same stack as Winston, Ollama, and SQLite on a 16GB Ubuntu box. What changes is the
service manager, systemd instead of launchd, and that a public deployment needs an
authenticated access layer in front. Do not simply bind Flask to `0.0.0.0`.
