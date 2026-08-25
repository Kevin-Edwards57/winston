#!/usr/bin/env python3
"""Guided mail credential setup and verification.

The operator types the app password here. It is never echoed, never logged, never
printed back, and never leaves this machine except to authenticate against Google.

The script writes to .env, verifies both protocols, and reports pass or fail. Both
matter and for different reasons: SMTP failing means Winston cannot send, and IMAP
failing means Winston cannot detect replies. A dead credential is invisible in
dry-run, because dry-run returns success before a server is ever contacted.
"""
from __future__ import annotations

import getpass
import imaplib
import os
import re
import smtplib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

GREEN, RED, AMBER, DIM, RESET = "\033[0;32m", "\033[0;31m", "\033[0;33m", "\033[2m", "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}PASS{RESET}  {msg}")


def fail(msg: str, detail: str = "") -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")
    if detail:
        print(f"        {DIM}{detail}{RESET}")


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if line.strip() and not line.strip().startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def write_env_value(key: str, value: str) -> None:
    """Replace one key in .env, preserving comments, order and everything else."""
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    replaced = False
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n")
    ENV.chmod(0o600)   # credentials should not be world readable


def verify(address: str, password: str) -> tuple[bool, bool]:
    print("\nVerifying against Google. Nothing is sent.\n")

    smtp_ok = False
    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15)
        server.login(address, password)
        server.quit()
        ok("SMTP authentication   Winston can send")
        smtp_ok = True
    except smtplib.SMTPAuthenticationError:
        fail("SMTP authentication", "Google rejected the credential. Check the address "
                                    "and regenerate the app password.")
    except Exception as exc:
        fail("SMTP authentication", f"{type(exc).__name__}: {str(exc)[:70]}")

    imap_ok = False
    try:
        box = imaplib.IMAP4_SSL("imap.gmail.com")
        box.login(address, password)
        status, _ = box.select("INBOX", readonly=True)
        box.logout()
        if status == "OK":
            ok("IMAP authentication   Winston can read replies")
            imap_ok = True
        else:
            fail("IMAP mailbox", "authenticated, but INBOX could not be opened")
    except imaplib.IMAP4.error as exc:
        detail = str(exc)[:70]
        hint = ("IMAP may be disabled. Enable it at "
                "https://mail.google.com/mail/u/0/#settings/fwdandpop"
                if "not enabled" in detail.lower() or "unavailable" in detail.lower()
                else "Google rejected the credential.")
        fail("IMAP authentication", f"{detail}  {hint}")
    except Exception as exc:
        fail("IMAP authentication", f"{type(exc).__name__}: {str(exc)[:70]}")

    return smtp_ok, imap_ok


def main() -> int:
    print(f"\n{'Winston mail setup':^64}")
    print("=" * 64)
    print("""
Get an app password:

  1. https://myaccount.google.com/apppasswords
  2. Sign in as the account Winston sends from
  3. Name it "Winston" and copy the 16 character code

If that page is unavailable, turn on 2-Step Verification first:

  https://myaccount.google.com/signinoptions/two-step-verification
""")

    env = read_env()
    current = env.get("GMAIL_ADDRESS", "")
    if current:
        user, _, domain = current.partition("@")
        print(f"Current address: {user[:2]}***@{domain}")
    address = input("Gmail address [keep current]: ").strip() or current
    if not address or "@" not in address:
        fail("No valid address given")
        return 1

    # getpass keeps the secret off the screen and out of shell history.
    password = getpass.getpass("App password (hidden, spaces fine): ").replace(" ", "")
    if not password:
        fail("No password given")
        return 1
    if len(password) != 16:
        print(f"  {AMBER}WARN{RESET}  {len(password)} characters; Gmail app passwords are 16")

    smtp_ok, imap_ok = verify(address, password)

    if not (smtp_ok and imap_ok):
        print(f"\n{RED}Not saved.{RESET} Fix the problems above and run this again.")
        print(f"{DIM}The existing .env was left untouched.{RESET}\n")
        return 1

    write_env_value("GMAIL_ADDRESS", address)
    write_env_value("GMAIL_APP_PASSWORD", password)
    print(f"\n{GREEN}Saved to .env{RESET} (permissions set to 600)")
    print(f"""
Next:

  ./winstonctl doctor     confirm everything else is green
  ./winstonctl restart    pick up the new credential

{DIM}Dry run is still on. Nothing can reach a real inbox until you turn it off
deliberately.{RESET}
""")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was changed.")
        sys.exit(130)
