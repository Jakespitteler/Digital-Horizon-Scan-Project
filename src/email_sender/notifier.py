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
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

# settings
# TODO: move these out of source before we hand anything over.
#       SMTP password especially must not end up in git.

SITE_NAME = "example.edu.au"
CLIENT_TO = ["client@example.com"]
FROM_ADDR = "sitewatch@example.com"
HEARTBEAT_DAYS = 7
# The daily job never fires at exactly the same second, so "7 days since the
# last email" would fail by a few seconds and slip to day 8. Allow a margin.
HEARTBEAT_SLACK_HOURS = 12

SMTP_HOST = "smtp.example.com"
SMTP_PORT = 587
SMTP_USER = ""
SMTP_PASS = ""

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
    """Open the database, creating the tables if they aren't there yet.

    Safe to call on every run — SCHEMA is all CREATE TABLE IF NOT EXISTS.
    isolation_level=None puts sqlite3 in autocommit mode, so nothing here
    needs an explicit conn.commit(). Rows come back as sqlite3.Row, which is
    why the rest of the file indexes them by name (row["type"]).
    """
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def get_state(conn, key):
    """Read one value from the `state` table, or None if was never set.

    `state` is value that survives between runs.
    Two keys are used: "baseline_done" and "last_email_at".
    """
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn, key, value):
    """Write a `state` value, inserting or overwriting as needed."""
    conn.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def add_event(conn, type_, url, detail=None, detected_at=None):
    """The scraper calls this.

    Records one detected change. `type_` must be one of EVENT_TYPES —
    anything else is a typo in the scraper and raises rather than silently
    creating an event nobody renders. `detail` is an optional dict of extras
    (e.g. {"title": ...} or {"filename": ...}) that render_digest uses for a
    friendlier label; it's stored as JSON text. `detected_at` defaults to now
    and is only passed in by tests/demo that need to fake the clock.

    The row lands with notified_at NULL, which is what marks it as pending
    for the next run_notification_cycle().
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
    """Call once at the end of each scraper run. Returns what it decided.

    Decides *what* to send and queues it in `outbox`; it does not talk to
    SMTP. send_pending() does the actual sending, so a slow or broken mail
    server can't stop us recording what changed.

    NOT crash-safe yet. connect() uses autocommit, so the queue / mark /
    set_state trio below are three separate commits. Dying between the first
    two leaves the digest in the outbox *and* the events still pending, and
    the next run emails the same changes a second time. Wrapping the three
    in one BEGIN..COMMIT fixes it — see README.

    Returns one of "baseline" | "digest" | "all_clear" | "nothing", mostly so
    the caller can log it (demo.py prints it).

    `now` is injectable so tests/demo can pretend a week has passed.
    """
    now = now or datetime.now(timezone.utc)
    pending = conn.execute(
        "SELECT * FROM events WHERE notified_at IS NULL ORDER BY detected_at, id"
    ).fetchall()

    # First ever run: everything looks new, so record it as the starting
    # point and send nothing. Otherwise the client gets 200 "new page" emails
    # on day one.
    if get_state(conn, "baseline_done") != "1":
        _mark_notified(conn, pending, now)
        set_state(conn, "baseline_done", "1")
        set_state(conn, "last_email_at", now.isoformat())
        return "baseline"

    # Because we run once a day, everything found in a run goes out as one
    # email and there's no need to rate limit. If the schedule ever changes
    # to hourly, revisit this — it would need a minimum gap between digests.

    # TODO (safety net): if most checks failed the site is probably just down,
    # and "every page was deleted" is the wrong conclusion to email anyone.
    # Needs the scraper to tell us how many checks passed/failed per run.

    if pending:
        subject, body = render_digest(pending, now)
        _queue(conn, subject, body, now)
        _mark_notified(conn, pending, now)
        set_state(conn, "last_email_at", now.isoformat())
        return "digest"

    # Nothing changed. check for if been a week
    # Any email resets the clock, so a change on Tuesday pushes the next possible all-clear out to the following Tuesday.
    last = get_state(conn, "last_email_at")
    due = timedelta(days=HEARTBEAT_DAYS, hours=-HEARTBEAT_SLACK_HOURS)
    if last and now - datetime.fromisoformat(last) >= due:
        subject, body = render_all_clear(now, datetime.fromisoformat(last))
        _queue(conn, subject, body, now)
        set_state(conn, "last_email_at", now.isoformat())
        return "all_clear"

    return "nothing"


def _queue(conn, subject, body, now):
    """Put a finished email in the outbox with sent_at NULL (= still to send)."""
    conn.execute(
        "INSERT INTO outbox(subject, body, created_at) VALUES(?,?,?)",
        (subject, body, now.isoformat()),
    )


def _mark_notified(conn, rows, now):
    """Stamp notified_at on these events so they're never emailed twice.

    Builds a "?,?,?" placeholder list to match the number of ids — the ids
    themselves are still bound as parameters, not formatted into the SQL.
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
# TODO: plain text only for now. An HTML version would look better but the plain text part has to stay either way some corporate mail clients make a mess of HTML-only email.


def render_digest(rows, now):
    """Build the (subject, body) for a "here's what changed" email.

    Groups the pending events by type and prints them in SECTIONS order, so
    the things the client cares about most come first and empty categories
    are skipped. Each item shows a human label — the title/filename out of
    `detail` if the scraper gave us one, otherwise just the URL — with the
    URL underneath.

    Pure function: no database, no sending. Handy to call directly when
    you're tweaking the wording.
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
    """Build the (subject, body) for the weekly "nothing changed" note.

    `since` is the timestamp of the last email we sent, so the body can name
    the window it's covering. This exists so silence never looks the same as
    a broken scraper — the client hears from us at least every HEARTBEAT_DAYS.
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

    Walks the unsent rows oldest first, builds an EmailMessage from each and
    hands it to SMTP, then stamps sent_at so it won't go out again. A row is
    only stamped after a successful send: if one message fails we log it,
    leave it unsent and carry on with the rest, and next run picks it up.

    dry_run=True prints the messages instead of connecting to a mail server
    — that's what demo.py passes.

    Auto-Submitted: auto-generated tells other mail systems this is a robot,
    so they don't reply or fire an out-of-office back at us.

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
