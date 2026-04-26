# Winston

Winston is an AI-powered B2B outreach automation tool built for YardLink Studio. It discovers local business leads across NYC and Long Island, scrapes their websites for contact information, generates personalized cold emails using Claude AI, and delivers them through a human-in-the-loop approval dashboard.

---

![Winston Dashboard](winston_dashboard.png)

## Live Stats

| Metric | Count |
|---|---|
| Leads discovered | 1,149 |
| Emails sent | 131 |
| Follow-ups delivered | 1 |
| Business categories covered | 15+ |
| Geographic areas covered | All 5 boroughs + Nassau and Suffolk County |

---

## What Winston Does

Winston runs a full outreach pipeline from lead discovery to email delivery without manual searching. You point it at a market, it finds businesses, scrapes their websites, writes a personalized pitch using Claude, and queues everything up for your review before anything gets sent. Nothing goes out without your approval.

It also handles follow-ups automatically. After a set window, Winston drafts and sends a follow-up to any business that never replied to the first email.

---

## Architecture

```
Google Places API  →  Firecrawl  →  Email Extractor  →  Claude API
                                                              ↓
JSON Persistence  ←  Follow-up Scheduler  ←  Gmail SMTP  ←  Flask Dashboard
```

**Lead discovery** — Winston queries Google Places across 15+ business categories including Jamaican and Caribbean restaurants, barbershops, salons, auto repair shops, dentists, real estate agencies, and more. It covers all five New York City boroughs plus Nassau and Suffolk County on Long Island.

**Website scraping** — Firecrawl visits each business website and pulls the raw HTML. A regex extractor parses out email addresses and filters them through a blocklist that removes platform emails from Wix, Squarespace, Shopify, Yelp, Google, and similar services to ensure only real business contacts get through.

**Email generation** — Each lead gets passed to Claude with business-specific context. Claude writes a personalized cold email tailored to that business type, rotating across six A/B tested subject lines. Winston's own personality shows up in the chat interface embedded in the dashboard.

**Human approval dashboard** — Flask serves a real-time web UI that displays every pending lead with its draft email, subject line, business type, and address. You approve or skip each one individually. An approve-all button is available when you want to move fast.

**Delivery and tracking** — Approved emails go out through Gmail SMTP. Every sent email is logged with a timestamp, the original draft, and a follow-up status flag. Stats persist across sessions in JSON files.

**Follow-up scheduler** — A background thread runs on startup and checks every previously emailed contact. Businesses that did not reply within the configured window automatically receive a follow-up email drafted by Claude.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| AI email generation | Anthropic Claude API (claude-sonnet-4-6) |
| Web scraping | Firecrawl |
| Lead discovery | Google Places API |
| Email delivery | Gmail SMTP via smtplib |
| Persistence | JSON flat files |
| Concurrency | Python threading |
| Environment management | python-dotenv |

---

## Setup

Clone the repository and install dependencies:

```bash
git clone https://github.com/Kevin-Edwards57/winston
cd winston
pip install -r requirements.txt
```

Create a .env file in the root directory with the following keys:

```
FIRECRAWL_KEY=your_firecrawl_api_key
ANTHROPIC_KEY=your_anthropic_api_key
GMAIL_ADDRESS=your_gmail_address
GMAIL_APP_PASSWORD=your_gmail_app_password
GOOGLE_PLACES_KEY=your_google_places_api_key
```

Run Winston:

```bash
python winston_app.py
```

Open the dashboard at http://localhost:5000

---

## Dashboard Features

The dashboard gives you full visibility and control over the pipeline in real time.

The leads panel shows every pending business with a draft email ready for review. You can approve individual leads, skip ones that are not a fit, or approve everything in the queue at once. Each card shows the business name, email, type, address, subject line, and the full email body.

The sent panel tracks every email that has gone out with delivery dates and follow-up status indicators.

The activity log streams what Winston is doing in real time with color-coded entries for wins, discoveries, and errors.

Winston also has a built-in chat interface powered by the Claude API where you can ask questions, check stats, or tell Winston to adjust its approach.

---

## Business Categories Covered

Jamaican restaurants, Caribbean restaurants, general restaurants in high-density Caribbean neighborhoods, barbershops, hair salons, nail salons, cleaning services, catering companies, auto repair shops, photographers, gyms, daycares, accountants, florists, plumbers, electricians, dentists, real estate agencies, and tutoring centers.

---

## Project Context

Winston is one of four products in the YardLink Studio ecosystem. It was built to drive client acquisition for YardLink Studio's web and AI services offerings targeting small businesses in the NYC and Long Island market.

Built by Kevin Edwards — YardLink Studio
