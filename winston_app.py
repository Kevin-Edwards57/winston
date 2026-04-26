from flask import Flask, render_template_string, request, jsonify, Response
import anthropic
import json
import os
import re
import smtplib
import time
import threading
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from firecrawl import FirecrawlApp
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ============================================================
# KEYS — stored in .env file, never hardcode these
# ============================================================
FIRECRAWL_KEY        = os.getenv("FIRECRAWL_KEY")
ANTHROPIC_KEY        = os.getenv("ANTHROPIC_KEY")
GMAIL_ADDRESS        = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD   = os.getenv("GMAIL_APP_PASSWORD")
GOOGLE_PLACES_KEY    = os.getenv("GOOGLE_PLACES_KEY")
firecrawl            = FirecrawlApp(api_key=FIRECRAWL_KEY)
claude_client        = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ============================================================
# PERSISTENT STORAGE FILES
# ============================================================
EMAILED_FILE = "emailed.json"
LEADS_FILE = "leads.json"
FOLLOWUP_FILE = "followups.json"
STATS_FILE = "stats.json"

# ============================================================
# GOOGLE PLACES SEARCH QUERIES
# Format: (keyword, location) — covers all 5 boroughs + LI
# ============================================================
PLACE_SEARCHES = [
    # ── JAMAICAN / CARIBBEAN ──
    ("jamaican restaurant", "Queens, New York"),
    ("jamaican restaurant", "Brooklyn, New York"),
    ("jamaican restaurant", "Bronx, New York"),
    ("jamaican restaurant", "Manhattan, New York"),
    ("jamaican restaurant", "Staten Island, New York"),
    ("jamaican restaurant", "Nassau County, New York"),
    ("jamaican restaurant", "Suffolk County, New York"),
    ("caribbean restaurant", "Queens, New York"),
    ("caribbean restaurant", "Brooklyn, New York"),
    ("caribbean restaurant", "Bronx, New York"),
    ("caribbean restaurant", "Nassau County, New York"),

    # ── RESTAURANTS ──
    ("restaurant", "Jamaica, Queens, New York"),
    ("restaurant", "Flatbush, Brooklyn, New York"),
    ("restaurant", "Bay Shore, New York"),
    ("restaurant", "Hempstead, New York"),
    ("restaurant", "Freeport, New York"),

    # ── BARBERSHOPS ──
    ("barbershop", "Queens, New York"),
    ("barbershop", "Brooklyn, New York"),
    ("barbershop", "Bronx, New York"),
    ("barbershop", "Nassau County, New York"),
    ("barbershop", "Suffolk County, New York"),

    # ── HAIR SALONS ──
    ("hair salon", "Queens, New York"),
    ("hair salon", "Brooklyn, New York"),
    ("hair salon", "Bronx, New York"),
    ("hair salon", "Long Island, New York"),

    # ── NAIL SALONS ──
    ("nail salon", "Queens, New York"),
    ("nail salon", "Brooklyn, New York"),
    ("nail salon", "Long Island, New York"),

    # ── CLEANING SERVICES ──
    ("cleaning service", "Queens, New York"),
    ("cleaning service", "Brooklyn, New York"),
    ("cleaning service", "Long Island, New York"),
    ("cleaning service", "Bronx, New York"),

    # ── CATERING ──
    ("catering", "Queens, New York"),
    ("catering", "Brooklyn, New York"),
    ("catering", "Long Island, New York"),

    # ── AUTO REPAIR ──
    ("auto repair", "Queens, New York"),
    ("auto repair", "Brooklyn, New York"),
    ("auto repair", "Long Island, New York"),

    # ── PHOTOGRAPHERS ──
    ("photographer", "Queens, New York"),
    ("photographer", "Brooklyn, New York"),
    ("photographer", "Long Island, New York"),

    # ── GYMS / FITNESS ──
    ("gym", "Queens, New York"),
    ("gym", "Brooklyn, New York"),
    ("gym", "Long Island, New York"),

    # ── DAYCARE ──
    ("daycare", "Queens, New York"),
    ("daycare", "Brooklyn, New York"),
    ("daycare", "Long Island, New York"),

    # ── ACCOUNTANTS ──
    ("accountant", "Queens, New York"),
    ("accountant", "Brooklyn, New York"),
    ("accountant", "Long Island, New York"),

    # ── FLORISTS ──
    ("florist", "Queens, New York"),
    ("florist", "Brooklyn, New York"),
    ("florist", "Long Island, New York"),

    # ── PLUMBERS ──
    ("plumber", "Queens, New York"),
    ("plumber", "Long Island, New York"),

    # ── ELECTRICIANS ──
    ("electrician", "Queens, New York"),
    ("electrician", "Long Island, New York"),

    # ── DENTISTS ──
    ("dentist", "Queens, New York"),
    ("dentist", "Brooklyn, New York"),
    ("dentist", "Long Island, New York"),

    # ── REAL ESTATE ──
    ("real estate agency", "Queens, New York"),
    ("real estate agency", "Long Island, New York"),

    # ── TUTORING ──
    ("tutoring", "Queens, New York"),
    ("tutoring", "Long Island, New York"),
]

# Rotating subject lines — A/B test these
EMAIL_SUBJECTS = [
    "Your website could be working harder for you",
    "Quick idea for {name}",
    "More customers for {name} — here's how",
    "Does {name} have a 24/7 AI assistant yet?",
    "I noticed something about {name}'s website",
    "This could save {name} 10 hours a week",
]

# In-memory state
state = {
    "businesses": [],
    "emails_sent": 0,
    "status": "idle",
    "log": [],
    "pending": [],
}

# ============================================================
# PERSISTENCE HELPERS
# ============================================================
def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath) as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

def load_emailed():
    return load_json(EMAILED_FILE, [])

def save_emailed(emailed):
    save_json(EMAILED_FILE, emailed)

def load_saved_leads():
    return load_json(LEADS_FILE, [])

def save_leads(leads):
    save_json(LEADS_FILE, leads)

def load_followups():
    return load_json(FOLLOWUP_FILE, [])

def save_followups(followups):
    save_json(FOLLOWUP_FILE, followups)

def load_stats():
    return load_json(STATS_FILE, {"emails_sent": 0, "leads_found": 0, "followups_sent": 0})

def save_stats(stats):
    save_json(STATS_FILE, stats)

# ============================================================
# LOGGING
# ============================================================
def log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    state["log"].append(entry)
    if len(state["log"]) > 200:
        state["log"] = state["log"][-200:]

# ============================================================
# SCRAPING
# ============================================================
# ============================================================
# EMAIL BLOCKLIST — skip these non-business emails
# ============================================================
EMAIL_BLOCKLIST = [
    'noreply', 'no-reply', 'yelp', 'google', 'example', 'sentry',
    'facebook', 'privacy', 'support@sentry', 'info@sentry', 'wix',
    'squarespace', 'shopify', 'godaddy', 'wordpress', 'mailchimp',
    'donotreply', 'test@', 'admin@', 'webmaster@', 'postmaster@',
    'schema', 'amazonaws', 'cloudflare', 'netlify', '.png', '.jpg'
]

def is_valid_email(email):
    """Check if email is a real business contact email."""
    email_lower = email.lower()
    if any(x in email_lower for x in EMAIL_BLOCKLIST):
        return False
    if len(email) > 80:
        return False
    return True

def extract_emails_from_html(html):
    """Pull emails from raw HTML using regex."""
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
    return [e for e in emails if is_valid_email(e)]

def scrape_with_bs4(url):
    """
    Primary scraper — free, local, no credits needed.
    Uses requests + BeautifulSoup to fetch and parse HTML.
    Works great for small business sites on Wix, Squarespace, WordPress.
    """
    try:
        from bs4 import BeautifulSoup
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code != 200:
            return ""
        soup = BeautifulSoup(res.text, "lxml")

        # Check mailto links first — most reliable source
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if href.startswith("mailto:"):
                email = href.replace("mailto:", "").split("?")[0].strip()
                if email and is_valid_email(email):
                    return email

        # Fall back to regex scan of full page text
        emails = extract_emails_from_html(res.text)
        if emails:
            return emails[0]

        return ""
    except Exception as e:
        return ""

def scrape_with_firecrawl(url):
    """
    Fallback scraper — uses Firecrawl for JS-heavy or bot-protected sites.
    Only called when BeautifulSoup returns nothing.
    """
    try:
        result = firecrawl.scrape(url)
        content = result.markdown if hasattr(result, "markdown") else str(result)
        emails = extract_emails_from_html(content)
        return emails[0] if emails else ""
    except Exception as e:
        log(f"Firecrawl fallback error: {e}")
        return ""

def scrape(url):
    """Legacy scrape function — kept for compatibility."""
    return scrape_with_bs4(url) or scrape_with_firecrawl(url)

def find_email(website_url):
    """
    Hybrid email finder:
    1. Try BeautifulSoup on homepage + contact pages (free, instant)
    2. Fall back to Firecrawl only if BS4 finds nothing
    """
    pages_to_check = [
        website_url,
        website_url.rstrip("/") + "/contact",
        website_url.rstrip("/") + "/contact-us",
        website_url.rstrip("/") + "/about",
        website_url.rstrip("/") + "/about-us",
    ]

    # ── PASS 1: BeautifulSoup (free) ──
    for url in pages_to_check:
        try:
            email = scrape_with_bs4(url)
            if email:
                return email
            time.sleep(0.5)
        except:
            continue

    # ── PASS 2: Firecrawl fallback (credits) ──
    for url in pages_to_check[:2]:  # only try homepage + /contact to save credits
        try:
            email = scrape_with_firecrawl(url)
            if email:
                return email
            time.sleep(1)
        except:
            continue

    return None

# ============================================================
# EMAIL WRITING — smarter prompts, rotating subjects
# ============================================================
def get_subject(business_name, index=0):
    template = EMAIL_SUBJECTS[index % len(EMAIL_SUBJECTS)]
    return template.replace("{name}", business_name)

def write_email(business):
    try:
        biz_name = business.get('name', 'there')
        biz_type = business.get('type', 'business')
        biz_location = business.get('address', 'NYC')

        prompt = f"""Write a cold outreach email from YardLink Studio to {biz_name}, a {biz_type} in {biz_location}.

YardLink Studio (yardlinkstudio.com) is a NYC digital agency that builds:
- Fast, modern websites
- AI chatbots that handle customer questions 24/7 (booking, FAQs, pricing)
- Automated tools that save business owners hours every week

Rules:
- Under 130 words
- Sound like a real person, not a marketing bot
- Reference something specific about being a {biz_type} in {biz_location}
- One clear pain point (e.g. missing calls after hours, outdated website, no online booking)
- One specific solution we offer
- End with: "Would you be open to a 15-minute call this week?"
- Sign off: — The YardLink Studio Team | yardlinkstudio.com
- Plain text only, no markdown, no bullet points"""

        msg = claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()
    except Exception as e:
        log(f"Email write error: {e}")
        return None

def write_followup_email(business, original_body):
    try:
        prompt = f"""Write a short follow-up cold email to {business.get('name')}, a {business.get('type')} in {business.get('address','NYC')}.

This is a follow-up to a previous email from YardLink Studio (yardlinkstudio.com) about building them a website or AI chatbot.
The original email said: {original_body[:300]}

Rules:
- Under 80 words
- Casual, not pushy — just a gentle bump
- Acknowledge they're busy
- One new hook or stat (e.g. "Most small businesses miss 40% of calls after hours")
- End with: "Still open to a quick chat if the timing works."
- Sign: — YardLink Studio | yardlinkstudio.com
- Plain text only"""

        msg = claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=250,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()
    except Exception as e:
        log(f"Followup write error: {e}")
        return None

# ============================================================
# EMAIL SENDING — with rate limiting
# ============================================================
def send_email_fn(to_email, business_name, body, subject=None):
    try:
        if not subject:
            subject = f"Quick idea for {business_name}"
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        return True
    except Exception as e:
        log(f"Send error: {e}")
        return False

# ============================================================
# FOLLOW-UP SCHEDULER
# ============================================================
def check_and_send_followups():
    followups = load_followups()
    now = datetime.now()
    updated = []
    sent_count = 0

    for entry in followups:
        sent_date = datetime.fromisoformat(entry["sent_date"])
        days_since = (now - sent_date).days

        if entry.get("followup_sent"):
            updated.append(entry)
            continue

        if days_since >= 3:
            followup_body = write_followup_email(entry, entry.get("original_body", ""))
            if followup_body:
                subject = f"Re: {entry.get('subject', 'Quick idea for ' + entry['name'])}"
                success = send_email_fn(entry["email"], entry["name"], followup_body, subject)
                if success:
                    entry["followup_sent"] = True
                    entry["followup_date"] = now.isoformat()
                    sent_count += 1
                    log(f"Follow-up sent to {entry['name']} 📬")
                    stats = load_stats()
                    stats["followups_sent"] = stats.get("followups_sent", 0) + 1
                    save_stats(stats)
                    time.sleep(10)

        updated.append(entry)

    save_followups(updated)
    return sent_count

def followup_scheduler():
    while True:
        time.sleep(3600)
        try:
            n = check_and_send_followups()
            if n > 0:
                log(f"Follow-up scheduler sent {n} emails 📬")
        except Exception as e:
            log(f"Follow-up scheduler error: {e}")

# ============================================================
# GOOGLE PLACES API — LEAD DISCOVERY
# ============================================================
def google_places_search(keyword, location, max_results=20):
    """
    Use Google Places Text Search to find real businesses.
    Returns list of dicts: {name, address, website, phone, types, place_id}
    """
    businesses = []
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    query = f"{keyword} in {location}"
    params = {
        "query": query,
        "key": GOOGLE_PLACES_KEY,
        "type": "establishment"
    }

    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            log(f"Places API error: {data.get('status')} — {data.get('error_message','')}")
            return []

        results = data.get("results", [])[:max_results]

        for place in results:
            place_id = place.get("place_id")
            name     = place.get("name", "")
            address  = place.get("formatted_address", location)
            types    = ", ".join(place.get("types", [])[:3]).replace("_", " ")

            # Get website + phone from Place Details
            website, phone = get_place_details(place_id)

            businesses.append({
                "name":    name,
                "address": address,
                "type":    keyword,
                "types":   types,
                "website": website,
                "phone":   phone,
                "place_id": place_id,
            })

        return businesses

    except Exception as e:
        log(f"Places search error: {e}")
        return []


def get_place_details(place_id):
    """Fetch website and phone from a Place ID."""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "website,formatted_phone_number",
        "key": GOOGLE_PLACES_KEY,
    }
    try:
        res = requests.get(url, params=params, timeout=8)
        result = res.json().get("result", {})
        website = result.get("website", "")
        phone   = result.get("formatted_phone_number", "")
        return website, phone
    except:
        return "", ""


# ============================================================
# MAIN SCAN — Google Places powered
# ============================================================
def run_scan(searches):
    state["status"] = "scanning"
    log("Aight Kevin, new pipeline is live. Let's get it.")
    emailed  = load_emailed()
    subject_index = 0
    total_leads   = 0

    for (keyword, location) in searches:
        if state["status"] == "stopped":
            break

        log(f"Scanning: {keyword} in {location}")
        businesses = google_places_search(keyword, location, max_results=20)

        if not businesses:
            log(f"No results: {keyword} in {location}")
            time.sleep(1)
            continue

        log(f"Found {len(businesses)} places for '{keyword}' in {location}")

        for b in businesses:
            if state["status"] == "stopped":
                break

            name    = b.get("name", "Unknown")
            website = b.get("website", "")

            # Skip if no website — nowhere to scrape for email
            if not website:
                log(f"No website: {name}, skipping")
                continue

            # Scrape their website for a real contact email
            log(f"Checking website for {name}...")
            email = find_email(website)

            if not email:
                log(f"No email found: {name}")
                continue

            if email in emailed:
                log(f"Already contacted: {name}")
                continue

            log(f"Lead locked: {name} — {email} 💰")
            subject = get_subject(name, subject_index)
            subject_index += 1

            body = write_email(b)
            if body:
                b["email"]   = email
                b["draft"]   = body
                b["subject"] = subject
                state["pending"].append(b)
                total_leads += 1

                stats = load_stats()
                stats["leads_found"] = stats.get("leads_found", 0) + 1
                save_stats(stats)

                log(f"Draft ready: {name} 🔥")

            time.sleep(1.5)  # be respectful to Firecrawl rate limits

        time.sleep(1)

    state["status"] = "idle"
    log(f"Done scanning. {total_leads} leads ready for you Kevin.")

# ============================================================
# HTML FRONTEND
# ============================================================
HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Winston — YardLink Studio</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&family=DM+Sans:ital,wght@0,300;0,400;0,600;0,700;1,400&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #030303;
  --surface: #0a0a0a;
  --card: #111;
  --card2: #141414;
  --border: rgba(255,255,255,0.05);
  --border2: rgba(255,255,255,0.09);
  --green: #00FF87;
  --green-dim: rgba(0,255,135,0.12);
  --gold: #FFD100;
  --gold-dim: rgba(255,209,0,0.1);
  --red: #FF4D4D;
  --red-dim: rgba(255,77,77,0.08);
  --blue: #4D9FFF;
  --white: #EDE8DF;
  --gray: #444;
  --dim: rgba(237,232,223,0.35);
  --dim2: rgba(237,232,223,0.55);
}

* { margin:0; padding:0; box-sizing:border-box; }
html, body { height:100%; overflow:hidden; }

body {
  background: var(--bg);
  color: var(--white);
  font-family: 'DM Sans', sans-serif;
  display: flex;
  flex-direction: column;
}

body::before {
  content:'';
  position:fixed;
  inset:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.025'/%3E%3C/svg%3E");
  pointer-events:none;
  z-index:9999;
}

body::after {
  content:'';
  position:fixed;
  inset:0;
  background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.03) 2px, rgba(0,0,0,0.03) 4px);
  pointer-events:none;
  z-index:9998;
}

header {
  background: var(--surface);
  border-bottom: 1px solid var(--border2);
  padding: 0 28px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  position: relative;
}

header::after {
  content:'';
  position:absolute;
  bottom:0; left:0; right:0;
  height:1px;
  background: linear-gradient(90deg, transparent, var(--green), transparent);
  opacity:0.3;
}

.header-left { display:flex; align-items:center; gap:14px; }

.logo-mark {
  width:40px; height:40px;
  border-radius:10px;
  background: linear-gradient(135deg, #00FF87 0%, #00C060 100%);
  display:flex; align-items:center; justify-content:center;
  font-size:1.2rem;
  box-shadow: 0 0 24px rgba(0,255,135,0.25), inset 0 1px 0 rgba(255,255,255,0.2);
  animation: pulse-logo 4s ease-in-out infinite;
  flex-shrink:0;
}
@keyframes pulse-logo {
  0%,100%{box-shadow:0 0 24px rgba(0,255,135,0.25),inset 0 1px 0 rgba(255,255,255,0.2);}
  50%{box-shadow:0 0 40px rgba(0,255,135,0.45),inset 0 1px 0 rgba(255,255,255,0.2);}
}

.logo-text h1 {
  font-family:'Bebas Neue',sans-serif;
  font-size:1.35rem;
  letter-spacing:3px;
  color:var(--green);
  line-height:1;
}
.logo-text p {
  font-size:0.62rem;
  color:var(--dim);
  letter-spacing:2px;
  text-transform:uppercase;
}

.header-center {
  position:absolute;
  left:50%; transform:translateX(-50%);
  display:flex; align-items:center; gap:24px;
}

.stat-pill {
  display:flex; align-items:center; gap:8px;
  background: var(--card);
  border:1px solid var(--border2);
  border-radius:100px;
  padding:5px 14px 5px 10px;
}
.stat-pill-icon { font-size:0.85rem; }
.stat-pill-info { display:flex; flex-direction:column; line-height:1; }
.stat-pill-num {
  font-family:'Bebas Neue',sans-serif;
  font-size:1.1rem;
  color:var(--gold);
  line-height:1.1;
}
.stat-pill-label { font-size:0.58rem; color:var(--dim); letter-spacing:1px; text-transform:uppercase; }

.status-badge {
  display:flex; align-items:center; gap:8px;
  background:var(--green-dim);
  border:1px solid rgba(0,255,135,0.2);
  padding:5px 14px;
  border-radius:100px;
  font-size:0.7rem;
  font-weight:700;
  letter-spacing:1.5px;
  text-transform:uppercase;
  color:var(--green);
}
.status-dot {
  width:6px; height:6px; border-radius:50%;
  background:var(--green);
  animation:blink 1.4s ease-in-out infinite;
}
@keyframes blink{0%,100%{opacity:1;}50%{opacity:0.2;}}

.main {
  display:grid;
  grid-template-columns: 300px 1fr 300px;
  flex:1;
  overflow:hidden;
}

.panel {
  display:flex; flex-direction:column;
  overflow:hidden;
  border-right:1px solid var(--border);
}
.panel:last-child { border-right:none; border-left:1px solid var(--border); }

.panel-header {
  padding:12px 16px;
  border-bottom:1px solid var(--border);
  display:flex; align-items:center; justify-content:space-between;
  flex-shrink:0;
  background:var(--surface);
}
.panel-title {
  font-family:'Bebas Neue',sans-serif;
  font-size:0.85rem; letter-spacing:3px; color:var(--dim2);
}
.panel-badge {
  font-family:'Space Mono',monospace;
  font-size:0.65rem; font-weight:700;
  padding:2px 9px; border-radius:100px;
  background:var(--green-dim);
  border:1px solid rgba(0,255,135,0.2);
  color:var(--green);
}
.panel-badge.gold {
  background:var(--gold-dim);
  border-color:rgba(255,209,0,0.2);
  color:var(--gold);
}

.panel-body { flex:1; overflow-y:auto; padding:10px; }
.panel-body::-webkit-scrollbar { width:2px; }
.panel-body::-webkit-scrollbar-thumb { background:rgba(0,255,135,0.15); border-radius:2px; }

.lead-card {
  background:var(--card);
  border:1px solid var(--border);
  border-radius:10px;
  padding:12px;
  margin-bottom:8px;
  transition:all 0.2s;
  position:relative;
  overflow:hidden;
}
.lead-card::before {
  content:'';
  position:absolute;
  left:0; top:0; bottom:0; width:3px;
  background:var(--green);
  border-radius:3px 0 0 3px;
  opacity:0;
  transition:opacity 0.2s;
}
.lead-card:hover { border-color:var(--border2); }
.lead-card:hover::before { opacity:1; }

.lead-name { font-weight:700; font-size:0.84rem; margin-bottom:3px; }
.lead-email {
  font-family:'Space Mono',monospace;
  font-size:0.64rem; color:var(--green); margin-bottom:3px;
}
.lead-meta { font-size:0.65rem; color:var(--dim); margin-bottom:8px; }
.lead-subject {
  font-size:0.68rem; color:var(--gold);
  margin-bottom:6px; font-style:italic;
}

.email-preview {
  background:rgba(0,255,135,0.03);
  border:1px solid rgba(0,255,135,0.08);
  border-radius:6px;
  padding:8px 10px;
  font-size:0.7rem; color:var(--dim);
  line-height:1.55;
  margin-bottom:8px;
  max-height:80px; overflow:hidden;
  cursor:pointer;
  transition:max-height 0.3s;
}
.email-preview.expanded { max-height:300px; overflow-y:auto; }
.email-preview:hover { border-color:rgba(0,255,135,0.15); }

.lead-actions { display:flex; gap:6px; }

.btn-approve, .btn-reject {
  flex:1; padding:6px;
  border-radius:6px; border:none;
  font-family:'DM Sans',sans-serif;
  font-size:0.68rem; font-weight:700;
  letter-spacing:1px; text-transform:uppercase;
  cursor:pointer; transition:all 0.15s;
}
.btn-approve {
  background:var(--green-dim);
  border:1px solid rgba(0,255,135,0.25);
  color:var(--green);
}
.btn-approve:hover { background:rgba(0,255,135,0.22); transform:scale(1.02); }
.btn-reject {
  background:var(--red-dim);
  border:1px solid rgba(255,77,77,0.2);
  color:var(--red);
}
.btn-reject:hover { background:rgba(255,77,77,0.15); }

.sent-card {
  background:var(--card);
  border:1px solid var(--border);
  border-radius:8px;
  padding:10px 12px;
  margin-bottom:6px;
  display:flex; align-items:center; gap:10px;
}
.sent-dot {
  width:7px; height:7px; border-radius:50%;
  background:var(--green); flex-shrink:0;
}
.sent-dot.followup { background:var(--blue); }
.sent-info { flex:1; min-width:0; }
.sent-name { font-size:0.78rem; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.sent-email { font-family:'Space Mono',monospace; font-size:0.6rem; color:var(--dim); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.sent-date { font-size:0.6rem; color:var(--dim); flex-shrink:0; }

.center-panel {
  display:flex; flex-direction:column; overflow:hidden;
}

.controls {
  padding:10px 16px;
  border-bottom:1px solid var(--border);
  display:flex; gap:8px; flex-shrink:0;
  background:var(--surface);
}
.ctrl-btn {
  flex:1; padding:9px;
  border-radius:7px;
  border:1px solid var(--border2);
  background:var(--card);
  color:var(--white);
  font-family:'DM Sans',sans-serif;
  font-size:0.72rem; font-weight:700;
  letter-spacing:1px; text-transform:uppercase;
  cursor:pointer; transition:all 0.15s;
}
.ctrl-btn:hover { background:var(--card2); border-color:rgba(255,255,255,0.12); }
.ctrl-btn.green { border-color:rgba(0,255,135,0.3); color:var(--green); }
.ctrl-btn.green:hover { background:var(--green-dim); }
.ctrl-btn.red { border-color:rgba(255,77,77,0.3); color:var(--red); }
.ctrl-btn.red:hover { background:var(--red-dim); }
.ctrl-btn.gold { border-color:rgba(255,209,0,0.3); color:var(--gold); }
.ctrl-btn.gold:hover { background:var(--gold-dim); }

.chat-area {
  flex:1; overflow-y:auto;
  padding:20px;
  display:flex; flex-direction:column; gap:14px;
}
.chat-area::-webkit-scrollbar { width:2px; }
.chat-area::-webkit-scrollbar-thumb { background:rgba(0,255,135,0.12); }

.msg {
  display:flex; gap:10px;
  animation:msgIn 0.25s ease both;
}
@keyframes msgIn{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:translateY(0);}}

.msg-av {
  width:30px; height:30px; border-radius:50%; flex-shrink:0;
  display:flex; align-items:center; justify-content:center;
  font-size:0.85rem;
  background:linear-gradient(135deg, #00FF87, #00A854);
  box-shadow:0 0 12px rgba(0,255,135,0.2);
}
.msg-user .msg-av { background:linear-gradient(135deg, #FFD100, #FF9500); order:2; }

.msg-bubble {
  background:var(--card);
  border:1px solid var(--border);
  border-radius:14px; border-bottom-left-radius:3px;
  padding:10px 14px;
  font-size:0.84rem; line-height:1.6;
  max-width:82%;
}
.msg-user .msg-bubble {
  background:rgba(255,209,0,0.07);
  border-color:rgba(255,209,0,0.12);
  border-bottom-left-radius:14px;
  border-bottom-right-radius:3px;
  margin-left:auto;
}
.msg-user { flex-direction:row-reverse; }

.chat-input-row {
  border-top:1px solid var(--border);
  padding:12px 16px;
  display:flex; gap:10px; align-items:center;
  flex-shrink:0;
  background:var(--surface);
}
.chat-inp {
  flex:1;
  background:var(--card);
  border:1px solid var(--border2);
  border-radius:10px;
  padding:10px 14px;
  color:var(--white);
  font-family:'DM Sans',sans-serif;
  font-size:0.84rem;
  outline:none;
  transition:border-color 0.2s;
}
.chat-inp:focus { border-color:rgba(0,255,135,0.35); }
.chat-inp::placeholder { color:var(--gray); }

.send-btn {
  width:40px; height:40px; border-radius:50%;
  background:var(--green); border:none; cursor:pointer;
  display:flex; align-items:center; justify-content:center;
  transition:all 0.15s; flex-shrink:0;
}
.send-btn:hover { background:var(--gold); transform:scale(1.06); }

.log-entry {
  font-family:'Space Mono',monospace;
  font-size:0.62rem; color:var(--dim);
  padding:5px 0;
  border-bottom:1px solid rgba(255,255,255,0.02);
  line-height:1.5;
  word-break:break-word;
}
.log-entry.win { color:var(--green); }
.log-entry.info { color:var(--blue); opacity:0.8; }

.tabs {
  display:flex; gap:0;
  border-bottom:1px solid var(--border);
  flex-shrink:0; background:var(--surface);
}
.tab-btn {
  flex:1; padding:9px;
  background:none; border:none;
  font-family:'DM Sans',sans-serif;
  font-size:0.7rem; font-weight:700;
  letter-spacing:1px; text-transform:uppercase;
  color:var(--dim); cursor:pointer;
  border-bottom:2px solid transparent;
  transition:all 0.15s;
}
.tab-btn.active { color:var(--green); border-bottom-color:var(--green); }

.tab-panel { display:none; flex:1; overflow:hidden; flex-direction:column; }
.tab-panel.active { display:flex; }

.empty {
  text-align:center; padding:40px 20px;
  color:var(--gray); font-size:0.78rem; line-height:1.8;
}
.empty-icon { font-size:2rem; margin-bottom:10px; display:block; opacity:0.5; }

.approve-strip {
  margin:0 0 10px;
  padding:8px 12px;
  background:var(--green-dim);
  border:1px solid rgba(0,255,135,0.15);
  border-radius:8px;
  display:flex; align-items:center; justify-content:space-between;
  font-size:0.72rem; color:var(--green);
}
.approve-all-btn {
  background:var(--green); color:#000;
  border:none; border-radius:5px;
  padding:4px 12px;
  font-family:'DM Sans',sans-serif;
  font-size:0.68rem; font-weight:700;
  letter-spacing:1px; text-transform:uppercase;
  cursor:pointer; transition:all 0.15s;
}
.approve-all-btn:hover { background:#00e07a; }

.followup-banner {
  margin-bottom:8px;
  padding:8px 12px;
  background:rgba(77,159,255,0.08);
  border:1px solid rgba(77,159,255,0.15);
  border-radius:8px;
  font-size:0.7rem; color:var(--blue);
}
</style>
</head>
<body>

<header>
  <div class="header-left">
    <div class="logo-mark">W</div>
    <div class="logo-text">
      <h1>WINSTON</h1>
      <p>YardLink Studio · Outreach Engine</p>
    </div>
  </div>

  <div class="header-center">
    <div class="stat-pill">
      <span class="stat-pill-icon">📤</span>
      <div class="stat-pill-info">
        <span class="stat-pill-num" id="emails-sent">0</span>
        <span class="stat-pill-label">Sent</span>
      </div>
    </div>
    <div class="stat-pill">
      <span class="stat-pill-icon">🎯</span>
      <div class="stat-pill-info">
        <span class="stat-pill-num" id="leads-count">0</span>
        <span class="stat-pill-label">Leads</span>
      </div>
    </div>
    <div class="stat-pill">
      <span class="stat-pill-icon">📬</span>
      <div class="stat-pill-info">
        <span class="stat-pill-num" id="followups-sent">0</span>
        <span class="stat-pill-label">Follow-ups</span>
      </div>
    </div>
  </div>

  <div class="status-badge">
    <div class="status-dot"></div>
    <span id="status-text">Online</span>
  </div>
</header>

<div class="main">

  <div class="panel">
    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab(\'leads\',this)">Pending</button>
      <button class="tab-btn" onclick="switchTab(\'sent\',this)">Sent</button>
    </div>

    <div class="tab-panel active" id="tab-leads">
      <div class="panel-body" id="leads-panel">
        <div class="empty">
          <span class="empty-icon">🎯</span>
          No leads yet Kevin.<br>Hit Scan and let's find some.
        </div>
      </div>
    </div>

    <div class="tab-panel" id="tab-sent">
      <div class="panel-body" id="sent-panel">
        <div class="empty">
          <span class="empty-icon">📤</span>
          No emails sent yet.
        </div>
      </div>
    </div>
  </div>

  <div class="center-panel">
    <div class="controls">
      <button class="ctrl-btn green" onclick="startScan()">▶ Scan</button>
      <button class="ctrl-btn red" onclick="stopScan()">■ Stop</button>
      <button class="ctrl-btn gold" onclick="triggerFollowups()">📬 Follow-ups</button>
    </div>

    <div class="chat-area" id="chat">
      <div class="msg">
        <div class="msg-av">W</div>
        <div class="msg-bubble">
          What's good Kevin. I find businesses, pull their emails, and write the outreach. You approve, I send. Hit Scan when you\'re ready.
        </div>
      </div>
    </div>

    <div class="chat-input-row">
      <input class="chat-inp" id="chat-input" placeholder="Talk to Winston..." onkeydown="if(event.key===\'Enter\')sendMsg()">
      <button class="send-btn" onclick="sendMsg()">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="#000"><path d="M2 21l21-9L2 3v7l15 2-15 2v7z"/></svg>
      </button>
    </div>
  </div>

  <div class="panel">
    <div class="panel-header">
      <div class="panel-title">Activity Log</div>
      <div class="panel-badge" id="log-count">0</div>
    </div>
    <div class="panel-body" id="log-panel">
      <div class="empty">
        <span class="empty-icon">📋</span>
        Waiting for activity...
      </div>
    </div>
  </div>

</div>

<script>
let pendingLeads = [];
let sentLeads = [];
let activeTab = \'leads\';

function switchTab(tab, el) {
  activeTab = tab;
  document.querySelectorAll(\'.tab-btn\').forEach(b => b.classList.remove(\'active\'));
  document.querySelectorAll(\'.tab-panel\').forEach(p => p.classList.remove(\'active\'));
  el.classList.add(\'active\');
  document.getElementById(\'tab-\' + tab).classList.add(\'active\');
}

function addMsg(text, isUser=false) {
  const chat = document.getElementById(\'chat\');
  const div = document.createElement(\'div\');
  div.className = \'msg\' + (isUser ? \' msg-user\' : \'\');
  div.innerHTML = `
    <div class="msg-av">${isUser ? 'K' : 'W'}</div>
    <div class="msg-bubble">${text}</div>
  `;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

async function sendMsg() {
  const inp = document.getElementById(\'chat-input\');
  const text = inp.value.trim();
  if (!text) return;
  inp.value = \'\';
  addMsg(text, true);
  const res = await fetch(\'/chat\', {
    method: \'POST\',
    headers: {\'Content-Type\':\'application/json\'},
    body: JSON.stringify({message: text})
  });
  const data = await res.json();
  addMsg(data.reply);
}

async function startScan() {
  addMsg("On it. Scanning NYC and Long Island now, give me a minute...");
  fetch(\'/scan\', {method:\'POST\'});
  document.getElementById(\'status-text\').textContent = \'Scanning...\';
  document.querySelector(\'.status-dot\').style.background = \'#FFD100\';
}

function stopScan() {
  fetch(\'/stop\', {method:\'POST\'});
  addMsg("Stopped. Check your pending leads Kevin.");
  document.getElementById(\'status-text\').textContent = \'Online\';
  document.querySelector(\'.status-dot\').style.background = \'var(--green)\';
}

async function triggerFollowups() {
  addMsg("Running follow-up check — any lead that didn\'t reply in 3+ days is getting a bump 📬");
  const res = await fetch(\'/followups\', {method:\'POST\'});
  const data = await res.json();
  addMsg(`Sent ${data.sent} follow-ups. We stay on them Kevin.`);
  document.getElementById(\'followups-sent\').textContent = data.total_followups;
  refreshAll();
}

async function approveLead(idx) {
  const res = await fetch(\'/approve\', {
    method:\'POST\',
    headers:{\'Content-Type\':\'application/json\'},
    body: JSON.stringify({index: idx})
  });
  const data = await res.json();
  if (data.success) {
    addMsg(`Sent to ${data.name}. That one\'s in the pipeline.`);
    document.getElementById(\'emails-sent\').textContent = data.total_sent;
    refreshAll();
  }
}

async function rejectLead(idx) {
  await fetch(\'/reject\', {
    method:\'POST\',
    headers:{\'Content-Type\':\'application/json\'},
    body: JSON.stringify({index: idx})
  });
  refreshAll();
}

async function approveAll() {
  const res = await fetch(\'/approve_all\', {method:\'POST\'});
  const data = await res.json();
  addMsg(`Sent ${data.sent} emails in one shot. Let\'s go Kevin.`);
  document.getElementById(\'emails-sent\').textContent = data.total_sent;
  refreshAll();
}

function togglePreview(el) {
  el.classList.toggle(\'expanded\');
}

async function refreshLeads() {
  const res = await fetch(\'/leads\');
  const data = await res.json();
  pendingLeads = data.leads;
  document.getElementById(\'leads-count\').textContent = data.total_found;

  const panel = document.getElementById(\'leads-panel\');
  if (pendingLeads.length === 0) {
    panel.innerHTML = \'<div class="empty"><span class="empty-icon">🎯</span>No pending leads.<br>Start a scan to find some!</div>\';
    return;
  }

  let html = \'\';
  if (pendingLeads.length > 1) {
    html += `<div class="approve-strip"><span>${pendingLeads.length} leads ready</span><button class="approve-all-btn" onclick="approveAll()">Send All</button></div>`;
  }
  html += pendingLeads.map((b, i) => `
    <div class="lead-card">
      <div class="lead-name">${b.name}</div>
      <div class="lead-email">${b.email}</div>
      <div class="lead-meta">${b.type || \'Business\'} · ${b.address || \'NYC\'}</div>
      ${b.subject ? `<div class="lead-subject">📧 "${b.subject}"</div>` : \'\'}
      <div class="email-preview" onclick="togglePreview(this)">${b.draft || \'Drafting...\'}</div>
      <div class="lead-actions">
        <button class="btn-approve" onclick="approveLead(${i})">✓ Send</button>
        <button class="btn-reject" onclick="rejectLead(${i})">✗ Skip</button>
      </div>
    </div>
  `).join(\'\');
  panel.innerHTML = html;
}

async function refreshSent() {
  const res = await fetch(\'/sent\');
  const data = await res.json();
  sentLeads = data.sent;
  document.getElementById(\'emails-sent\').textContent = data.total_sent;
  document.getElementById(\'followups-sent\').textContent = data.total_followups;

  const panel = document.getElementById(\'sent-panel\');
  if (sentLeads.length === 0) {
    panel.innerHTML = \'<div class="empty"><span class="empty-icon">📤</span>No emails sent yet.</div>\';
    return;
  }
  panel.innerHTML = sentLeads.slice().reverse().map(s => `
    <div class="sent-card">
      <div class="sent-dot ${s.followup_sent ? \'followup\' : \'\'}"></div>
      <div class="sent-info">
        <div class="sent-name">${s.name}</div>
        <div class="sent-email">${s.email}</div>
      </div>
      <div class="sent-date">${s.sent_date ? s.sent_date.substring(0,10) : \'\'}</div>
    </div>
  `).join(\'\');
}

async function refreshLog() {
  const res = await fetch(\'/log\');
  const data = await res.json();
  document.getElementById(\'log-count\').textContent = data.log.length;
  const panel = document.getElementById(\'log-panel\');
  if (data.log.length === 0) {
    panel.innerHTML = \'<div class="empty"><span class="empty-icon">📋</span>Waiting for activity...</div>\';
    return;
  }
  panel.innerHTML = data.log.slice(-80).reverse().map(l => {
    let cls = \'\';
    if (l.includes(\'💰\') || l.includes(\'🔥\') || l.includes(\'sent\')) cls = \'win\';
    else if (l.includes(\'📬\') || l.includes(\'Found\') || l.includes(\'Scanning\')) cls = \'info\';
    return `<div class="log-entry ${cls}">${l}</div>`;
  }).join(\'\');
}

async function refreshStatus() {
  const res = await fetch(\'/status\');
  const data = await res.json();
  document.getElementById(\'status-text\').textContent = data.status === \'scanning\' ? \'Scanning...\' : \'Online\';
  document.querySelector(\'.status-dot\').style.background = data.status === \'scanning\' ? \'#FFD100\' : \'var(--green)\';
}

function refreshAll() {
  refreshLeads();
  refreshSent();
  refreshLog();
  refreshStatus();
}

setInterval(refreshAll, 2500);
refreshAll();
</script>
</body>
</html>'''

# ============================================================
# ROUTES
# ============================================================
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_msg = data.get('message', '')
    stats = load_stats()
    try:
        response = claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=f"""You are Winston, a Gen Z Jamaican-American AI from NYC working for YardLink Studio.
You are Winston, an AI outreach tool built for YardLink Studio. You help Kevin find business leads and send cold emails. Talk like a sharp, no-nonsense New Yorker. Be direct and helpful. No fluff. Refer to the user as Kevin. Keep it short.
You help find clients for YardLink Studio (websites + AI tools for small businesses).
Current stats: {stats['emails_sent']} emails sent, {stats.get('followups_sent',0)} follow-ups sent.
Keep it short, punchy, never use markdown.""",
            messages=[{"role": "user", "content": user_msg}]
        )
        return jsonify({"reply": response.content[0].text})
    except Exception as e:
        return jsonify({"reply": f"Something broke Kevin, my bad: {e}"})

@app.route('/scan', methods=['POST'])
def scan():
    if state["status"] == "scanning":
        return jsonify({"status": "already scanning"})
    t = threading.Thread(target=run_scan, args=(PLACE_SEARCHES,))
    t.daemon = True
    t.start()
    return jsonify({"status": "scanning"})

@app.route('/stop', methods=['POST'])
def stop():
    state["status"] = "stopped"
    return jsonify({"status": "stopped"})

@app.route('/status')
def get_status():
    return jsonify({"status": state["status"]})

@app.route('/leads')
def leads():
    return jsonify({
        "leads": state["pending"],
        "total_found": len(state["businesses"]) + len(state["pending"])
    })

@app.route('/sent')
def get_sent():
    followups = load_followups()
    stats = load_stats()
    return jsonify({
        "sent": followups,
        "total_sent": stats.get("emails_sent", 0),
        "total_followups": stats.get("followups_sent", 0)
    })

@app.route('/log')
def get_log():
    return jsonify({"log": state["log"]})

@app.route('/approve', methods=['POST'])
def approve():
    data = request.json
    idx = data.get('index', 0)
    if idx >= len(state["pending"]):
        return jsonify({"success": False})

    b = state["pending"][idx]
    subject = b.get("subject", f"Quick idea for {b['name']}")
    success = send_email_fn(b["email"], b["name"], b["draft"], subject)

    if success:
        emailed = load_emailed()
        emailed.append(b["email"])
        save_emailed(emailed)

        followups = load_followups()
        followups.append({
            "name": b["name"],
            "email": b["email"],
            "type": b.get("type", ""),
            "address": b.get("address", "NYC"),
            "subject": subject,
            "original_body": b["draft"],
            "sent_date": datetime.now().isoformat(),
            "followup_sent": False,
        })
        save_followups(followups)

        stats = load_stats()
        stats["emails_sent"] = stats.get("emails_sent", 0) + 1
        save_stats(stats)

        state["emails_sent"] += 1
        state["pending"].pop(idx)
        log(f"Email sent to {b['name']} 💰")
        return jsonify({"success": True, "name": b["name"], "total_sent": stats["emails_sent"]})

    return jsonify({"success": False})

@app.route('/approve_all', methods=['POST'])
def approve_all():
    sent = 0
    emailed = load_emailed()
    followups = load_followups()
    stats = load_stats()

    for b in state["pending"][:]:
        subject = b.get("subject", f"Quick idea for {b['name']}")
        if send_email_fn(b["email"], b["name"], b["draft"], subject):
            emailed.append(b["email"])
            followups.append({
                "name": b["name"],
                "email": b["email"],
                "type": b.get("type", ""),
                "address": b.get("address", "NYC"),
                "subject": subject,
                "original_body": b["draft"],
                "sent_date": datetime.now().isoformat(),
                "followup_sent": False,
            })
            stats["emails_sent"] = stats.get("emails_sent", 0) + 1
            state["emails_sent"] += 1
            sent += 1
            log(f"Email sent to {b['name']} 💰")
            time.sleep(8)

    save_emailed(emailed)
    save_followups(followups)
    save_stats(stats)
    state["pending"] = []
    return jsonify({"sent": sent, "total_sent": stats["emails_sent"]})

@app.route('/reject', methods=['POST'])
def reject():
    data = request.json
    idx = data.get('index', 0)
    if idx < len(state["pending"]):
        state["pending"].pop(idx)
    return jsonify({"success": True})

@app.route('/followups', methods=['POST'])
def run_followups():
    sent = check_and_send_followups()
    stats = load_stats()
    return jsonify({"sent": sent, "total_followups": stats.get("followups_sent", 0)})

if __name__ == '__main__':
    stats = load_stats()
    state["emails_sent"] = stats.get("emails_sent", 0)

    ft = threading.Thread(target=followup_scheduler)
    ft.daemon = True
    ft.start()

    print("\nWinston is starting up...")
    print("Dashboard: http://localhost:5000")
    print("Press Ctrl+C to stop\n")
    app.run(debug=False, port=5000)
