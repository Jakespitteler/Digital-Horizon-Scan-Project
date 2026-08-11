"""
Email notifications for the site scraper. (Just basic draft)

Runs once a day. Each run scraper writes rows into `events` then this
module decides what to email and sends it. At most one email per day.

  changes found      -> digest listing them
  nothing for 7 days -> "no changes detected" note
  otherwise          -> silence

The scraper never sends mail itself. Keeping apart means can
re-run it while debugging without emailing the client.

Run `python demo.py` to see it work without a scraper or a mail server.

TODOs are marked below — see README
"""

from __future__ import annotations

import json
import os
import smtplib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

# settings
# All from the environment, so no passwords in git. Copy .env.example to .env
# and export it before running (see README). Defaults are example.com so the
# demo works with no setup.

SITE_NAME = os.environ.get("SITE_NAME", "example.edu.au")
CLIENT_TO = [a.strip() for a in os.environ.get("CLIENT_TO", "client@example.com").split(",") if a.strip()]
FROM_ADDR = os.environ.get("FROM_ADDR", "sitewatch@example.com")
HEARTBEAT_DAYS = 7
# The daily job never fires at exactly the same second, so "7 days since the
# last email" would fail by a few seconds and slip to day 8. Allow a margin.
HEARTBEAT_SLACK_HOURS = 12

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.example.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
# EMAIL / EMAIL_PASSWORD are the names already used in .env.example.
SMTP_USER = os.environ.get("EMAIL", "")
SMTP_PASS = os.environ.get("EMAIL_PASSWORD", "")

# Event types the scraper is allowed to write.
EVENT_TYPES = [
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

# Keep the two lists in step. Mark every event as notified but only print
# the types in SECTIONS, so a type with no section vanishes from the email and
# never comes back. Better to blow up on import than lose changes quietly.
_missing = set(EVENT_TYPES) ^ {t for t, _ in SECTIONS}
if _missing:
    raise RuntimeError(f"EVENT_TYPES and SECTIONS disagree about: {sorted(_missing)}")
del _missing


# storage 

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    type        TEXT NOT NULL,
    url         TEXT NOT NULL,
    detail      TEXT,              -- JSON, optional extras like a page title
    detected_at TEXT NOT NULL,
    notified_at TEXT               -- NULL means we haven't emailed it yet
);

CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at    TEXT               -- NULL means still to send
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path="sitewatch.db"):
    """Open the db, making the tables if they're not there. Fine to call every run.

    Autocommit is on, so single writes need no commit()
    (use _transaction() for anything multi-step), and rows come back as
    sqlite3.Row -- that's why we index them by name, e.g. row["type"].
    """
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def _transaction(conn):
    """All-or-nothing for a group of writes.

    Sending is three steps, queue the email, mark the events, move the clock.
    this stopes Crash after step one where the digest sits in the outbox with its events
    still pending -- next run sends the it all again.
    """
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def get_state(conn, key):
    """Read a `state` value, or None if never set.

    `state` is kept between runs. Only two keys in it:
    "baseline_done" and "last_email_at".
    """
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn, key, value):
    """Write a `state` value -- inserts, or overwrites if the key's there."""
    conn.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def add_event(conn, type_, url, detail=None, detected_at=None):
    """The scraper calls this -- one row per change it finds.

    `type_` has to be in EVENT_TYPES; anything else is a typo, so raise
    rather than store an event nobody prints. `detail` is optional extras
    like {"title": ...} or {"filename": ...}, stored as JSON and used for a
    nicer label in the email. `detected_at` is only for tests/demo faking
    the clock.

    Row goes in with notified_at NULL that's what "pending" means.
    """
    if type_ not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {type_}")
    conn.execute(
        "INSERT INTO events(type, url, detail, detected_at) VALUES(?,?,?,?)",
        (
            type_,
            url,
            json.dumps(detail or {}),
            (detected_at or datetime.now(timezone.utc)).isoformat(),
        ),
    )


#  deciding what to send 

def run_notification_cycle(conn, now=None):
    """Call once at the end of each scraper run. The main logic lives here.

    Only picks what to send and drops it in `outbox` no SMTP, that's
    send_pending(). So a dead mail server can't stop us recording changes.

    Returns "baseline" | "digest" | "all_clear" | "nothing", mainly for
    logging (demo.py prints it). Pass `now` to fake the clock in tests.

    Every branch writes inside _transaction() -- see the note there for why.
    """
    now = now or datetime.now(timezone.utc)
    pending = conn.execute(
        "SELECT * FROM events WHERE notified_at IS NULL ORDER BY detected_at, id"
    ).fetchall()

    # First ever run: everything looks new, so record it as the starting
    # point and send nothing. Otherwise the client gets 200 "new page" emails
    # on day one.
    if get_state(conn, "baseline_done") != "1":
        with _transaction(conn):
            _mark_notified(conn, pending, now)
            set_state(conn, "baseline_done", "1")
            set_state(conn, "last_email_at", now.isoformat())
        return "baseline"

    # Because it runs once a day, everything found in a run goes out as one
    # email and there's no need to rate limit.

    # TODO (safety net): if most checks failed the site is probably just down,
    # and "every page was deleted" is the wrong conclusion to email.
    # Needs the scraper to tell us how many checks passed/failed per run.

    if pending:
        subject, body = render_digest(pending, now)
        with _transaction(conn):
            _queue(conn, subject, body, now)
            _mark_notified(conn, pending, now)
            set_state(conn, "last_email_at", now.isoformat())
        return "digest"

    # Nothing changed see if it's been a week. Any email resets the clock,
    # so a change on Tuesday pushes the next all-clear to the Tuesday after.
    last = get_state(conn, "last_email_at")
    due = timedelta(days=HEARTBEAT_DAYS, hours=-HEARTBEAT_SLACK_HOURS)
    if last and now - datetime.fromisoformat(last) >= due:
        subject, body = render_all_clear(now, datetime.fromisoformat(last))
        with _transaction(conn):
            _queue(conn, subject, body, now)
            set_state(conn, "last_email_at", now.isoformat())
        return "all_clear"

    return "nothing"


def _queue(conn, subject, body, now):
    """Drop a finished email in the outbox. sent_at NULL = still to go out."""
    conn.execute(
        "INSERT INTO outbox(subject, body, created_at) VALUES(?,?,?)",
        (subject, body, now.isoformat()),
    )


def _mark_notified(conn, rows, now):
    """Stamp notified_at so these events don't get emailed again.

    The f-string only builds the "?,?,?" placeholders -- the ids are still
    bound as params, so it's not an injection hole.
    """
    if not rows:
        return
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE events SET notified_at = ? WHERE id IN ({marks})",
        [now.isoformat(), *ids],
    )


#  writing the email 
# TODO: plain text only for now. HTML would look nicer, but we keep the plain
# text part regardless -- some corporate mail clients mangle HTML-only email.


def render_digest(rows, now):
    """Build (subject, body) for " what changed" email.

    Buckets the events by type and prints them in SECTIONS order, skipping
    empty ones. Each item shows the title/filename from `detail` if we got
    one, else just the URL.

    No db or network just call it directly when you're fiddling with wording.
    """
    n = len(rows)
    noun = "change" if n == 1 else "changes"
    subject = f"[{SITE_NAME}] {n} {noun} detected - {now:%d %b %Y}"

    lines = [
        f"Website monitoring report for {SITE_NAME}",
        f"Checked {now:%d %b %Y, %H:%M} UTC",  # TODO: show Perth time
        "",
        f"{n} {noun} detected since the last report.",
        "",
    ]
    by_type = {}
    for row in rows:
        by_type.setdefault(row["type"], []).append(row)

    for type_, heading in SECTIONS:
        items = by_type.get(type_)
        if not items:
            continue
        lines.append(f"{heading} ({len(items)})")
        for row in items:
            detail = json.loads(row["detail"] or "{}")
            label = detail.get("title") or detail.get("filename") or row["url"]
            lines.append(f"  * {label}")
            lines.append(f"    {row['url']}")
        lines.append("")

    lines.append("This is an automated message.")
    return subject, "\n".join(lines)


def render_all_clear(now, since):
    """Build (subject, body) for the weekly "nothing changed" note.

    `since` is when last emailed, so the body can name the window. Point
    of this one: silence shouldn't look the same as a broken scraper.
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


#  sending 

def send_pending(conn, now=None, dry_run=False):
    """Send everything in the outbox. Returns how many went out.

    Send oldest first

    Stamp sent_at *after* SMTP takes it, on purpose, crash in between and
    that one email goes twice, but the other way round we'd lose it and
    never know, i think better to double up than never send. 

    dry_run=True just prints the messages, no mail server. demo.py uses it.

    The Auto-Submitted header tells other mail systems we're a bot, so we
    don't get out-of-office replies bouncing back.

    TODO: no retries yet. If SMTP is down the row stays unsent and we try
    again next run, which is fine, but it'll retry every 15 minutes forever
    and nobody gets told. Needs an attempt counter and a backoff.
    """
    now = now or datetime.now(timezone.utc)
    sent = 0
    rows = conn.execute(
        "SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY id"
    ).fetchall()

    for row in rows:
        msg = EmailMessage()
        msg["Subject"] = row["subject"]
        msg["From"] = FROM_ADDR
        msg["To"] = ", ".join(CLIENT_TO)
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(row["body"])

        if dry_run:
            print("=" * 60)
            print(msg)
        else:
            try:
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
                    smtp.starttls()
                    if SMTP_USER:
                        smtp.login(SMTP_USER, SMTP_PASS)
                    smtp.send_message(msg)
            except Exception as exc:
                print(f"  send failed for outbox {row['id']}: {exc}")
                continue

        conn.execute(
            "UPDATE outbox SET sent_at = ? WHERE id = ?", (now.isoformat(), row["id"])
        )
        sent += 1
    return sent
