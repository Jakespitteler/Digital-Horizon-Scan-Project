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

import difflib
import re
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from html import escape, unescape
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
# TODO: plain text only for now. HTML would look nicer, but we keep the plain
# text part regardless -- some corporate mail clients mangle HTML-only email.


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


def render_digest_html(changes: list[Change], now: datetime) -> str:
    """The HTML half of the digest -- same content as the plain text, plus diffs.

    Sent alongside render_digest()'s text, never instead of it. Mail clients
    that refuse HTML still get a readable report.
    """
    n = len(changes)
    noun = "change" if n == 1 else "changes"

    out = [
        '<div style="font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#1f2328;max-width:900px">',
        f'<h2 style="margin:0 0 4px;font-size:18px">Website monitoring report for {escape(SITE_NAME)}</h2>',
        f'<p style="margin:0 0 16px;color:#57606a;font-size:13px">Checked {now:%d %b %Y, %H:%M} UTC &middot; '
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


def build_message(subject: str, body: str, html_body: str | None = None) -> EmailMessage:
    """Wrap finished text in an email envelope. No sending.

    With `html_body` the result is multipart/alternative: clients that render
    HTML get the side by side diffs, and everything else falls back to `body`.
    The text part is never dropped -- some corporate mail clients make a mess
    of HTML-only email, and the client's might be one of them.

    The Auto-Submitted header tells other mail systems we're a bot, so we don't
    get out-of-office replies bouncing back.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM_ADDR
    msg["To"] = ", ".join(CLIENT_TO)
    msg["Auto-Submitted"] = "auto-generated"
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
        html_body = render_digest_html(changes, now)
        action = "digest"
    elif last_email_at is not None and now - last_email_at >= HEARTBEAT_DUE:
        subject, body = render_all_clear(now, last_email_at)
        html_body = None
        action = "all_clear"
    else:
        return "nothing"

    message = build_message(subject, body, html_body)
    return action if send_message(message, dry_run=dry_run) else "failed"
