from flask import Flask, render_template, request, jsonify, Response, url_for
import json
import os
import re
import csv
import io
import smtplib
import time
import threading
import tempfile
import uuid
import requests
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from winston.repository import WinstonRepository, utc_now
from winston.ai import AIService, ProviderError
from winston.commercial import CommercialLedger
from winston.signals import SignalStore, research_contact
from winston.catalog import Catalog, CatalogValidationError, UnknownEntry
from winston.fit import FitEngine
from winston.writer import Writer
from winston.guardian import Guardian
from winston.pipeline import OutreachPipeline
from winston.pricing import PricingEngine, NoPricingBasis
from winston.ratecard import RateCard
from winston.providers import ProviderRegistry
from winston.costs import BudgetGuard

load_dotenv()

app = Flask(__name__)
repository = WinstonRepository(os.getenv("WINSTON_DATABASE", "winston.db"))
repository.initialize()
ai_service = AIService.from_environment(repository)
ledger = CommercialLedger(repository)
ledger.initialize()
signal_store = SignalStore(repository)
signal_store.initialize()
catalog = Catalog(repository)
catalog.initialize()
fit_engine = FitEngine(repository, catalog, signal_store)
rate_card = RateCard(repository, catalog)
rate_card.initialize()
pricing_engine = PricingEngine(repository, catalog, rate_card)
budget_guard = BudgetGuard(repository)
budget_guard.initialize()
provider_registry = ProviderRegistry(repository, ai_service, budget_guard)
writer = Writer(repository, catalog, signal_store, fit_engine, ai_service, pricing_engine)
guardian = Guardian(repository, catalog)
pipeline = OutreachPipeline(repository, catalog, signal_store, fit_engine, writer, guardian)
pipeline.initialize()
json_write_lock = threading.RLock()

# ============================================================
# KEYS — stored in .env file, never hardcode these
# ============================================================
ANTHROPIC_KEY        = os.getenv("ANTHROPIC_KEY")
GMAIL_ADDRESS        = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD   = os.getenv("GMAIL_APP_PASSWORD")
GOOGLE_PLACES_KEY    = os.getenv("GOOGLE_PLACES_KEY")

# ============================================================
# SEND SAFETY CONTROLS
# ============================================================
# Dry-run defaults to ON. Real mail requires an explicit opt-out, so a fresh
# checkout, a test run, or a forgotten .env can never deliver to real people.
WINSTON_DRY_RUN      = os.getenv("WINSTON_DRY_RUN", "true").strip().casefold() != "false"
SEND_MIN_INTERVAL_S  = float(os.getenv("WINSTON_SEND_MIN_INTERVAL", "30"))
SEND_MAX_PER_DAY     = int(os.getenv("WINSTON_SEND_MAX_PER_DAY", "50"))

_send_gate      = threading.Lock()
_last_send_at   = 0.0
_send_day       = ""
_send_day_count = 0


class SendBlocked(RuntimeError):
    """Raised when a send is refused by a safety control."""


def _enforce_send_limits() -> None:
    """Serialize sends, space them out, and cap daily volume. Raises SendBlocked."""
    global _last_send_at, _send_day, _send_day_count
    with _send_gate:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != _send_day:
            _send_day, _send_day_count = today, 0
        if _send_day_count >= SEND_MAX_PER_DAY:
            raise SendBlocked(f"Daily send cap reached ({SEND_MAX_PER_DAY})")
        wait = SEND_MIN_INTERVAL_S - (time.monotonic() - _last_send_at)
        if wait > 0:
            time.sleep(wait)
        _last_send_at = time.monotonic()
        _send_day_count += 1

# ============================================================
# YOUR SOCIAL HANDLES — used in DM messages
# ============================================================
YARDLINK_IG      = "@yardlinkstudio"
YARDLINK_FB      = "YardLink Studio"
YARDLINK_SITE    = "yardlinkstudio.com"

# ============================================================
# PERSISTENT STORAGE FILES
# ============================================================
EMAILED_FILE   = "emailed.json"
LEADS_FILE     = "leads.json"
FOLLOWUP_FILE  = "followups.json"
STATS_FILE     = "stats.json"
SOCIAL_FILE    = "social_leads.json"   # NEW — businesses with IG/FB but no email
CONTACTS_FILE  = "contacts.json"       # NEW — master contact DB (all leads ever found)

# ============================================================
# GOOGLE PLACES SEARCH QUERIES
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

# Rotating subject lines

# In-memory state
state = {
    "businesses":    [],
    "emails_sent":   0,
    "status":        "idle",
    "log":           [],
    "pending":       [],
    "social_pending": [],  # NEW — leads with social but no email
    "existing_draft_progress": {"requested": 0, "completed": 0, "failed": 0},
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
    """Legacy compatibility writer using atomic replacement until JSON is retired."""
    target = os.path.abspath(filepath)
    directory = os.path.dirname(target) or "."
    with json_write_lock:
        fd, temporary = tempfile.mkstemp(prefix=".winston-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

def load_emailed():      return load_json(EMAILED_FILE, [])
def save_emailed(d):     save_json(EMAILED_FILE, d)
def load_saved_leads():  return load_json(LEADS_FILE, [])
def save_leads(d):       save_json(LEADS_FILE, d)
def load_followups():    return load_json(FOLLOWUP_FILE, [])
def load_stats():        return load_json(STATS_FILE, {"emails_sent": 0, "leads_found": 0, "followups_sent": 0, "social_leads": 0})
def save_stats(d):       save_json(STATS_FILE, d)
def load_social_leads(): return load_json(SOCIAL_FILE, [])
def save_social_leads(d):save_json(SOCIAL_FILE, d)
def load_contacts():     return load_json(CONTACTS_FILE, [])
def save_contacts(d):    save_json(CONTACTS_FILE, d)

# ============================================================
# LOGGING
# ============================================================
def log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    state["log"].append(entry)
    if len(state["log"]) > 200:
        state["log"] = state["log"][-200:]
    # Structured events complement the short-lived display log. Avoid storing secrets.
    try:
        repository.add_event("activity.log", details={"message": str(msg)[:500]})
    except Exception:
        pass

# ============================================================
# EMAIL BLOCKLIST & QUALITY SCORING
# ============================================================
EMAIL_BLOCKLIST = [
    'noreply', 'no-reply', 'yelp', 'google', 'example', 'sentry',
    'facebook', 'privacy', 'support@sentry', 'info@sentry', 'wix',
    'squarespace', 'shopify', 'godaddy', 'wordpress', 'mailchimp',
    'donotreply', 'test@', 'admin@', 'webmaster@', 'postmaster@',
    'schema', 'amazonaws', 'cloudflare', 'netlify', '.png', '.jpg',
    'reviews.import', 'domain.com', 'mysite.com', 'mailservice.com',
    'info@info.com', 'hello@info.com', 'webador', 'booking.com',
    'tripadvisor', 'opentable', 'grubhub', 'doordash', 'ubereats',
]

# ── NEW: Prefer domain emails over free emails ──
FREE_EMAIL_DOMAINS = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
                      'aol.com', 'icloud.com', 'ymail.com', 'optonline.net'}

def email_quality_score(email: str) -> int:
    """
    Score an email 0-10. Higher = better.
    Domain email (info@theirbusiness.com) scores higher than gmail.
    Prefers contact/info/hello prefixes over random strings.
    """
    if not email or '@' not in email:
        return 0
    local, domain = email.lower().split('@', 1)
    if domain in FREE_EMAIL_DOMAINS:
        base = 3  # free email — usable but not ideal
    else:
        base = 8  # domain email — this is a real business contact
    # Boost for professional prefixes
    good_prefixes = ('info', 'contact', 'hello', 'hi', 'team', 'office',
                     'mail', 'booking', 'reservations', 'admin', 'support')
    if any(local.startswith(p) for p in good_prefixes):
        base += 1
    # Penalize weirdly long local parts
    if len(local) > 30:
        base -= 2
    return max(0, min(10, base))

def is_valid_email(email: str) -> bool:
    email_lower = email.lower()
    if any(x in email_lower for x in EMAIL_BLOCKLIST):
        return False
    if len(email) > 80:
        return False
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return False
    return True

def extract_emails_from_html(html: str) -> list[str]:
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
    valid  = [e for e in emails if is_valid_email(e)]
    # Sort by quality — domain emails float to top
    return sorted(set(valid), key=email_quality_score, reverse=True)

# ============================================================
# SOCIAL MEDIA DETECTION — NEW
# ============================================================
SOCIAL_PATTERNS = {
    "instagram": [
        r'instagram\.com/([A-Za-z0-9_.]+)',
    ],
    "facebook": [
        r'facebook\.com/([A-Za-z0-9_./-]+)',
        r'fb\.com/([A-Za-z0-9_./-]+)',
        r'fb\.me/([A-Za-z0-9_./-]+)',
    ],
    "tiktok": [
        r'tiktok\.com/@([A-Za-z0-9_.]+)',
    ],
}

def extract_social_handles(html: str, website_url: str = "") -> dict:
    """
    Extracts Instagram, Facebook, TikTok handles from a page's HTML.
    Returns dict like: {"instagram": "handle", "facebook": "page/slug", "tiktok": "handle"}
    """
    found = {}
    for platform, patterns in SOCIAL_PATTERNS.items():
        for pat in patterns:
            matches = re.findall(pat, html, re.IGNORECASE)
            if matches:
                # Clean up — remove trailing slashes, filter junk
                clean = [m.strip('/').split('?')[0] for m in matches]
                clean = [m for m in clean if m and len(m) > 2 and m.lower() not in
                         ('sharer', 'share', 'dialog', 'plugins', 'tr', 'login',
                          'home', 'pages', 'groups', 'events', 'marketplace',
                          'formatjs', 'wix', 'wixstudio', 'intent', 'hashtag',
                          'privacy', 'terms', 'help', 'developer', 'developers')]
                if clean:
                    found[platform] = clean[0]
                    break
    return found

def scrape_social_from_website(website_url: str) -> dict:
    """Try to pull social handles from homepage and /contact."""
    pages = [website_url, website_url.rstrip("/") + "/contact"]
    all_social = {}
    for url in pages:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}
            res = requests.get(url, headers=headers, timeout=6)
            if res.status_code == 200:
                social = extract_social_handles(res.text, url)
                all_social.update(social)
        except:
            continue
        if all_social:
            break
    return all_social

def find_instagram_from_google(business_name: str, location: str) -> str:
    """
    Search Google for a business's Instagram page using their Places data.
    Falls back gracefully — returns handle or empty string.
    """
    # Use Google Custom Search or just scrape search snippet
    try:
        query = f"{business_name} {location} instagram"
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        res = requests.get(
            "https://www.google.com/search",
            params={"q": query},
            headers=headers,
            timeout=6
        )
        matches = re.findall(r'instagram\.com/([A-Za-z0-9_.]+)', res.text, re.IGNORECASE)
        matches = [m for m in matches if m.lower() not in
                   ('p', 'reel', 'stories', 'explore', 'accounts', 'tv')]
        return matches[0] if matches else ""
    except:
        return ""

# ============================================================
# PHONE NUMBER EXTRACTION — NEW
# ============================================================
def extract_phone_from_html(html: str) -> str:
    """Pull a US phone number from page HTML."""
    patterns = [
        r'\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}',
        r'\+1[\s.\-]?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}',
    ]
    for pat in patterns:
        matches = re.findall(pat, html)
        if matches:
            # Prefer numbers that look like formatted US numbers
            cleaned = [re.sub(r'[^\d]', '', m) for m in matches]
            valid   = [c for c in cleaned if len(c) in (10, 11)]
            if valid:
                num = valid[0][-10:]  # strip leading 1 if 11 digits
                return f"({num[:3]}) {num[3:6]}-{num[6:]}"
    return ""

# ============================================================
# SCRAPING
# ============================================================
LOCAL_SCRAPER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 Winston/1.0",
    "Accept": "text/html,application/xhtml+xml",
}
MAX_LOCAL_PAGE_BYTES = 2_000_000

class LocalLinkParser(HTMLParser):
    """Minimal dependency-free link extractor for public business pages."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() == "a":
            self._href = dict(attrs).get("href", "")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.casefold() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []

def fetch_local_html(url: str) -> str:
    """Fetch one public HTML page locally without any paid scraping service."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    try:
        response = requests.get(url, headers=LOCAL_SCRAPER_HEADERS, timeout=10)
        content_type = response.headers.get("Content-Type", "").lower()
        if response.status_code != 200 or "html" not in content_type:
            return ""
        if len(response.content) > MAX_LOCAL_PAGE_BYTES:
            return ""
        return response.text
    except requests.RequestException:
        return ""

def discover_contact_pages(home_url: str, html: str, limit: int = 5) -> list[str]:
    """Find likely contact/about pages while staying on the website's own domain."""
    home = urlparse(home_url)
    discovered = []
    parser = LocalLinkParser()
    parser.feed(html)
    keywords = ("contact", "about", "team", "location", "visit", "reservation")
    for href, label in parser.links:
        href = href.strip()
        label = label.casefold()
        absolute = urljoin(home_url, href)
        parsed = urlparse(absolute)
        haystack = f"{parsed.path} {label}".casefold()
        if parsed.scheme in ("http", "https") and parsed.netloc == home.netloc and any(word in haystack for word in keywords):
            clean = parsed._replace(fragment="").geturl()
            if clean not in discovered and clean != home_url:
                discovered.append(clean)
        if len(discovered) >= limit:
            break
    return discovered

def scrape_local_page(url: str, html: str | None = None) -> tuple[str, str, dict]:
    """
    Returns (best_email, phone, social_handles) from a page.
    """
    try:
        html = html if html is not None else fetch_local_html(url)
        if not html:
            return "", "", {}
        social = extract_social_handles(html, url)
        phone  = extract_phone_from_html(html)

        # Check mailto links first
        mailto_emails = []
        parser = LocalLinkParser()
        parser.feed(html)
        for href, _ in parser.links:
            if href.casefold().startswith("mailto:"):
                email = href[7:].split("?")[0].strip()
                if email and is_valid_email(email):
                    mailto_emails.append(email)

        # Score and pick best
        all_emails = mailto_emails + extract_emails_from_html(html)
        all_emails = list(dict.fromkeys(all_emails))  # dedupe, preserve order
        best_email = sorted(all_emails, key=email_quality_score, reverse=True)[0] if all_emails else ""

        return best_email, phone, social

    except Exception:
        return "", "", {}

def find_contact_info(website_url: str) -> tuple[str, str, dict]:
    """
    Full contact info finder.
    Returns (best_email, phone, social_handles)
    Tries multiple pages, scores emails, picks domain email over gmail.
    """
    homepage_html = fetch_local_html(website_url)
    pages = [website_url]
    if homepage_html:
        pages.extend(discover_contact_pages(website_url, homepage_html))
    for fallback_path in ("/contact", "/contact-us", "/about", "/about-us"):
        fallback = website_url.rstrip("/") + fallback_path
        if fallback not in pages and len(pages) < 6:
            pages.append(fallback)

    all_emails = []
    best_phone = ""
    all_social = {}

    # All scraping is local and free: requests + the standard-library parser only.
    for index, url in enumerate(pages):
        try:
            email, phone, social = scrape_local_page(url, homepage_html if index == 0 else None)
            if email:
                all_emails.append(email)
            if phone and not best_phone:
                best_phone = phone
            all_social.update(social)
            time.sleep(0.2)
        except:
            continue

    # Pick the best email
    best_email = sorted(all_emails, key=email_quality_score, reverse=True)[0] if all_emails else ""

    return best_email, best_phone, all_social

# ============================================================
# DM MESSAGE WRITER — NEW
# ============================================================
# ============================================================
# SOCIAL DM GENERATORS - REMOVED 2026-08-23 (Phase A)
# ============================================================
# write_instagram_dm() and write_facebook_dm() were a second outreach generation
# path carrying the same hardcoded agency blurb, and they never worked: 0 successes
# across 120 attempts, because their 200-token budget was consumed entirely by the
# reasoning block (see docs/ARCHITECTURE.md).
#
# Social lead capture is retained. Businesses with a social presence and no email
# are still discovered and recorded, they simply arrive without a generated message.
# The DM channel needs its own evidence-based Writer variant and, more importantly,
# outcome tracking. Generating messages for a channel with no send path and no
# measurement produces text nobody can learn from.

# ============================================================
# LEGACY OUTREACH GENERATOR - REMOVED 2026-08-23 (Phase A)
# ============================================================
# write_email() described YardLink from a hardcoded string and opened identically
# to all 1,396 prospects: "a NYC digital agency that builds fast, modern websites,
# AI chatbots, and automated tools". It knew nothing about the business it was
# sent to, and ignored every signal, score, and catalogue entry Winston had.
#
# All outreach now runs through OutreachPipeline:
#   research -> problems -> catalogue -> offer -> proof -> Writer -> Guardian -> draft
# A second generation path is a regression; tests/test_production_pipeline.py
# enforces that this function does not return.

# ============================================================
# EMAIL SENDING
# ============================================================
def send_email_fn(to_email: str, business_name: str, body: str, subject: str = None) -> bool:
    """The single production delivery primitive.

    Reachable only from confirm_send() after the full state machine has run.
    Three independent guards sit in front of SMTP: dry-run, a suppression
    backstop, and rate limiting. Any one of them refusing means no mail.
    """
    if not subject:
        subject = f"Quick idea for {business_name}"

    # Guard 1 — suppression backstop. claim_send() already checks this inside the
    # claiming transaction; repeating it here means no future caller can bypass it.
    if repository.is_suppressed(to_email):
        log(f"BLOCKED: {to_email} is suppressed")
        repository.add_event("send.blocked", entity_type="contact",
                             details={"reason": "suppressed", "email": to_email})
        return False

    # Guard 2 — dry-run. Default ON; real delivery is an explicit opt-out.
    if WINSTON_DRY_RUN:
        log(f"DRY RUN: would send to {to_email} — subject {subject!r}")
        repository.add_event("send.dry_run", entity_type="contact",
                             details={"email": to_email, "subject": subject[:120]})
        return True

    # Guard 3 — rate limiting and daily cap.
    try:
        _enforce_send_limits()
    except SendBlocked as exc:
        log(f"BLOCKED: {exc}")
        repository.add_event("send.blocked", entity_type="contact",
                             details={"reason": str(exc), "email": to_email})
        return False

    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = to_email
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
# LEGACY FOLLOW-UP SENDER — REMOVED 2026-08-23
# ============================================================
# check_and_send_followups() and followup_scheduler() were deleted, not disabled.
# They sent mail directly from followups.json, bypassing suppression, idempotency,
# atomic claiming, audit logging, and human confirmation. 131 follow-ups went out
# through that path before it was closed.
#
# There is now exactly ONE production send path:
#   draft -> reviewed -> approved -> queued -> confirmed -> claim_send() -> send_email_fn()
# Historical follow-up records are preserved as read-only data (see /sent).
# Re-introducing a second send path is a regression; tests/test_no_legacy_send.py enforces this.

# ============================================================
# MASTER CONTACT DB — NEW
# save every lead we ever find, regardless of whether emailed
# ============================================================
def upsert_contact(business: dict, email: str, phone: str, social: dict):
    """Add or update a contact in the master contacts DB."""
    contacts = load_contacts()
    place_id = business.get("place_id", "")

    contact = {
        "place_id":   place_id,
        "name":       business.get("name", ""),
        "type":       business.get("type", ""),
        "address":    business.get("address", ""),
        "phone":      phone or business.get("phone", ""),
        "website":    business.get("website", ""),
        "email":      email,
        "email_score": email_quality_score(email) if email else 0,
        "instagram":  social.get("instagram", ""),
        "facebook":   social.get("facebook", ""),
        "tiktok":     social.get("tiktok", ""),
        "found_at":   datetime.now().isoformat(),
    }

    if place_id:
        for existing in contacts:
            if existing.get("place_id") == place_id:
                # Keep previously discovered values when a later scan has blanks.
                for key, value in contact.items():
                    if value or key == "email_score":
                        existing[key] = value
                existing["updated_at"] = datetime.now().isoformat()
                save_contacts(contacts)
                return

    contacts.append(contact)
    save_contacts(contacts)

# ============================================================
# GOOGLE PLACES API — LEAD DISCOVERY
# ============================================================
def google_places_search(keyword: str, location: str, max_results: int = 20) -> list:
    businesses = []
    url    = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    query  = f"{keyword} in {location}"
    params = {"query": query, "key": GOOGLE_PLACES_KEY, "type": "establishment"}

    try:
        res  = requests.get(url, params=params, timeout=10)
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
            website, phone = get_place_details(place_id)

            businesses.append({
                "name":     name,
                "address":  address,
                "type":     keyword,
                "types":    types,
                "website":  website,
                "phone":    phone,
                "place_id": place_id,
            })

        return businesses

    except Exception as e:
        log(f"Places search error: {e}")
        return []

def get_place_details(place_id: str) -> tuple[str, str]:
    """Fetch website and phone from a Place ID."""
    url    = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields":   "website,formatted_phone_number",
        "key":      GOOGLE_PLACES_KEY,
    }
    try:
        res    = requests.get(url, params=params, timeout=8)
        result = res.json().get("result", {})
        return result.get("website", ""), result.get("formatted_phone_number", "")
    except:
        return "", ""

# ============================================================
# MAIN SCAN — upgraded with social + phone collection
# ============================================================
def run_scan(searches):
    state["status"] = "scanning"
    log("Winston is live. Let's get it, Emperor Edwards.")
    emailed       = load_emailed()
    emailed_keys  = {str(value).lower() for value in emailed}
    queued_emails = {b.get("email", "").lower() for b in state["pending"] if b.get("email")}
    social_leads  = load_social_leads()
    social_handles_seen = {s.get("place_id") for s in social_leads if s.get("place_id")}
    subject_index = 0
    total_email_leads  = 0
    total_social_leads = 0

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
            phone   = b.get("phone", "")

            # ── Try to get email + phone + social from website ──
            if website:
                log(f"Scraping {name}...")
                email, scraped_phone, social = find_contact_info(website)
                if scraped_phone and not phone:
                    phone = scraped_phone
            else:
                email  = ""
                social = {}

            # ── If no website, try to find IG via Google search ──
            if not website and not social.get("instagram"):
                ig = find_instagram_from_google(name, b.get("address", "NYC"))
                if ig:
                    social["instagram"] = ig

            # ── Save to master contact DB regardless ──
            upsert_contact(b, email, phone, social)

            # ── Route: email lead vs social-only lead ──
            email_key = email.lower()
            if email and email_key not in emailed_keys and email_key not in queued_emails and email_quality_score(email) >= 3:
                log(f"📧 Email lead: {name} — {email} (score: {email_quality_score(email)}/10)")
                b["email"], b["phone"], b["social"] = email, phone, social
                contact_id, _ = repository.upsert_contact(b, "scan")
                # Research first: the Writer refuses to invent problems, so a prospect
                # with no observations produces no draft rather than a generic one.
                if b.get("website"):
                    research_contact(repository, signal_store, contact_id, b["website"])
                outcome = pipeline.generate(contact_id)
                if outcome.reviewable:
                    b.update({"contact_id": contact_id, "draft_id": outcome.draft_id,
                              "subject": outcome.draft.subject, "draft": outcome.draft.body,
                              "workflow_stage": "draft", "intent": outcome.draft.intent})
                    state["pending"].append(b)
                    queued_emails.add(email_key)
                    total_email_leads += 1
                    stats = load_stats()
                    stats["leads_found"] = stats.get("leads_found", 0) + 1
                    save_stats(stats)
                    log(f"Draft ready: {name} 🔥")

            elif not email and social and b.get("place_id") not in social_handles_seen:
                # No email but has social — queue as social lead
                log(f"📱 Social lead: {name} — IG: {social.get('instagram','')} FB: {social.get('facebook','')}")
                b["phone"]  = phone
                b["social"] = social
                if social.get("instagram") or social.get("facebook"):
                    social_leads.append({**b, "added_at": datetime.now().isoformat()})
                    social_handles_seen.add(b.get("place_id"))
                    save_social_leads(social_leads)
                    state["social_pending"].append(b)
                    total_social_leads += 1
                    stats = load_stats()
                    stats["social_leads"] = stats.get("social_leads", 0) + 1
                    save_stats(stats)

            elif not email and not social:
                if phone:
                    log(f"📞 Phone only: {name} — {phone}")
                else:
                    log(f"No contact found: {name}")

            time.sleep(1.5)

        time.sleep(1)

    state["status"] = "idle"
    log(f"Done. {total_email_leads} email leads, {total_social_leads} social leads. Let's go Kevin.")

def persist_draft(business: dict, subject: str, body: str) -> str:
    """Write a generated draft to SQLite immediately.

    Drafts used to reach the database only when a human clicked Approve, so an
    unreviewed queue lived solely in ``state["pending"]`` and died with the process.
    Persisting at generation time makes the in-memory list a cache of the drafts
    table rather than the only copy.
    """
    contact_id, _ = repository.upsert_contact(business, business.get("source", "scan"))
    draft_id = repository.create_draft(contact_id, subject, body)
    business.update({"contact_id": contact_id, "draft_id": draft_id,
                     "subject": subject, "draft": body, "workflow_stage": "draft"})
    return draft_id


def rehydrate_pending_queue() -> int:
    """Rebuild the review queue from SQLite on startup."""
    restored = []
    for row in repository.pending_drafts():
        restored.append({
            "contact_id": row["contact_id"], "draft_id": row["draft_id"],
            "place_id": row.get("place_id") or "", "name": row.get("name") or "",
            "email": row.get("email") or "", "phone": row.get("phone") or "",
            "website": row.get("website") or "", "address": row.get("address") or "",
            "type": row.get("business_type") or "business",
            "subject": row.get("subject") or "", "draft": row.get("body") or "",
            "workflow_stage": row.get("stage") or "draft",
            "social": {"instagram": row.get("instagram") or "",
                       "facebook": row.get("facebook") or "",
                       "tiktok": row.get("tiktok") or ""},
        })
    state["pending"] = restored
    if restored:
        log(f"Restored {len(restored)} pending drafts from the database")
    return len(restored)


def run_existing_contact_drafts(limit: int):
    """Create a bounded, zero-discovery-cost review batch from migrated contacts."""
    state["status"] = "drafting_existing"
    progress = {"requested": limit, "completed": 0, "failed": 0}
    state["existing_draft_progress"] = progress
    candidates = repository.draft_candidates(limit)
    progress["requested"] = len(candidates)
    log(f"Zero-cost batch started: {len(candidates)} existing contacts")
    for contact in candidates:
        if state["status"] == "stopped":
            break
        email = contact.get("email", "")
        if not is_valid_email(email):
            progress["failed"] += 1
            repository.suppress(email, "invalid-or-blocklisted", contact["id"])
            repository.add_event("draft.skipped_invalid_email", entity_type="contact",
                                 entity_id=contact["id"])
            continue
        business = {
            "contact_id": contact["id"],
            "place_id": contact.get("place_id", ""),
            "name": contact.get("name", ""),
            "email": email,
            "email_score": email_quality_score(email),
            "phone": contact.get("phone", ""),
            "website": contact.get("website", ""),
            "address": contact.get("address", ""),
            "type": contact.get("business_type", "") or "business",
            "social": {
                "instagram": contact.get("instagram", ""),
                "facebook": contact.get("facebook", ""),
                "tiktok": contact.get("tiktok", ""),
            },
        }
        # Research before writing. The Writer will not invent problems, so an
        # unresearched prospect yields no draft rather than a generic one.
        if business["website"] and not signal_store.last_researched(contact["id"]):
            research_contact(repository, signal_store, contact["id"], business["website"])

        outcome = pipeline.generate(contact["id"])
        if not outcome.reviewable:
            progress["failed"] += 1
            progress.setdefault("skipped_reasons", {})
            key = outcome.status
            progress["skipped_reasons"][key] = progress["skipped_reasons"].get(key, 0) + 1
            log(f"No draft for {business['name']}: {outcome.reason[:90]}")
            continue

        business.update({"subject": outcome.draft.subject, "draft": outcome.draft.body,
                         "draft_id": outcome.draft_id, "workflow_stage": "draft",
                         "intent": outcome.draft.intent})
        state["pending"].append(business)
        progress["completed"] += 1
        log(f"Draft ready: {business['name']} ({outcome.draft.intent}) "
            f"{progress['completed']}/{len(candidates)}")
    state["status"] = "idle"
    log(f"Zero-cost batch complete: {progress['completed']} drafts, {progress['failed']} skipped/failed")

# ============================================================
# CSV EXPORT — NEW
# ============================================================
def generate_csv() -> str:
    """Export master contact DB as CSV string."""
    contacts = load_contacts()
    output   = io.StringIO()
    fieldnames = ["name", "type", "address", "phone", "email", "email_score",
                  "website", "instagram", "facebook", "tiktok", "found_at"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(contacts)
    return output.getvalue()

# ============================================================
# FRONTEND
# ============================================================
# Served from templates/dashboard.html + static/. A large inline HTML
# string also lived here, unreferenced since the template was introduced,
# and still contained the removed DM preview markup.


# ============================================================
# ROUTES
# ============================================================
@app.context_processor
def asset_version():
    """Bust the browser cache when a static file actually changes.

    Without this, editing the dashboard JS or CSS leaves the browser serving a
    stale copy, which looks exactly like the change not working.
    """
    def versioned(filename: str) -> str:
        path = os.path.join(app.static_folder or "static", filename)
        try:
            stamp = int(os.path.getmtime(path))
        except OSError:
            stamp = 0
        return f"{url_for('static', filename=filename)}?v={stamp}"
    return {"versioned": versioned}


@app.route('/')
def index():
    return render_template("dashboard.html")

@app.route('/api/dashboard')
def dashboard_data():
    stats = load_stats()
    contacts = repository.counts()
    workflow = repository.workflow_counts()
    ai = ai_service.status()
    pending_ready = [lead for lead in state["pending"] if lead.get("workflow_stage", "draft") in ("draft", "reviewed")]
    persisted_pending = sum(1 for lead in pending_ready if lead.get("draft_id"))
    return jsonify({
        "metrics": {
            "contacts": contacts["contacts"],
            "drafts_ready": len(pending_ready) + workflow["draft"] + workflow["reviewed"] - persisted_pending,
            "approved": workflow["approved"],
            "queued": workflow["queued"] + workflow["confirmed"],
            "emails_sent": stats.get("emails_sent", 0),
            "social_leads": len(load_social_leads()),
        },
        "workflow": workflow,
        "scan_status": state["status"],
        "automatic_followups": False,
        "ai": ai,
        "events": repository.recent_events(12),
    })

@app.route('/chat', methods=['POST'])
def chat():
    data     = request.get_json(silent=True) or {}
    user_msg = str(data.get('message', '')).strip()
    if not user_msg or len(user_msg) > 2000:
        return jsonify({"error": "Message must contain 1–2000 characters"}), 400
    stats    = load_stats()
    contacts = load_contacts()
    try:
        system = f"""You are Winston, a Gen Z Jamaican-American AI from NYC working for YardLink Studio.
You help Kevin find business leads and send cold emails. Talk like a sharp, no-nonsense New Yorker. Be direct and helpful. No fluff. Refer to the user as Kevin or Emperor Edwards occasionally.
Stats: {stats.get('emails_sent',0)} emails sent, {stats.get('followups_sent',0)} follow-ups, {stats.get('social_leads',0)} social leads, {len(contacts)} total contacts in DB.
Keep it short and punchy. Never use markdown."""
        response = ai_service.generate(user_msg, system=system, max_tokens=300, purpose="chat")
        return jsonify({"reply": response.text, "provider": response.provider, "model": response.model})
    except ProviderError:
        return jsonify({"error": "No zero-cost AI provider is currently available. Configure Gemini or start Ollama."}), 503

@app.route('/ai/status')
def ai_status():
    return jsonify(ai_service.status())

@app.route('/scan', methods=['POST'])
def scan():
    if state["status"] in ("scanning", "drafting_existing"):
        return jsonify({"status": "already running"}), 409
    t = threading.Thread(target=run_scan, args=(PLACE_SEARCHES,))
    t.daemon = True
    t.start()
    return jsonify({"status": "scanning"})

@app.route('/draft-existing', methods=['POST'])
def draft_existing():
    if state["status"] in ("scanning", "drafting_existing"):
        return jsonify({"error": "Another Winston job is already running"}), 409
    data = request.get_json(silent=True) or {}
    limit = data.get("limit", 10)
    if not isinstance(limit, int) or not 1 <= limit <= 50:
        return jsonify({"error": "Batch limit must be an integer from 1 to 50"}), 400
    state["status"] = "drafting_existing"
    state["existing_draft_progress"] = {"requested": limit, "completed": 0, "failed": 0}
    thread = threading.Thread(target=run_existing_contact_drafts, args=(limit,), daemon=True)
    thread.start()
    return jsonify({"status": "drafting_existing", "limit": limit})

@app.route('/stop', methods=['POST'])
def stop():
    state["status"] = "stopped"
    return jsonify({"status": "stopped"})

@app.route('/status')
def get_status():
    return jsonify({"status": state["status"],
                    "existing_draft_progress": state["existing_draft_progress"]})

@app.route('/leads')
def leads():
    return jsonify({
        "leads":       state["pending"],
        "total_found": len(load_contacts()),
    })

@app.route('/social_leads')
def get_social_leads():
    all_social = load_social_leads()
    limit = max(1, min(request.args.get('limit', 200, type=int), 500))
    return jsonify({
        "leads": all_social[-limit:],
        "total": len(all_social),
    })

@app.route('/sent')
def get_sent():
    followups = load_followups()
    stats     = load_stats()
    limit     = max(1, min(request.args.get('limit', 200, type=int), 500))
    return jsonify({
        "sent":             followups[-limit:],
        "total_sent":       stats.get("emails_sent", 0),
        "total_followups":  stats.get("followups_sent", 0),
    })

@app.route('/log')
def get_log():
    return jsonify({"log": state["log"]})

@app.route('/approve', methods=['POST'])
def approve():
    data = request.get_json(silent=True) or {}
    idx = data.get("index")
    if not isinstance(idx, int) or idx < 0 or idx >= len(state["pending"]):
        return jsonify({"success": False, "error": "Invalid lead index"}), 400
    business = state["pending"][idx]
    contact_id, _ = repository.upsert_contact(business, "review_queue")
    draft_id = business.get("draft_id")
    if not draft_id:
        draft_id = repository.create_draft(
            contact_id,
            business.get("subject", f"Quick idea for {business.get('name', 'your business')}"),
            business.get("draft", ""),
        )
        business["draft_id"] = draft_id
    draft = repository.get_draft(draft_id)
    if draft and draft["stage"] == "draft":
        repository.transition_draft(draft_id, "reviewed")
    if repository.get_draft(draft_id)["stage"] == "reviewed":
        repository.transition_draft(draft_id, "approved")
    business["workflow_stage"] = "approved"
    log(f"Draft approved for {business.get('name', 'lead')} — awaiting queue and confirmation")
    return jsonify({"success": True, "name": business.get("name", "Lead"),
                    "draft_id": draft_id, "stage": "approved"})

@app.route('/approve_all', methods=['POST'])
def approve_all():
    approved = 0
    for idx in range(len(state["pending"])):
        business = state["pending"][idx]
        if business.get("workflow_stage") == "approved":
            continue
        contact_id, _ = repository.upsert_contact(business, "review_queue")
        draft_id = repository.create_draft(contact_id, business.get("subject", ""), business.get("draft", ""))
        repository.transition_draft(draft_id, "reviewed")
        repository.transition_draft(draft_id, "approved")
        business.update({"draft_id": draft_id, "workflow_stage": "approved"})
        approved += 1
    return jsonify({"approved": approved})

@app.route('/drafts/<draft_id>/queue', methods=['POST'])
def queue_draft(draft_id):
    try:
        job_id, created = repository.queue_draft(draft_id)
        return jsonify({"success": True, "job_id": job_id, "created": created, "stage": "queued"})
    except (KeyError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

@app.route('/send-jobs/<job_id>/confirm', methods=['POST'])
def confirm_send(job_id):
    """Explicit human-confirmation boundary; the only dashboard route that sends."""
    try:
        repository.confirm_send(job_id)
        worker_id = f"web-{uuid.uuid4()}"
        job = repository.claim_send(job_id, worker_id)
        if not job:
            return jsonify({"success": False, "error": "Job unavailable or recipient suppressed"}), 409
        success = send_email_fn(job["email"], job["name"], job["body"], job["subject"])
        repository.complete_send(job_id, success=success, error="SMTP delivery failed" if not success else "")

        # Record the attempt in the commercial ledger regardless of outcome. A failed
        # send is as much a fact worth learning from as a successful one.
        campaign_id = ledger.ensure_campaign(
            os.getenv("WINSTON_CAMPAIGN", "default"), "Default outreach",
            objective="Ongoing YardLink prospecting")
        message_id = ledger.record_message(
            contact_id=job["contact_id"], to_email=job["email"], subject=job["subject"],
            body=job["body"], campaign_id=campaign_id, draft_id=job["draft_id"],
            send_job_id=job_id, sent_at=utc_now() if success else None, source="winston:confirm_send")
        ledger.record_message_event(message_id, "sent" if success else "failed",
                                    detail={"dry_run": WINSTON_DRY_RUN})

        if not success:
            return jsonify({"success": False, "error": "SMTP delivery failed"}), 502
        emailed = load_emailed()
        if job["email"].casefold() not in {str(value).casefold() for value in emailed}:
            emailed.append(job["email"])
            save_emailed(emailed)
        stats = load_stats()
        stats["emails_sent"] = stats.get("emails_sent", 0) + 1
        save_stats(stats)
        state["emails_sent"] += 1
        state["pending"] = [lead for lead in state["pending"] if lead.get("draft_id") != job["draft_id"]]
        log(f"Confirmed email sent to {job['name']}")
        return jsonify({"success": True, "stage": "sent", "job_id": job_id})
    except (KeyError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

@app.route('/reject', methods=['POST'])
def reject():
    data = request.get_json(silent=True) or {}
    idx = data.get("index")
    if not isinstance(idx, int) or idx < 0 or idx >= len(state["pending"]):
        return jsonify({"success": False, "error": "Invalid lead index"}), 400
    business = state["pending"].pop(idx)
    repository.add_event("lead.rejected", entity_type="contact", actor="user",
                         details={"name": str(business.get("name", ""))[:120]})
    return jsonify({"success": True})

@app.route('/skip', methods=['POST'])
def skip():
    data = request.get_json(silent=True) or {}
    idx = data.get("index")
    if not isinstance(idx, int) or idx < 0 or idx >= len(state["pending"]):
        return jsonify({"success": False, "error": "Invalid lead index"}), 400
    business = state["pending"].pop(idx)
    repository.add_event("lead.skipped", entity_type="contact", actor="user",
                         details={"name": str(business.get("name", ""))[:120]})
    return jsonify({"success": True})

@app.route('/followups', methods=['POST'])
def run_followups():
    return jsonify({
        "sent": 0, "enabled": False,
        "error": "The legacy follow-up sender was permanently removed. All sending must go "
                 "through the draft/approve/queue/confirm state machine.",
    }), 410

@app.route('/drafts/<draft_id>/intelligence')
def draft_intelligence(draft_id):
    """The full reasoning chain behind one draft, for human review."""
    record = pipeline.intelligence_for(draft_id)
    if record is None:
        return jsonify({"error": "No intelligence recorded for that draft"}), 404
    draft = repository.get_draft(draft_id)
    return jsonify({**record, "draft": dict(draft) if draft else None})


@app.route('/drafts/blocked')
def blocked_drafts():
    """Drafts Guardian refused. Visible rather than silently discarded."""
    return jsonify({"blocked": pipeline.blocked(), "stats": pipeline.stats()})


@app.route('/prospects/<contact_id>/generate', methods=['POST'])
def generate_draft(contact_id):
    """Run one prospect through the production pipeline."""
    try:
        return jsonify(pipeline.generate(contact_id).as_dict())
    except KeyError:
        return jsonify({"error": "Unknown contact"}), 404


@app.route('/ratecard')
def ratecard_list():
    """Commercial parameters with their provenance. Nothing here is market data."""
    return jsonify({
        "entries": [e.as_dict() for e in rate_card.list()],
        "status": rate_card.status(),
    })


@app.route('/ratecard', methods=['POST'])
def ratecard_upsert():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify({"success": True, "entry": rate_card.upsert(payload, actor="operator").as_dict()})
    except (ValueError, KeyError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route('/ratecard/<slug>/enable', methods=['POST'])
def ratecard_enable(slug):
    """Having a price is not a decision to sell. This is that decision."""
    body = request.get_json(silent=True) or {}
    try:
        entry = rate_card.enable(slug, enabled=bool(body.get("enabled", True)), actor="operator")
    except KeyError:
        return jsonify({"success": False, "error": "No rate card entry"}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "entry": entry.as_dict()})


@app.route('/ratecard/<slug>/calibrate', methods=['POST'])
def ratecard_calibrate(slug):
    """Raise a price basis from assumption to evidence, when the data supports it."""
    return jsonify(rate_card.calibrate_from_outcomes(slug))


@app.route('/costs')
def costs_dashboard():
    """AI spend, made visible rather than assumed."""
    return jsonify(budget_guard.dashboard())


@app.route('/costs/budget', methods=['POST'])
def costs_set_budget():
    """Raising a budget above zero is the moment Winston becomes able to spend."""
    body = request.get_json(silent=True) or {}
    try:
        entry = budget_guard.set_budget(
            body.get("provider", ""),
            monthly_budget_usd=float(body.get("monthly_budget_usd", 0)),
            enabled=body.get("enabled"), actor="operator")
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "budget": entry})


@app.route('/providers')
def providers_summary():
    """Routing policy, provider availability, and measured performance."""
    return jsonify(provider_registry.summary())


@app.route('/providers/route/<purpose>')
def providers_route(purpose):
    """Which provider would handle this task, and why."""
    return jsonify(provider_registry.route(purpose).as_dict())


@app.route('/providers/policy', methods=['POST'])
def providers_policy():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"success": True, "policy": provider_registry.set_policy(body)})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route('/pricing')
def pricing_readiness():
    """What Winston needs before it can quote anything."""
    return jsonify(pricing_engine.readiness())


@app.route('/pricing/configure', methods=['POST'])
def pricing_configure():
    """Set the rate card. Winston refuses to quote until this exists."""
    body = request.get_json(silent=True) or {}
    try:
        card = pricing_engine.configure(
            hourly_rate_usd=body.get("hourly_rate_usd"),
            min_margin=body.get("min_margin"))
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "rate_card": card,
                    "readiness": pricing_engine.readiness()})


@app.route('/prospects/<contact_id>/price')
def prospect_price(contact_id):
    """An explainable band for one prospect, or the reason there is none."""
    try:
        brief = writer.build_brief(contact_id)
    except KeyError:
        return jsonify({"error": "Unknown contact"}), 404
    return jsonify({"status": brief["pricing_status"], "pricing": brief["pricing"],
                    "offer": brief.get("recommended_service") or brief.get("recommended_product")})


@app.route('/catalog')
def catalog_list():
    """The YardLink knowledge base. Nothing here is sellable until verified."""
    return jsonify({
        "entries": catalog.list(kind=request.args.get("kind")),
        "readiness": catalog.readiness(),
    })


@app.route('/catalog/<slug>')
def catalog_entry(slug):
    entry = catalog.get(slug)
    if entry is None:
        return jsonify({"error": "Unknown entry"}), 404
    return jsonify({"entry": entry, "proof": catalog.proof_for(slug),
                    "revisions": catalog.revisions(slug, limit=20)})


@app.route('/catalog', methods=['POST'])
def catalog_upsert():
    """Add or edit a product/service/portfolio entry. No code change required."""
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify({"success": True, "entry": catalog.upsert(payload, actor="user")})
    except CatalogValidationError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route('/catalog/<slug>/verify', methods=['POST'])
def catalog_verify(slug):
    """Confirm an entry's claims. Winston will not sell anything unverified."""
    body = request.get_json(silent=True) or {}
    try:
        entry = catalog.verify(slug, actor="user", verified=bool(body.get("verified", True)))
    except UnknownEntry:
        return jsonify({"success": False, "error": "Unknown entry"}), 404
    return jsonify({"success": True, "entry": entry})


@app.route('/catalog/link', methods=['POST'])
def catalog_link():
    """Link proof to an offer, e.g. web-development <- proves <- otonia."""
    body = request.get_json(silent=True) or {}
    try:
        link_id = catalog.link(body.get("from", ""), body.get("to", ""),
                               body.get("relation", "proves"), note=body.get("note", ""))
    except (CatalogValidationError, UnknownEntry) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "link_id": link_id})


@app.route('/prospects/<contact_id>/fit')
def prospect_fit(contact_id):
    """What this prospect needs, what YardLink can provide, and what proves it."""
    try:
        return jsonify(fit_engine.assess(contact_id).as_dict())
    except KeyError:
        return jsonify({"error": "Unknown contact"}), 404


@app.route('/research/<contact_id>', methods=['POST'])
def research_prospect(contact_id):
    """Research one business. Deterministic HTML analysis only -- no AI, no paid services."""
    row = repository.connect().execute(
        "SELECT id, website FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if row is None:
        return jsonify({"success": False, "error": "Unknown contact"}), 404
    if not row["website"]:
        return jsonify({"success": False, "error": "Contact has no website to research"}), 400
    result = research_contact(repository, signal_store, contact_id, row["website"])
    return jsonify({"success": result["status"] == "ok", **result,
                    "signals_detail": signal_store.for_contact(contact_id)})


@app.route('/research/coverage')
def research_coverage():
    """How much of the prospect base has evidence behind it."""
    return jsonify(signal_store.coverage())


@app.route('/funnel')
def funnel():
    """Commercial outcomes. Rates report null where nothing has measured them yet."""
    campaign = request.args.get("campaign_id")
    return jsonify(ledger.funnel(campaign))


@app.route('/prospects/<contact_id>/history')
def prospect_history(contact_id):
    return jsonify(ledger.contact_history(contact_id))


@app.route('/inbox/scan', methods=['POST'])
def inbox_scan():
    """Read-only inbox pass: records replies, bounces, and unsubscribes.

    Hard bounces and unsubscribe requests suppress the address immediately. Nothing
    is deleted and messages are left unread.
    """
    from winston.inbox import InboxScanner
    limit = max(1, min(request.get_json(silent=True).get("limit", 200)
                       if request.get_json(silent=True) else 200, 500))
    try:
        summary = InboxScanner(repository, ledger, ai_service).scan(limit=limit)
        log(f"Inbox scan: {summary['replies']} replies, {summary['hard_bounces']} hard bounces, "
            f"{summary['unsubscribes']} unsubscribes")
        return jsonify({"success": True, **summary})
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route('/health')
def health():
    try:
        counts = repository.counts()
        status = ai_service.status()
        return jsonify({
            "status": "ok", "database": "ok", "counts": counts,
            "legacy_followup_sender": "removed",
            "dry_run": WINSTON_DRY_RUN,
            "ai_mode": status["mode"],
            "provider_health": status["health"],
            "misconfigured_providers": status["misconfigured"],
            "funnel": ledger.funnel(),
            "catalogue": catalog.readiness(),
            "rate_card": rate_card.status(),
            "pricing": pricing_engine.readiness(),
            "routing": {"usable_providers": provider_registry.summary()["usable_providers"]},
            "ai_cost": budget_guard.dashboard()["ai_cost"],
            "spend_capable_providers": budget_guard.dashboard()["spend_capable_providers"],
        })
    except Exception:
        return jsonify({"status": "degraded", "database": "unavailable"}), 503

@app.route('/export_csv')
def export_csv():
    """Download the full contacts database as a CSV file."""
    csv_data = generate_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=winston_contacts_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

if __name__ == '__main__':
    stats = load_stats()
    state["emails_sent"] = stats.get("emails_sent", 0)
    state["social_pending"] = load_social_leads()
    rehydrate_pending_queue()

    print("\nWinston v2 is starting up...")
    print("Press Ctrl+C to stop\n")
    # macOS ControlCenter (AirPlay Receiver) binds *:5000, so the port is configurable
    # rather than a fixed value that collides with the OS on a default install.
    port = int(os.getenv("WINSTON_PORT", "5001"))
    host = os.getenv("WINSTON_HOST", "127.0.0.1")
    print(f"Dashboard: http://{host}:{port}")
    app.run(debug=False, host=host, port=port)
