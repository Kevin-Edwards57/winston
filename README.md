# Winston

Winston is an AI-powered B2B outreach command center built for YardLink Studio. It discovers local business leads across NYC and Long Island, scrapes their websites for contact information, and drafts personalized outreach through a zero-cost-first AI provider layer.

---

## Live Stats

| Metric | Count |
|---|---|
| Leads discovered | 1,749 |
| Emails sent | 139 |
| Follow-ups delivered | 131 |
| Unique businesses contacted | 122 |
| Business categories covered | 15+ |
| Geographic areas covered | All 5 boroughs + Nassau and Suffolk County |

---

## What Winston Does

Winston runs a full outreach pipeline from lead discovery to confirmed delivery without manual searching. You point it at a market, it finds businesses, scrapes their websites, writes a personalized pitch, and queues everything for review. Sending requires a separate human confirmation after approval.

The legacy follow-up sender was **permanently removed** on 2026-08-23. It delivered mail
directly from JSON, bypassing suppression, idempotency, atomic claiming, and human
confirmation. There is now exactly one production send path, and it runs the full state
machine. Historical follow-up records are preserved as read-only data.

The safest no-discovery-cost workflow is **Draft Existing (Free)**. It selects a
bounded batch from the migrated SQLite contacts, excludes suppressed, previously sent,
and already-drafted recipients, then creates review drafts with local Ollama. The
separate **Google Scan $** control is explicitly labeled because Google Places usage
can be billable.

---

## Architecture

```
Google Places API → Local HTML Scraper → Email Extractor → AI Provider Router
                                                               ↓
SQLite Repository ← Confirmed Send Jobs ← Gmail SMTP ← Flask Dashboard
```

**Lead discovery** — Winston queries Google Places across 15+ business categories including Jamaican and Caribbean restaurants, barbershops, salons, auto repair shops, dentists, real estate agencies, and more. It covers all five New York City boroughs plus Nassau and Suffolk County on Long Island.

**Website scraping** — Winston uses local `requests` and Python's built-in HTML parser with no paid scraping service. It fetches public HTML, discovers same-domain contact/about pages, applies response-size and content-type limits, extracts emails and phone numbers, and validates explicit social profile URLs.

**AI generation** — Gemini free tier is tried first, followed by local Ollama. Claude is optional and cannot be called unless paid mode and Claude are both explicitly enabled.

**Human approval dashboard** — Flask serves a real-time web UI that displays every pending lead with its draft email, subject line, business type, and address. You approve or skip each one individually. An approve-all button is available when you want to move fast.

**Delivery and tracking** — Approved drafts must be queued and explicitly confirmed before Gmail SMTP can run. SQLite transactions, suppression checks, idempotency keys, and claim locks prevent duplicate or concurrent sends.

**Send safety** — Dry-run mode is ON by default; real delivery requires setting
`WINSTON_DRY_RUN=false` explicitly. Sends are rate-limited and daily-capped. Every
previously contacted address is suppressed, so historical recipients cannot be re-mailed
by accident.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| AI generation | Gemini free tier, local Ollama, optional Claude |
| Web scraping | Local requests + Python HTMLParser |
| Lead discovery | Google Places API |
| Email delivery | Gmail SMTP via smtplib |
| Persistence | SQLite with legacy JSON compatibility |
| Concurrency | Python threading |
| Environment management | python-dotenv |

---

## Setup

Clone the repository, create a virtual environment, and install dependencies:

```bash
git clone https://github.com/Kevin-Edwards57/winston
cd winston
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
```

Create a .env file in the root directory with the following keys:

```
ANTHROPIC_KEY=your_anthropic_api_key
GMAIL_ADDRESS=your_gmail_address
GMAIL_APP_PASSWORD=your_gmail_app_password
GOOGLE_PLACES_KEY=your_google_places_api_key
WINSTON_DATABASE=winston.db
```

Copy [.env.example](.env.example) as the starting point. Zero-Cost Mode tries Gemini's
configured free-tier model first and local Ollama second. Claude is skipped unless
`WINSTON_ZERO_COST_MODE=false` and `WINSTON_ENABLE_CLAUDE=true` are both explicitly set.

Before first run, migrate the legacy JSON data into SQLite. The command creates a
fresh timestamped JSON backup and an audit report; it does not modify the JSON files.

```bash
python3 -m winston.migration --database winston.db
```

Run Winston:

```bash
./start.sh
```

That kills any previous run, starts the server, waits until it answers, and opens the
dashboard. To stop it: `./stop.sh`

The port defaults to 5001 because macOS ControlCenter (AirPlay Receiver) occupies 5000.
Override with `WINSTON_PORT`.

<details>
<summary>Running it manually</summary>


```bash
python3 winston_app.py
```

Then open http://localhost:5001

</details>

Check application/database health at http://localhost:5000/health.

Run the safety and migration tests with:

```bash
python3 -m unittest discover -v
```

## Current safety model

The incremental SQLite workflow enforces `Draft → Reviewed → Approved → Queued →
Confirmed → Sent`. Approval no longer sends email. A queued send must receive a
separate explicit confirmation, and transactional locks plus idempotency keys prevent
the same job from being claimed twice.

Dry-run mode defaults to ON. `send_email_fn` refuses to touch SMTP unless
`WINSTON_DRY_RUN=false` is set explicitly, and it independently re-checks suppression
before every delivery. Sends are spaced by `WINSTON_SEND_MIN_INTERVAL` seconds and capped
at `WINSTON_SEND_MAX_PER_DAY` per day.

The legacy follow-up sender has been deleted. Re-introducing a second send path is treated
as a regression and is blocked by `tests/test_no_legacy_send.py`.

See [architecture](docs/ARCHITECTURE.md), [audit](docs/AUDIT.md),
[migration](docs/MIGRATION.md), and [PII remediation](docs/PII-REMEDIATION.md).

---

## Dashboard Features

The dashboard gives you full visibility and control over the pipeline in real time.

The leads panel shows every pending business with a draft email ready for review. You can approve individual leads, skip ones that are not a fit, or approve everything in the queue at once. Each card shows the business name, email, type, address, subject line, and the full email body.

The sent panel tracks every email that has gone out with delivery dates and follow-up status indicators.

The activity log streams what Winston is doing in real time with color-coded entries for wins, discoveries, and errors.

Winston also has a built-in chat interface powered by the same controlled provider router.

---

## Business Categories Covered

Jamaican restaurants, Caribbean restaurants, general restaurants in high-density Caribbean neighborhoods, barbershops, hair salons, nail salons, cleaning services, catering companies, auto repair shops, photographers, gyms, daycares, accountants, florists, plumbers, electricians, dentists, real estate agencies, and tutoring centers.

---

## Project Context

Winston is one of four products in the YardLink Studio ecosystem. It was built to drive client acquisition for YardLink Studio's web and AI services offerings targeting small businesses in the NYC and Long Island market.

Built by Kevin Edwards — YardLink Studio
