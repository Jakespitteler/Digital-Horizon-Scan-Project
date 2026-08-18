"""
Email notifications for the site scraper.

The notifier does one job: take the changes it is handed, package them into an
email, and send it. It does not scrape, does not work out what changed, and
does not read or write a database. The pipeline around it:

    scraper      finds the pages and files on the site
    diff finder  compares that against the previous run, produces Changes
    notifier     packages those Changes into an email and sends it   <-- here

  changes given      -> digest listing them
  nothing for 7 days -> "no changes detected" note
  otherwise          -> silence

Everything here is a plain function of its arguments. send_message() is the
only part that touches the network, so the wording can be tested without a mail
server, and a mail server being down can't affect anything upstream.

Run `python demo.py` to see it work without a scraper or a mail server.

TODOs are marked below - see README
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from os import environ
from pathlib import Path
from typing import Literal, get_args

# settings
# All from the environment, so no passwords in git. Copy .env.example to .env
# (see README). Defaults are example.com so the demo works with no setup.


def _find_dotenv() -> Path | None:
    """Walk up from this file looking for the repo's .env. None if there isn't one.

    Walks up rather than using the working directory so it's found whether you
    run from the repo root or from inside src/email_sender.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def _load_dotenv(env_file: Path | None = None) -> None:
    """Read the repo's .env into the environment, if there is one.

    Called on import, before the settings below are read. Without it the daily
    job would start with nothing exported and quietly mail the example.com
    defaults -- you only find out when the client never gets a report.

    Anything already exported wins, so `SITE_NAME=x python3 demo.py` still
    overrides the file. Hand rolled rather than python-dotenv on purpose:
    keeping this module free of third party imports is what lets demo.py run
    on a bare python with nothing installed.
    """
    env_file = env_file or _find_dotenv()
    if env_file is None or not env_file.is_file():
        return

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _env_bool(name: str, default: bool) -> bool:
    raw = environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_load_dotenv()

SITE_NAME = environ.get("SITE_NAME", "example.edu.au")
CLIENT_TO = [a.strip() for a in environ.get("CLIENT_TO", "client@example.com").split(",") if a.strip()]
FROM_ADDR = environ.get("FROM_ADDR", "sitewatch@example.com")

# Real sending is opt in. A run with no .env, or a fresh checkout, prints its
# emails instead of mailing whatever CLIENT_TO happens to default to.
DRY_RUN = _env_bool("DRY_RUN", True)

HEARTBEAT_DAYS = 7
# The daily job never fires at exactly the same second, so "7 days since the
# last email" would fail by a few seconds and slip to day 8. Allow a margin.
HEARTBEAT_SLACK_HOURS = 12
HEARTBEAT_DUE = timedelta(days=HEARTBEAT_DAYS, hours=-HEARTBEAT_SLACK_HOURS)

SMTP_HOST = environ.get("SMTP_HOST", "smtp.example.com")
SMTP_PORT = int(environ.get("SMTP_PORT", "587"))
# EMAIL / EMAIL_PASSWORD are the names already used in .env.example.
SMTP_USER = environ.get("EMAIL", "")
SMTP_PASS = environ.get("EMAIL_PASSWORD", "")


# what the diff finder hands over

# The kinds of change the diff finder is allowed to report. Anything else is a
# typo, and Change rejects it on construction rather than letting a change
# nobody prints reach the email.
ChangeType = Literal[
    "PAGE_ADDED",
    "PAGE_CONTENT_CHANGED",
    "PAGE_REMOVED",
    "FILE_ADDED",
    "FILE_CHANGED",
    "FILE_REMOVED",
]

# Order and headings in the email.
SECTIONS = [
    ("PAGE_CONTENT_CHANGED", "Watched pages changed"),
    ("FILE_CHANGED", "Files changed"),
    ("PAGE_ADDED", "New pages"),
    ("FILE_ADDED", "New files"),
    ("PAGE_REMOVED", "Pages no longer reachable"),
    ("FILE_REMOVED", "Files no longer reachable"),
]

# Keep the two lists in step. The digest only prints the types in SECTIONS, so
# a type with no section would silently vanish from the email. Better to blow
# up on import than lose changes quietly.
_missing = set(get_args(ChangeType)) ^ {t for t, _ in SECTIONS}
if _missing:
    raise RuntimeError(f"ChangeType and SECTIONS disagree about: {sorted(_missing)}")
del _missing


@dataclass(frozen=True)
class Change:
    """One thing the diff finder noticed. The notifier's only input.

    `label` is a human readable name for the URL -- a page title or a filename.
    The diff finder picks it because it is the one holding the scraped page;
    the email falls back to the raw URL when there isn't one.

    Frozen because the notifier has no business editing what it was handed.
    A plain dataclass rather than a pydantic model on purpose: it keeps this
    module dependency free, so demo.py runs on a bare python with no install.
    """

    type: ChangeType
    url: str
    label: str | None = None

    def __post_init__(self):
        if self.type not in get_args(ChangeType):
            raise ValueError(f"unknown change type: {self.type}")


#  writing the email
# TODO: plain text only for now. HTML would look nicer, but we keep the plain
# text part regardless -- some corporate mail clients mangle HTML-only email.


def render_digest(changes: list[Change], now: datetime) -> tuple[str, str]:
    """Build (subject, body) for the "what changed" email.

    Buckets the changes by type and prints them in SECTIONS order, skipping
    empty ones.

    No db and no network, so call it directly when you're fiddling with wording.
    """
    n = len(changes)
    noun = "change" if n == 1 else "changes"
    subject = f"[{SITE_NAME}] {n} {noun} detected - {now:%d %b %Y}"

    lines = [
        f"Website monitoring report for {SITE_NAME}",
        f"Checked {now:%d %b %Y, %H:%M} UTC",  # TODO: show Perth time
        "",
        f"{n} {noun} detected since the last report.",
        "",
    ]

    by_type: dict[str, list[Change]] = {}
    for change in changes:
        by_type.setdefault(change.type, []).append(change)

    for type_, heading in SECTIONS:
        items = by_type.get(type_)
        if not items:
            continue
        lines.append(f"{heading} ({len(items)})")
        for change in items:
            lines.append(f"  * {change.label or change.url}")
            lines.append(f"    {change.url}")
        lines.append("")

    lines.append("This is an automated message.")
    return subject, "\n".join(lines)


def render_all_clear(now: datetime, since: datetime) -> tuple[str, str]:
    """Build (subject, body) for the weekly "nothing changed" note.

    `since` is when we last emailed, so the body can name the window. Point of
    this one: silence shouldn't look the same as a broken scraper.
    """
    subject = f"[{SITE_NAME}] No changes detected - week to {now:%d %b %Y}"
    body = "\n".join(
        [
            f"Website monitoring report for {SITE_NAME}",
            f"Checked {now:%d %b %Y, %H:%M} UTC",
            "",
            f"No changes detected between {since:%d %b %Y} and {now:%d %b %Y}.",
            "",
            "Monitoring is running normally.",
            "",
            "This is an automated message.",
        ]
    )
    return subject, body


def build_message(subject: str, body: str) -> EmailMessage:
    """Wrap finished text in an email envelope. No sending.

    The Auto-Submitted header tells other mail systems we're a bot, so we don't
    get out-of-office replies bouncing back.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM_ADDR
    msg["To"] = ", ".join(CLIENT_TO)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(body)
    return msg


#  sending


def send_message(msg: EmailMessage, dry_run: bool | None = None) -> bool:
    """Hand one message to the mail server. Returns whether it went out.

    dry_run=True just prints it, no mail server. demo.py uses it. Left as None
    it follows the DRY_RUN setting, which defaults to printing -- so nothing
    reaches a real inbox until someone sets DRY_RUN=false in .env.

    Returns False rather than raising: a mail server having a bad morning
    shouldn't take the whole daily run down with it.

    TODO: no retries. The notifier no longer keeps an outbox, so a failure here
    means that day's changes are gone -- the diff finder has already moved its
    snapshot on, and tomorrow's run won't see them again. Whoever drives this
    needs to hold the changes until send_message() confirms, or park failures
    somewhere they can be retried. See README.
    """
    if DRY_RUN if dry_run is None else dry_run:
        print("=" * 60)
        print(msg)
        return True

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
    except Exception as exc:
        print(f"  send failed: {exc}")
        return False
    return True


def notify(
    changes: list[Change],
    now: datetime | None = None,
    last_email_at: datetime | None = None,
    dry_run: bool | None = None,
) -> str:
    """Package a run's changes into an email and send it. The way in.

    Call once at the end of each daily run with whatever the diff finder found.

    `last_email_at` is passed in, not looked up -- the notifier keeps no state
    of its own. It only drives the weekly all-clear: without it we can't tell a
    quiet week from a scraper that has been dead for a month, so pass None and
    the all-clear simply never fires.

    Returns "digest" | "all_clear" | "nothing" | "failed", mainly for logging.
    Note there is no "baseline" any more -- on the first ever run the diff
    finder has nothing to compare against, so it reports no changes and this
    stays quiet on its own.

    Pass `now` to fake the clock in tests. `dry_run` left as None follows the
    DRY_RUN setting; pass True or False to override it for one call.
    """
    now = now or datetime.now(UTC)

    if changes:
        subject, body = render_digest(changes, now)
        action = "digest"
    elif last_email_at is not None and now - last_email_at >= HEARTBEAT_DUE:
        subject, body = render_all_clear(now, last_email_at)
        action = "all_clear"
    else:
        return "nothing"

    return action if send_message(build_message(subject, body), dry_run=dry_run) else "failed"
