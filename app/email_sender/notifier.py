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

Anything that has to be remembered between runs -- when we last emailed, and
any report whose send failed -- lives in app/services/notification_service.py.
Keeping it out of here is what lets a report be rendered and checked without a
database, and what stops a dead mail server costing a day's changes.

Run `python -m app.email_sender.demo` to see it work without a scraper or a
mail server.

TODOs are marked below - see README
"""

from __future__ import annotations

import difflib
import re
import smtplib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from html import escape, unescape
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import config

# settings
# All from the shared Config in app/core/config.py, which reads the environment
# and .env for the whole project -- so there are no passwords in git and no
# second .env loader competing with the one the rest of the app uses. Copy
# .env.example to .env (see README). Defaults are example.com placeholders so
# the demo works with no setup.
#
# These stay module level constants rather than reads of `config` so a caller
# can still override one for a single run, and so the demo can be pointed at a
# different site without touching .env.

SITE_NAME = config.site_name
CLIENT_TO = config.client_to_addresses
FROM_ADDR = config.from_addr

# Real sending is opt in. A run with no .env, or a fresh checkout, prints its
# emails instead of mailing whatever CLIENT_TO happens to default to.
DRY_RUN = config.dry_run

# Times in the report are shown in the client's timezone, not UTC. The daily
# run happening at 06:00 UTC is 14:00 in Perth, and a report headed "checked
# 06:00" when the client reads it over lunch invites a support email.
REPORT_TIMEZONE = config.report_timezone

try:
    _REPORT_TZ = ZoneInfo(REPORT_TIMEZONE)
except (ZoneInfoNotFoundError, ValueError):
    # A typo in .env shouldn't stop the client's report going out.
    print(f"  unknown REPORT_TIMEZONE {REPORT_TIMEZONE!r}, falling back to UTC")
    _REPORT_TZ = UTC


def report_tz() -> ZoneInfo:
    """The timezone reports are written for, with the UTC fallback applied.

    Public so the scheduler can express the daily run time in the same zone the
    client reads timestamps in, without duplicating the fallback.
    """
    return _REPORT_TZ


def _local(when: datetime) -> datetime:
    """The same instant, in the timezone the report is written for.

    Display only. All the arithmetic stays in UTC, so a daylight saving jump
    can't move the heartbeat window.
    """
    return when.astimezone(_REPORT_TZ)

HEARTBEAT_DAYS = 7
# The daily job never fires at exactly the same second, so "7 days since the
# last email" would fail by a few seconds and slip to day 8. Allow a margin.
HEARTBEAT_SLACK_HOURS = 12
HEARTBEAT_DUE = timedelta(days=HEARTBEAT_DAYS, hours=-HEARTBEAT_SLACK_HOURS)

SMTP_HOST = config.smtp_host
SMTP_PORT = config.smtp_port
# EMAIL / EMAIL_PASSWORD are the names already used in .env.example, and the
# same two fields the rest of the app reads off Config.
SMTP_USER = config.email
SMTP_PASS = config.email_password.get_secret_value()


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

    `old_text` and `new_text` are the page or document text before and after,
    and are what the side by side diff in the email is built from. They are
    optional: a page that was added or removed has nothing to compare, and a
    change that arrives without them still emails fine, just without a diff.

    TODO (diff finder): nothing populates old_text/new_text yet. The diff
    finder has both versions in hand at the moment it decides a page changed,
    so it is the one that has to pass them through. Until it does, content
    changes email as a bare "this page changed" line.
    """

    type: ChangeType
    url: str
    label: str | None = None
    old_text: str | None = None
    new_text: str | None = None

    def __post_init__(self):
        if self.type not in get_args(ChangeType):
            raise ValueError(f"unknown change type: {self.type}")

    @property
    def has_diff(self) -> bool:
        """Whether there's enough here to show a before/after comparison."""
        return self.old_text is not None and self.new_text is not None

    def to_dict(self) -> dict[str, Any]:
        """A plain dict, for parking this change when a send fails.

        Serialisation, not state -- the notifier still remembers nothing. The
        scheduler is the one that stores the result and hands it back.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Change:
        """Rebuild a Change parked by to_dict().

        Unknown keys are dropped rather than raising: a parked change written
        by an older version of this file should still be sendable after a
        deploy, otherwise an upgrade silently strands the retry queue.
        """
        fields = {"type", "url", "label", "old_text", "new_text"}
        return cls(**{key: value for key, value in data.items() if key in fields})


#  working out what changed inside a page

# A whole page is far too much to email. Show only the parts that differ, plus
# a little unchanged text either side so the client can see where they sit.
DIFF_CONTEXT_LINES = 2
# Hard cap, so one rewritten page can't produce a megabyte long email.
DIFF_MAX_ROWS = 40


def _split_lines(text: str) -> list[str]:
    """Page text into comparable lines, blank ones dropped.

    Scraped text is full of blank lines that shift around whenever the markup
    is touched. Keeping them makes the diff look enormous when almost nothing
    has actually changed.
    """
    return [line.strip() for line in text.splitlines() if line.strip()]


def _mark_words(old_line: str, new_line: str) -> tuple[str, str]:
    """Wrap the words that differ between two lines in <del>/<ins>. Returns HTML.

    Line level highlighting alone tells the client "this line changed" and
    leaves them hunting for the word. This points at it.
    """
    old_words = old_line.split()
    new_words = new_line.split()
    matcher = difflib.SequenceMatcher(None, old_words, new_words)

    left, right = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_part = escape(" ".join(old_words[i1:i2]))
        new_part = escape(" ".join(new_words[j1:j2]))
        if tag == "equal":
            left.append(old_part)
            right.append(new_part)
            continue
        if old_part:
            left.append(f'<del style="background:#ffd7d5;text-decoration:none">{old_part}</del>')
        if new_part:
            right.append(f'<ins style="background:#ccffd8;text-decoration:none">{new_part}</ins>')

    return " ".join(left), " ".join(right)


def diff_rows(old_text: str, new_text: str, context: int = DIFF_CONTEXT_LINES) -> list[tuple[str, str, str]]:
    """Side by side rows of (kind, left_html, right_html).

    `kind` is "equal", "changed", "removed", "added" or "skip" -- "skip" being
    the "... unchanged text ..." marker where a long identical stretch was cut
    out. Escaping happens here, so callers can drop the result straight into a
    table.

    Truncated at DIFF_MAX_ROWS with a final row saying how much was left out.
    """
    old_lines = _split_lines(old_text)
    new_lines = _split_lines(new_text)

    rows: list[tuple[str, str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag == "equal":
            block = old_lines[i1:i2]
            if len(block) <= context * 2:
                keep = [(n, n) for n in block]
                skipped = 0
            else:
                keep = [(n, n) for n in block[:context]]
                skipped = len(block) - context * 2
                keep += [(n, n) for n in block[-context:]]
            for k, (left, right) in enumerate(keep):
                if skipped and k == context:
                    rows.append(("skip", f"... {skipped} unchanged lines ...", f"... {skipped} unchanged lines ..."))
                rows.append(("equal", escape(left), escape(right)))
            continue

        if tag == "replace":
            # Pair the lines up so the client reads before against after.
            for left, right in zip(old_lines[i1:i2], new_lines[j1:j2], strict=False):
                rows.append(("changed", *_mark_words(left, right)))
            # Uneven blocks leave a tail on one side with nothing to pair to.
            extra_old = old_lines[i1 + min(i2 - i1, j2 - j1) : i2]
            extra_new = new_lines[j1 + min(i2 - i1, j2 - j1) : j2]
            rows += [("removed", escape(line), "") for line in extra_old]
            rows += [("added", "", escape(line)) for line in extra_new]
        elif tag == "delete":
            rows += [("removed", escape(line), "") for line in old_lines[i1:i2]]
        elif tag == "insert":
            rows += [("added", "", escape(line)) for line in new_lines[j1:j2]]

    if len(rows) > DIFF_MAX_ROWS:
        dropped = len(rows) - DIFF_MAX_ROWS
        rows = rows[:DIFF_MAX_ROWS]
        rows.append(("skip", f"... {dropped} more rows, see the page itself ...", ""))
    return rows


#  writing the email
# Reports go out multipart: an HTML part and a plain text part carrying the
# same information. The text part is never dropped -- some corporate mail
# clients mangle HTML-only email.


def _unified_lines(change: Change) -> list[str]:
    """Before/after for the plain text part, stacked rather than side by side."""
    if not change.has_diff:
        return []

    out = []
    for kind, left, right in diff_rows(change.old_text, change.new_text):
        if kind == "equal":
            continue
        if kind == "skip":
            out.append(left)
        elif kind == "changed":
            out.append(f"- {_strip_tags(left)}")
            out.append(f"+ {_strip_tags(right)}")
        elif kind == "removed":
            out.append(f"- {_strip_tags(left)}")
        else:
            out.append(f"+ {_strip_tags(right)}")
    return out


def _strip_tags(html_fragment: str) -> str:
    """Undo the escaping and word marking diff_rows() did, for the text part."""
    text = re.sub(r"<[^>]+>", "", html_fragment)
    return unescape(text)


def render_digest(changes: list[Change], now: datetime, site_name: str | None = None) -> tuple[str, str]:
    """Build (subject, body) for the "what changed" email.

    Buckets the changes by type and prints them in SECTIONS order, skipping
    empty ones.

    `site_name` names the site this report is about. The database models many
    websites, and one report per site reads far better than a single digest
    that mixes them, so the caller says which one rather than every report
    being stamped with a single global. Defaults to SITE_NAME.

    No db and no network, so call it directly when you're fiddling with wording.
    """
    site_name = site_name or SITE_NAME
    n = len(changes)
    noun = "change" if n == 1 else "changes"
    local = _local(now)
    subject = f"[{site_name}] {n} {noun} detected - {local:%d %b %Y}"

    lines = [
        f"Website monitoring report for {site_name}",
        f"Checked {local:%d %b %Y, %H:%M %Z}",
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
            # Side by side needs two columns and plain text only has ~78
            # characters to play with, so the text part shows the same edits
            # stacked instead. The HTML part carries the real comparison.
            for line in _unified_lines(change):
                lines.append(f"      {line}")
        lines.append("")

    lines.append("This is an automated message.")
    return subject, "\n".join(lines)


# Inline styles throughout, not a <style> block: several mail clients strip
# stylesheets out of the head, and Gmail's web client is one of them.
_TD = "padding:6px 8px;vertical-align:top;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;width:50%"
_ROW_BG = {"changed": "#fffbe6", "removed": "#ffeceb", "added": "#eaffee", "skip": "#f6f8fa", "equal": "#ffffff"}


def _link(url: str) -> str:
    """A clickable link for http(s) URLs, plain escaped text for anything else.

    Page text and hrefs come off a third party website, so they're untrusted.
    Everything gets escaped, and this additionally refuses to build an <a href>
    around a `javascript:` or `data:` URL. The scraper filters those already;
    this is the second lock on the same door.
    """
    safe = escape(url, quote=True)
    if url.lower().startswith(("http://", "https://")):
        return f'<a href="{safe}" style="color:#0969da;font-size:12px">{safe}</a>'
    return f'<span style="color:#57606a;font-size:12px">{safe}</span>'


def _diff_table(change: Change) -> str:
    """The side by side before/after table for one changed page."""
    rows = diff_rows(change.old_text, change.new_text)
    # All "equal" means the only differences were blank lines or whitespace,
    # which _split_lines drops. A table of identical rows helps nobody.
    if not any(kind not in ("equal", "skip") for kind, _, _ in rows):
        return '<p style="margin:4px 0;color:#57606a;font-size:13px">Text changed, but no visible difference.</p>'

    cells = []
    for kind, left, right in rows:
        colour = "#57606a" if kind == "skip" else "#1f2328"
        cells.append(
            f'<tr style="background:{_ROW_BG[kind]}">'
            f'<td style="{_TD};color:{colour};border-right:1px solid #d0d7de">{left}&nbsp;</td>'
            f'<td style="{_TD};color:{colour}">{right}&nbsp;</td>'
            f"</tr>"
        )

    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" '
        'style="width:100%;max-width:100%;border-collapse:collapse;border:1px solid #d0d7de;margin:8px 0 18px">'
        '<tr style="background:#f6f8fa">'
        '<th style="{h};border-right:1px solid #d0d7de">Before</th>'
        '<th style="{h}">After</th>'
        "</tr>{rows}</table>"
    ).format(
        h="padding:6px 8px;text-align:left;font:600 12px/1.4 system-ui,sans-serif;color:#57606a",
        rows="".join(cells),
    )


def render_digest_html(changes: list[Change], now: datetime, site_name: str | None = None) -> str:
    """The HTML half of the digest -- same content as the plain text, plus diffs.

    Sent alongside render_digest()'s text, never instead of it. Mail clients
    that refuse HTML still get a readable report.
    """
    site_name = site_name or SITE_NAME
    n = len(changes)
    noun = "change" if n == 1 else "changes"

    out = [
        '<div style="font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#1f2328;max-width:900px">',
        f'<h2 style="margin:0 0 4px;font-size:18px">Website monitoring report for {escape(site_name)}</h2>',
        f'<p style="margin:0 0 16px;color:#57606a;font-size:13px">'
        f"Checked {_local(now):%d %b %Y, %H:%M %Z} &middot; "
        f"{n} {noun} detected since the last report.</p>",
    ]

    by_type: dict[str, list[Change]] = {}
    for change in changes:
        by_type.setdefault(change.type, []).append(change)

    for type_, heading in SECTIONS:
        items = by_type.get(type_)
        if not items:
            continue
        out.append(
            f'<h3 style="margin:20px 0 8px;font-size:15px;border-bottom:1px solid #d0d7de;'
            f'padding-bottom:4px">{escape(heading)} ({len(items)})</h3>'
        )
        for change in items:
            label = escape(change.label or change.url)
            out.append(
                f'<p style="margin:10px 0 2px"><strong>{label}</strong><br>{_link(change.url)}</p>'
            )
            if change.has_diff:
                out.append(_diff_table(change))

    out.append('<p style="margin:24px 0 0;color:#57606a;font-size:12px">This is an automated message.</p></div>')
    return "".join(out)


def render_all_clear(now: datetime, since: datetime, site_name: str | None = None) -> tuple[str, str]:
    """Build (subject, body) for the weekly "nothing changed" note.

    `since` is when we last emailed, so the body can name the window. Point of
    this one: silence shouldn't look the same as a broken scraper.
    """
    site_name = site_name or SITE_NAME
    local = _local(now)
    since_local = _local(since)
    subject = f"[{site_name}] No changes detected - week to {local:%d %b %Y}"
    body = "\n".join(
        [
            f"Website monitoring report for {site_name}",
            f"Checked {local:%d %b %Y, %H:%M %Z}",
            "",
            f"No changes detected between {since_local:%d %b %Y} and {local:%d %b %Y}.",
            "",
            "Monitoring is running normally.",
            "",
            "This is an automated message.",
        ]
    )
    return subject, body


def _sender_domain() -> str | None:
    """Domain of FROM_ADDR, for the Message-ID. None if it can't be read."""
    _, address = parseaddr(FROM_ADDR)
    _, _, domain = address.partition("@")
    return domain or None


def build_message(
    subject: str,
    body: str,
    html_body: str | None = None,
    now: datetime | None = None,
    recipients: list[str] | None = None,
) -> EmailMessage:
    """Wrap finished text in an email envelope. No sending.

    With `html_body` the result is multipart/alternative: clients that render
    HTML get the side by side diffs, and everything else falls back to `body`.
    The text part is never dropped -- some corporate mail clients make a mess
    of HTML-only email, and the client's might be one of them.

    `recipients` defaults to CLIENT_TO. It is a parameter so that different
    sites can eventually go to different people without this module growing a
    lookup of its own.

    The Auto-Submitted header tells other mail systems we're a bot, so we don't
    get out-of-office replies bouncing back.
    """
    now = now or datetime.now(UTC)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM_ADDR
    msg["To"] = ", ".join(recipients or CLIENT_TO)
    msg["Auto-Submitted"] = "auto-generated"
    # Date and Message-ID are not optional. RFC 5322 requires Date, and spam
    # filters treat a missing Message-ID as a strong signal of a bulk sender --
    # neither is added for us, so a report without them risks the junk folder.
    # The Message-ID domain matches the sending address for the same reason.
    msg["Date"] = formatdate(now.timestamp())
    msg["Message-ID"] = make_msgid(domain=_sender_domain())
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


#  sending


def send_message(msg: EmailMessage, dry_run: bool | None = None) -> bool:
    """Hand one message to the mail server. Returns whether it went out.

    dry_run=True just prints it, no mail server. demo.py uses it. Left as None
    it follows the DRY_RUN setting, which defaults to printing -- so nothing
    reaches a real inbox until someone sets DRY_RUN=false in .env.

    Returns False rather than raising: a mail server having a bad morning
    shouldn't take the whole daily run down with it. The caller decides what a
    failure costs -- app/services/notification_service.py parks the changes and
    retries them with a backoff, so a dead mail server no longer loses a day.
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
    site_name: str | None = None,
    recipients: list[str] | None = None,
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

    `site_name` and `recipients` say which site this report covers and who it
    goes to, both defaulting to the configured single-site values. They are
    arguments for the same reason `last_email_at` is: the database models many
    websites, and the notifier has no business looking any of that up.

    Pass `now` to fake the clock in tests. `dry_run` left as None follows the
    DRY_RUN setting; pass True or False to override it for one call.
    """
    now = now or datetime.now(UTC)

    if changes:
        subject, body = render_digest(changes, now, site_name)
        html_body = render_digest_html(changes, now, site_name)
        action = "digest"
    elif last_email_at is not None and now - last_email_at >= HEARTBEAT_DUE:
        subject, body = render_all_clear(now, last_email_at, site_name)
        html_body = None
        action = "all_clear"
    else:
        return "nothing"

    message = build_message(subject, body, html_body, now=now, recipients=recipients)
    return action if send_message(message, dry_run=dry_run) else "failed"
