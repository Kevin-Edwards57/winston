# Mail setup

Winston needs one Gmail account for two jobs: sending outreach over SMTP, and reading
replies over IMAP. Both use the same app password.

## Why this has its own document

A revoked app password is invisible in normal operation. Dry run returns success before
SMTP is ever contacted, so Winston reports every send as fine while never reaching a
server. The credential configured here was dead for months and nothing surfaced it until
a read-only mailbox verification was attempted.

`./winstonctl doctor` now authenticates against both protocols on every run, so this
cannot hide again.

## Setup

```bash
./winstonctl mail
```

It prompts for the address and app password, verifies both protocols, and only writes to
`.env` if both pass. A failed verification leaves the existing configuration untouched,
so a bad paste cannot break a working setup.

The password is read with `getpass`, so it is not echoed to the screen and does not enter
shell history. `.env` is written with permissions `600`.

## Getting an app password

1. <https://myaccount.google.com/apppasswords>
2. Sign in as the account Winston sends from
3. Name it `Winston`, copy the 16 character code

If that page is unavailable, 2-Step Verification is off. App passwords require it:

<https://myaccount.google.com/signinoptions/two-step-verification>

Google displays the code in groups of four. The spaces are cosmetic and are stripped.

## If IMAP fails but SMTP passes

IMAP is disabled on the account. Enable it:

<https://mail.google.com/mail/u/0/#settings/fwdandpop>

Sending will work in that state, but Winston cannot detect replies, so
`reply_tracking_enabled` stays false and the funnel reports reply rates as unknown rather
than zero.

## Verifying

```bash
./winstonctl doctor
```

Under **Mail**, both `SMTP authentication` and `IMAP authentication` must read PASS.

## What the two failures mean

| Failing | Consequence |
|---|---|
| SMTP | Winston cannot send. A campaign fails at authentication. |
| IMAP | Winston cannot read replies. Outreach produces no outcome data, so there is nothing to learn from. |

IMAP failing is the quieter problem. Sending without reply detection generates activity
and no evidence, which is the failure mode the commercial ledger exists to prevent.

## Security

- `.env` is gitignored and has never been committed. Verified across every ref.
- The password is never logged, printed, or included in any error message.
- Winston binds to `127.0.0.1`. The mailbox is reachable only from this machine.
- Rotating the password is just running `./winstonctl mail` again.
