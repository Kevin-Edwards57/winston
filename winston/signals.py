"""Digital signal extraction — the evidence layer underneath every recommendation.

Winston already fetched every prospect's website, parsed it, took the email address,
and threw the HTML away. Everything needed to judge a business passed through memory
and was discarded, which is why nothing downstream could score a prospect without
inventing the inputs.

This module reads signals out of HTML that has already been fetched. No extra network
requests, no AI calls, no paid services. Detection is deterministic pattern matching
against platform fingerprints, which is both cheaper and more auditable than asking a
model to guess whether a site has online booking.

Three rules hold:

**Every signal carries its evidence.** A detection records the exact token that matched
and the URL it matched on, so any downstream claim can be traced to something observable.

**Absence is not negation.** Failing to find a booking widget yields `unknown`, not
"has no booking". A site that could not be fetched produces no signals at all rather
than a business that looks maximally broken. Confusing "we did not observe X" with
"X is false" is how a scoring system invents opportunities that are not there.

**Confidence is recorded, not implied.** A platform fingerprint in a script tag is
strong evidence. A keyword in link text is weak. Both are useful; conflating them
is not.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable

from .repository import WinstonRepository, stable_id, utc_now

SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS business_signals (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    signal TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    evidence TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT 'deterministic',
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(contact_id, signal)
);
CREATE INDEX IF NOT EXISTS business_signals_contact ON business_signals(contact_id);
CREATE INDEX IF NOT EXISTS business_signals_name ON business_signals(signal);

-- When a business was last researched, and whether it worked. Without this,
-- "no signals" is ambiguous between never-looked and looked-and-found-nothing.
CREATE TABLE IF NOT EXISTS research_runs (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    signals_found INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS research_runs_contact ON research_runs(contact_id, started_at DESC);
"""

# ── Platform fingerprints ────────────────────────────────────────────────
# Ordered most-specific first. Each entry is (label, pattern, confidence).
# Confidence reflects how uniquely the token identifies the platform: a vendor
# script host is near-certain, a bare brand word in text is not.

BOOKING_PLATFORMS = [
    ("Booksy", r"booksy\.com", 0.95),
    ("Calendly", r"calendly\.com", 0.95),
    ("Square Appointments", r"squareup\.com/appointments|square\.site/book", 0.95),
    ("Acuity", r"acuityscheduling\.com|squarespacescheduling\.com", 0.95),
    ("Mindbody", r"mindbodyonline\.com|mindbody\.io", 0.95),
    ("Vagaro", r"vagaro\.com", 0.95),
    ("Schedulicity", r"schedulicity\.com", 0.95),
    ("Setmore", r"setmore\.com", 0.95),
    ("Resy", r"resy\.com", 0.95),
    ("OpenTable", r"opentable\.com", 0.95),
    ("Tock", r"exploretock\.com", 0.95),
    ("StyleSeat", r"styleseat\.com", 0.95),
    ("Fresha", r"fresha\.com", 0.95),
    ("generic booking link", r"/book-?(now|online|appointment)|/appointments?\b|/reservations?\b", 0.55),
]

ORDERING_PLATFORMS = [
    ("Toast", r"toasttab\.com", 0.95),
    ("ChowNow", r"chownow\.com", 0.95),
    ("Slice", r"slicelife\.com", 0.95),
    ("DoorDash", r"doordash\.com", 0.9),
    ("Grubhub", r"grubhub\.com", 0.9),
    ("Uber Eats", r"ubereats\.com", 0.9),
    ("Clover", r"clover\.com/online-ordering", 0.95),
    ("Square Online", r"square\.site", 0.85),
    ("generic ordering link", r"/order-?online|/online-?order", 0.55),
]

ECOMMERCE_PLATFORMS = [
    ("Shopify", r"cdn\.shopify\.com|shopify\.com/s/files", 0.95),
    ("WooCommerce", r"woocommerce", 0.9),
    ("BigCommerce", r"bigcommerce\.com", 0.95),
    ("Magento", r"/static/version\d+/frontend|magento", 0.85),
    ("Ecwid", r"ecwid\.com", 0.9),
    ("Wix Stores", r"wixstores", 0.9),
]

CMS_PLATFORMS = [
    ("WordPress", r"/wp-content/|/wp-includes/|wp-json", 0.95),
    ("Wix", r"wix\.com|wixstatic\.com|parastorage\.com", 0.95),
    ("Squarespace", r"squarespace\.com|squarespace-cdn\.com", 0.95),
    ("Weebly", r"weebly\.com|editmysite\.com", 0.95),
    ("GoDaddy Website Builder", r"godaddysites\.com|img1\.wsimg\.com", 0.95),
    ("Webflow", r"webflow\.com|assets\.website-files\.com", 0.95),
    ("Duda", r"dudamobile\.com|dudaone", 0.9),
    ("Shopify", r"cdn\.shopify\.com", 0.9),
    ("Framer", r"framer\.app|framerusercontent\.com", 0.9),
]

ANALYTICS_TOOLS = [
    ("Google Analytics 4", r"gtag/js\?id=G-|googletagmanager\.com/gtag", 0.9),
    ("Google Tag Manager", r"googletagmanager\.com/gtm\.js", 0.9),
    ("Universal Analytics (deprecated)", r"google-analytics\.com/analytics\.js|ua-\d{4,}-\d", 0.85),
    ("Meta Pixel", r"connect\.facebook\.net/.*/fbevents\.js", 0.9),
    ("Hotjar", r"static\.hotjar\.com", 0.9),
]

# Platforms that ship measurement whether or not a tag appears in static HTML.
# A Shopify store has analytics by construction, so "no GA script" says nothing.
PLATFORMS_WITH_BUILTIN_ANALYTICS = [
    ("Shopify", r"cdn\.shopify\.com|shopify\.com/s/files|shopify\.theme", 0.95),
    ("Wix", r"wix\.com|wixstatic\.com|parastorage\.com", 0.95),
    ("Squarespace", r"squarespace\.com|squarespace-cdn\.com", 0.95),
    ("BigCommerce", r"bigcommerce\.com", 0.95),
    ("Webflow", r"webflow\.com|assets\.website-files\.com", 0.9),
    ("Square Online", r"square\.site", 0.85),
    ("WooCommerce", r"woocommerce", 0.9),
    ("GoDaddy Website Builder", r"godaddysites\.com|img1\.wsimg\.com", 0.9),
]

# Tag managers and consent gates load analytics after the initial document, so
# their presence means a static scan cannot see what is downstream of them.
DEFERRED_ANALYTICS_INDICATORS = [
    ("Google Tag Manager", r"googletagmanager\.com/gtm\.js|datalayer", 0.9),
    ("Consent manager", r"cookiebot|onetrust|cookieyes|termly|iubenda|usercentrics|"
                        r"cookie-?consent|cookiehub|klaro|osano", 0.85),
    ("Tag loader", r"segment\.com/analytics\.js|cdn\.segment|tealium|ensighten", 0.9),
]

CHAT_TOOLS = [
    ("Intercom", r"widget\.intercom\.io", 0.95),
    ("Tawk.to", r"embed\.tawk\.to", 0.95),
    ("Drift", r"js\.driftt\.com", 0.95),
    ("Tidio", r"code\.tidio\.co", 0.95),
    ("Crisp", r"client\.crisp\.chat", 0.95),
    ("Facebook Messenger widget", r"connect\.facebook\.net/.*/sdk/xfbml\.customerchat", 0.9),
]

# Platforms that build the DOM client-side. On these, static HTML shows no <form>,
# no analytics tag, and no chat widget even when all three exist in the live page.
# Real-site validation caught this: Wix and Squarespace prospects were reporting
# "no contact form" purely because the markup had not run yet.
JS_RENDERED_PLATFORMS = [
    ("Wix", r"wix\.com|wixstatic\.com|parastorage\.com", 0.95),
    ("Squarespace", r"squarespace\.com|squarespace-cdn\.com", 0.95),
    ("Framer", r"framer\.app|framerusercontent\.com", 0.9),
    ("Duda", r"dudamobile\.com|dudaone", 0.9),
    ("GoDaddy Website Builder", r"godaddysites\.com|img1\.wsimg\.com", 0.9),
    ("React/Next app shell", r"__NEXT_DATA__|data-reactroot|id=\"root\"></div>", 0.7),
]

# Signals whose absence is genuinely informative, because the marker would be
# present in the HTML if the feature existed. Everything else stays unknown.
CLOSED_WORLD_SIGNALS = {
    "mobile_responsive", "has_ssl", "has_contact_form", "has_analytics", "has_chat_widget",
}


@dataclass
class Signal:
    """One observation about a business, with the evidence that produced it."""
    name: str
    value: Any
    confidence: float
    evidence: str = ""
    source_url: str = ""
    method: str = "deterministic"

    def as_row(self) -> dict[str, Any]:
        return {
            "signal": self.name,
            "value_json": json.dumps(self.value),
            "confidence": round(float(self.confidence), 3),
            "evidence": self.evidence[:500],
            "source_url": self.source_url[:500],
            "method": self.method,
        }


class _PageStructureParser(HTMLParser):
    """Collects the structural facts that regexes read badly."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_viewport_meta = False
        self.viewport_content = ""
        self.form_count = 0
        self.input_types: set[str] = set()
        self.image_count = 0
        self.images_without_alt = 0
        self.script_srcs: list[str] = []
        self.title = ""
        self.has_h1 = False
        self.meta_description = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): (value or "") for key, value in attrs}
        if tag == "meta":
            name = attributes.get("name", "").casefold()
            if name == "viewport":
                self.has_viewport_meta = True
                self.viewport_content = attributes.get("content", "")
            elif name == "description":
                self.meta_description = attributes.get("content", "")
        elif tag == "form":
            self.form_count += 1
        elif tag == "input":
            self.input_types.add(attributes.get("type", "text").casefold())
        elif tag == "img":
            self.image_count += 1
            if not attributes.get("alt"):
                self.images_without_alt += 1
        elif tag == "script" and attributes.get("src"):
            self.script_srcs.append(attributes["src"])
        elif tag == "h1":
            self.has_h1 = True
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()


def _match_platform(haystack: str, catalogue: list[tuple[str, str, float]]) -> tuple[str, str, float] | None:
    """Return (label, matched_text, confidence) for the first fingerprint that hits."""
    for label, pattern, confidence in catalogue:
        match = re.search(pattern, haystack, re.IGNORECASE)
        if match:
            return label, match.group(0)[:120], confidence
    return None


def extract_signals(html: str, url: str = "") -> list[Signal]:
    """Read every deterministic signal out of one page of already-fetched HTML."""
    if not html or not html.strip():
        return []

    signals: list[Signal] = []
    lowered = html.casefold()
    structure = _PageStructureParser()
    try:
        structure.feed(html)
    except Exception:
        pass  # Malformed markup is common; structural signals degrade, others still work.

    def add(name: str, value: Any, confidence: float, evidence: str = "") -> None:
        signals.append(Signal(name, value, confidence, evidence, url))

    # ── Capability platforms. Presence is strong; absence stays unknown. ──
    for signal_name, catalogue in (
        ("online_booking", BOOKING_PLATFORMS),
        ("online_ordering", ORDERING_PLATFORMS),
        ("ecommerce", ECOMMERCE_PLATFORMS),
        ("cms", CMS_PLATFORMS),
    ):
        found = _match_platform(lowered, catalogue)
        if found:
            label, evidence, confidence = found
            add(signal_name, label, confidence, f"matched {evidence!r}")

    # On a client-rendered page, "not found in the HTML" is not evidence of absence.
    # Positive detections still count; negatives are withheld as unknown.
    js_platform = _match_platform(lowered, JS_RENDERED_PLATFORMS)
    client_rendered = js_platform is not None
    if client_rendered:
        add("client_rendered", js_platform[0], js_platform[2],
            f"matched {js_platform[1]!r}; static HTML understates this site")

    def add_closed_world(name: str, observed: bool, positive_evidence: str,
                         negative_evidence: str, confidence: float) -> None:
        """Emit a negative only when the page could actually have shown it."""
        if observed:
            add(name, True, confidence, positive_evidence)
        elif not client_rendered:
            add(name, False, confidence, negative_evidence)
        # else: withheld -- unknown, because the DOM had not rendered

    # ── Measurement, as four states rather than a boolean ──
    # "No analytics tag in static HTML" is not "this business does not measure".
    # A Shopify store measures by construction; a tag manager loads analytics after
    # the document; a consent gate defers it until the visitor accepts. Treating an
    # unseen tag as an absent one manufactured commercial opportunities that were not
    # there, so absence is only ever confirmed when nothing could be hiding it.
    analytics = _match_platform(lowered, ANALYTICS_TOOLS)
    platform = _match_platform(lowered, PLATFORMS_WITH_BUILTIN_ANALYTICS)
    deferred = _match_platform(lowered, DEFERRED_ANALYTICS_INDICATORS)

    limitations: list[str] = []
    if client_rendered:
        limitations.append("page is client-rendered; scripts may load after the document")
    if platform:
        limitations.append(f"{platform[0]} provides platform-level analytics not visible in markup")
    if deferred:
        limitations.append(f"{deferred[0]} loads analytics after the initial document")
    if structure.script_srcs and len(structure.script_srcs) > 12:
        limitations.append(f"{len(structure.script_srcs)} external scripts; any may load analytics")

    if analytics:
        state, confidence = "detected", analytics[2]
        evidence = f"matched {analytics[1]!r}"
    elif deferred:
        state, confidence = "not_detected", 0.35
        evidence = f"{deferred[0]} present; analytics is likely downstream of it"
    elif platform or client_rendered:
        state, confidence = "not_detected", 0.3
        evidence = "no analytics tag in static HTML, but the platform can supply it"
    elif limitations:
        state, confidence = "not_detected", 0.45
        evidence = "no analytics tag found, with detection limits present"
    else:
        # Server-rendered, no platform that supplies analytics, no tag manager,
        # no consent gate, few scripts. Absence here is worth asserting.
        state, confidence = "confirmed_absence", 0.75
        evidence = ("no analytics of any recognised kind, on a server-rendered page "
                    "with no tag manager, consent gate or platform-level analytics")

    add("measurement_state", state, confidence, evidence)
    if limitations:
        add("measurement_limitations", limitations, 0.9, "; ".join(limitations)[:400])
    if analytics:
        add("analytics_tool", analytics[0], analytics[2], f"matched {analytics[1]!r}")
    if platform:
        add("analytics_platform", platform[0], 0.9, f"matched {platform[1]!r}")

    # Retained for compatibility, but only ever True on a positive detection.
    if analytics:
        add("has_analytics", True, analytics[2], f"matched {analytics[1]!r}")

    chat = _match_platform(lowered, CHAT_TOOLS)
    add_closed_world("has_chat_widget", bool(chat),
                     f"matched {chat[1]!r}" if chat else "",
                     "no chat widget script found", 0.9 if chat else 0.6)
    if chat:
        add("chat_tool", chat[0], chat[2], f"matched {chat[1]!r}")

    # ── Structural quality. The marker would be present if the feature existed. ──
    add("mobile_responsive", structure.has_viewport_meta,
        0.9 if structure.has_viewport_meta else 0.85,
        f"viewport meta: {structure.viewport_content[:80]!r}" if structure.has_viewport_meta
        else "no <meta name=viewport> — page will not adapt to phones")

    # Must be the URL actually served, not the one on file. A stored http:// address
    # that 301s to https:// otherwise reports as insecure -- a false negative that
    # would penalise a business for a stale directory listing.
    if url:
        secure = url.casefold().startswith("https://")
        add("has_ssl", secure, 0.99,
            f"final URL scheme is {url.split('://')[0]} (after redirects)")

    contact_form = structure.form_count > 0 and bool(
        structure.input_types & {"email", "tel", "text", "textarea", "submit"})
    add_closed_world("has_contact_form", contact_form,
                     f"{structure.form_count} form(s), input types: {sorted(structure.input_types)[:6]}",
                     "no <form> element on the page", 0.8)

    add("has_title", bool(structure.title), 0.95,
        f"title: {structure.title[:80]!r}" if structure.title else "page has no <title>")
    add("has_meta_description", bool(structure.meta_description), 0.9,
        "meta description present" if structure.meta_description else "no meta description")
    add("has_h1", structure.has_h1, 0.9,
        "h1 present" if structure.has_h1 else "no <h1> heading")

    if structure.image_count:
        ratio = structure.images_without_alt / structure.image_count
        add("image_alt_coverage", round(1 - ratio, 2), 0.85,
            f"{structure.image_count - structure.images_without_alt}/{structure.image_count} images have alt text")

    # ── Freshness. A stale copyright is weak evidence but real. ──
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(20\d{2})",
                                        lowered)]
    if years:
        latest = max(years)
        current = datetime.now(timezone.utc).year
        add("copyright_year", latest, 0.7, f"footer copyright reads {latest}")
        add("years_since_copyright_update", max(0, current - latest), 0.6,
            f"copyright {latest} against current year {current}")

    add("script_count", len(structure.script_srcs), 0.9,
        f"{len(structure.script_srcs)} external script(s)")
    add("page_bytes", len(html), 0.99, f"{len(html)} bytes of HTML")

    return signals


def merge_page_signals(per_page: Iterable[tuple[str, list[Signal]]]) -> list[Signal]:
    """Fold signals from several pages of one site into one view.

    Capability signals are optimistic: booking found on the contact page counts even
    if the homepage lacked it. Structural signals prefer the homepage, which is the
    first entry. Highest confidence wins ties.
    """
    best: dict[str, Signal] = {}
    for _, signals in per_page:
        for signal in signals:
            existing = best.get(signal.name)
            if existing is None:
                best[signal.name] = signal
                continue
            # A positive observation beats a negative one; otherwise higher confidence wins.
            existing_truthy = bool(existing.value)
            incoming_truthy = bool(signal.value)
            if incoming_truthy and not existing_truthy:
                best[signal.name] = signal
            elif incoming_truthy == existing_truthy and signal.confidence > existing.confidence:
                best[signal.name] = signal
    return list(best.values())


class SignalStore:
    """Persists observed signals with provenance and staleness."""

    def __init__(self, repository: WinstonRepository) -> None:
        self.repository = repository

    def initialize(self) -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.executescript(SIGNALS_SCHEMA)

    def start_run(self, contact_id: str, url: str) -> str:
        run_id = stable_id("research_run", contact_id, utc_now())
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO research_runs(id,contact_id,url,status,started_at,created_at)
                   VALUES(?,?,?,'running',?,?)""",
                (run_id, contact_id, url, now, now))
        return run_id

    def complete_run(self, run_id: str, *, status: str, pages: int = 0,
                     signals: int = 0, error: str = "") -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE research_runs SET status=?,pages_fetched=?,signals_found=?,
                                            error=?,completed_at=? WHERE id=?""",
                (status, pages, signals, error[:500], utc_now(), run_id))

    def record(self, contact_id: str, signals: list[Signal]) -> int:
        """Upsert signals for a business. Latest observation wins."""
        if not signals:
            return 0
        now = utc_now()
        with self.repository.transaction(immediate=True) as connection:
            for signal in signals:
                row = signal.as_row()
                connection.execute(
                    """INSERT INTO business_signals(id,contact_id,signal,value_json,confidence,
                                                    evidence,source_url,method,observed_at,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(contact_id,signal) DO UPDATE SET
                           value_json=excluded.value_json, confidence=excluded.confidence,
                           evidence=excluded.evidence, source_url=excluded.source_url,
                           method=excluded.method, observed_at=excluded.observed_at""",
                    (stable_id("signal", contact_id, row["signal"]), contact_id, row["signal"],
                     row["value_json"], row["confidence"], row["evidence"], row["source_url"],
                     row["method"], now, now))
        return len(signals)

    def for_contact(self, contact_id: str) -> dict[str, dict[str, Any]]:
        """Every signal known about one business, keyed by signal name."""
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT signal,value_json,confidence,evidence,source_url,method,observed_at
                   FROM business_signals WHERE contact_id=?""", (contact_id,)).fetchall()
        return {
            row["signal"]: {
                "value": json.loads(row["value_json"]),
                "confidence": row["confidence"],
                "evidence": row["evidence"],
                "source_url": row["source_url"],
                "method": row["method"],
                "observed_at": row["observed_at"],
            }
            for row in rows
        }

    def last_researched(self, contact_id: str) -> str | None:
        with self.repository.read() as connection:
            row = connection.execute(
                """SELECT completed_at FROM research_runs
                   WHERE contact_id=? AND status='ok' AND completed_at IS NOT NULL
                   ORDER BY completed_at DESC LIMIT 1""", (contact_id,)).fetchone()
        return row["completed_at"] if row else None

    def coverage(self) -> dict[str, Any]:
        """How much of the prospect base has actually been researched."""
        with self.repository.read() as connection:
            total = connection.execute("SELECT COUNT(*) n FROM contacts").fetchone()["n"]
            researched = connection.execute(
                "SELECT COUNT(DISTINCT contact_id) n FROM business_signals").fetchone()["n"]
            runs = connection.execute(
                "SELECT status, COUNT(*) n FROM research_runs GROUP BY status").fetchall()
        return {
            "contacts": total,
            "researched": researched,
            "unresearched": total - researched,
            "coverage": round(researched / total, 4) if total else None,
            "runs_by_status": {row["status"]: row["n"] for row in runs},
        }


# ── Research pipeline ────────────────────────────────────────────────────

MAX_RESEARCH_BYTES = 3_000_000
RESEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WinstonBot/1.0; +https://yardlinkstudio.com)",
    "Accept": "text/html,application/xhtml+xml",
}


def fetch_page(url: str, *, timeout: int = 12) -> tuple[str, str]:
    """Fetch one page, returning (html, final_url).

    The final URL matters: a stored ``http://`` address that redirects to HTTPS
    would otherwise be recorded as having no SSL, penalising a business for a
    stale directory listing rather than anything about the business.
    """
    import requests
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "", url
    try:
        response = requests.get(url, headers=RESEARCH_HEADERS, timeout=timeout, allow_redirects=True)
        if response.status_code != 200:
            return "", str(response.url)
        if "html" not in response.headers.get("Content-Type", "").casefold():
            return "", str(response.url)
        if len(response.content) > MAX_RESEARCH_BYTES:
            return "", str(response.url)
        return response.text, str(response.url)
    except Exception:
        return "", url


def research_site(url: str, *, extra_paths: Iterable[str] = ("/contact", "/about", "/book"),
                  max_pages: int = 3) -> tuple[list[Signal], int, str]:
    """Fetch a site and read every deterministic signal from it.

    Returns (signals, pages_fetched, status). A site that cannot be fetched yields
    no signals and status ``unreachable`` -- deliberately not a business that looks
    maximally broken, which is what a naive implementation would conclude.
    """
    home_html, final_url = fetch_page(url)
    if not home_html:
        return [], 0, "unreachable"

    pages: list[tuple[str, list[Signal]]] = [(final_url, extract_signals(home_html, final_url))]
    fetched = 1

    base = final_url.rstrip("/")
    for path in extra_paths:
        if fetched >= max_pages:
            break
        candidate = base + path
        html, resolved = fetch_page(candidate, timeout=8)
        if html:
            pages.append((resolved, extract_signals(html, resolved)))
            fetched += 1

    return merge_page_signals(pages), fetched, "ok"


def research_contact(repository: WinstonRepository, store: SignalStore,
                     contact_id: str, website: str) -> dict[str, Any]:
    """Research one business end to end and persist what was observed."""
    run_id = store.start_run(contact_id, website)
    try:
        signals, pages, status = research_site(website)
    except Exception as exc:
        store.complete_run(run_id, status="error", error=f"{type(exc).__name__}: {exc}")
        return {"contact_id": contact_id, "status": "error", "signals": 0}

    recorded = store.record(contact_id, signals) if signals else 0
    store.complete_run(run_id, status=status, pages=pages, signals=recorded)
    repository.add_event("prospect.researched", entity_type="contact", entity_id=contact_id,
                         details={"status": status, "pages": pages, "signals": recorded})
    return {"contact_id": contact_id, "status": status, "pages": pages, "signals": recorded}
