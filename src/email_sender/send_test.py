"""Send one real sample report, to check the mail account actually works.

    cd src/email_sender
    python3 send_test.py

Unlike demo.py this talks to a real mail server, so it needs a filled in .env
with DRY_RUN=false (see README). It refuses to run if the settings still look
like the example.com placeholders, so a half configured .env fails loudly
rather than pretending to send.

Sends one digest with made up changes. Nothing is scraped and no state is kept
-- this only answers "can we get mail out of this account".
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    from email_sender import notifier
except ImportError:
    # Run as `python3 send_test.py` from inside this directory, sys.path[0] is
    # this folder rather than src/, so the package name isn't importable. Add
    # src/ and retry -- same guard as background_scheduler.py.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from email_sender import notifier

# Before and after text for the sample content change, so the test email
# actually shows the side by side diff rather than a bare "this page changed".
_BEFORE = """Enrolment deadlines for Semester 2, 2026
Applications close on 15 July 2026.
Late applications may be considered at the discretion of the faculty.
Students must complete the online form before the closing date.
A late fee of $150 applies to applications received after the deadline.
Contact the Student Centre for assistance."""

_AFTER = """Enrolment deadlines for Semester 2, 2026
Applications close on 1 August 2026.
Late applications may be considered at the discretion of the faculty.
Students must complete the online form before the closing date.
A late fee of $220 applies to applications received after the deadline.
Payment plans are available for students experiencing hardship.
Contact the Student Centre for assistance."""

SAMPLE = [
    notifier.Change(
        type="PAGE_CONTENT_CHANGED",
        url="https://example.edu.au/enrolment",
        label="Enrolment deadlines",
        old_text=_BEFORE,
        new_text=_AFTER,
    ),
    notifier.Change(
        type="FILE_ADDED",
        url="https://example.edu.au/docs/handbook.pdf",
        label="handbook-2026.pdf",
    ),
]


def check_configured() -> list[str]:
    """Return the reasons this can't send for real, empty if it's good to go."""
    problems = []
    if notifier.DRY_RUN:
        problems.append("DRY_RUN is on -- set DRY_RUN=false in .env to send for real")
    if not notifier.SMTP_USER:
        problems.append("EMAIL is empty -- set it to the account you're sending from")
    if not notifier.SMTP_PASS:
        problems.append("EMAIL_PASSWORD is empty -- Gmail needs an app password, not your login")
    if "example.com" in notifier.SMTP_HOST:
        problems.append(f"SMTP_HOST is still the placeholder ({notifier.SMTP_HOST})")
    if any("example.com" in a for a in notifier.CLIENT_TO):
        problems.append(f"CLIENT_TO is still the placeholder ({', '.join(notifier.CLIENT_TO)})")
    return problems


def main() -> int:
    problems = check_configured()
    if problems:
        print("Not configured to send:")
        for p in problems:
            print(f"  - {p}")
        print("\nSee the 'Sending real test email' section of the README.")
        return 1

    print(f"Sending a sample report as {notifier.SMTP_USER}")
    print(f"  via  {notifier.SMTP_HOST}:{notifier.SMTP_PORT}")
    print(f"  to   {', '.join(notifier.CLIENT_TO)}")

    action = notifier.notify(SAMPLE, now=datetime.now(UTC), dry_run=False)

    if action == "failed":
        print("\nFailed -- the error from the mail server is above.")
        return 1

    print(f"\nSent ({action}). Check the inbox, and the spam folder if it isn't there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
