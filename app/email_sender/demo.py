"""Simulate three weeks of daily runs: python demo.py

No scraper, no diff finder and no mail server -- emails are printed, not sent.

Doubles as a worked example of what has to sit around the notifier: the
notifier is handed a list of changes and told when we last emailed, so this
file plays the part of the caller and remembers `last_email_at` between runs.

to test cd into this src/email_sender directory and run python3 demo.py
"""

from datetime import UTC, datetime, timedelta

import notifier

# What the diff finder "reports" on each day of the simulation.
# Day 1 is the first ever run: there is no previous snapshot to compare
# against, so the diff finder reports nothing and no email goes out.
FINDINGS = {
    3: [("PAGE_CONTENT_CHANGED", "https://example.edu.au/enrolment", "Enrolment deadlines"),
        ("FILE_CHANGED", "https://example.edu.au/docs/fees.pdf", "2026-fee-schedule.pdf"),
        ("PAGE_ADDED", "https://example.edu.au/news/sem2", "Semester 2 update")],

    # Days 4-9 quiet. Day 10 should be the all-clear (7 days after day 3).
    12: [("FILE_ADDED", "https://example.edu.au/docs/handbook.pdf", "handbook-2026.pdf")],
    # Days 13-18 quiet. Day 19 should be the next all-clear.
}


def main():
    start = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)

    # Monitoring starts on day 1, so that's the clock the first all-clear
    # counts from. Real callers have to keep this between runs.
    last_email_at = start

    for day in range(1, 22):
        # Cron drifts a bit each morning; the heartbeat must cope with that.
        when = start + timedelta(days=day - 1, minutes=(day * 7) % 20)

        changes = [
            notifier.Change(type=type_, url=url, label=label)
            for type_, url, label in FINDINGS.get(day, [])
        ]

        action = notifier.notify(
            changes,
            now=when,
            last_email_at=last_email_at,
            dry_run=True,
        )
        if action in ("digest", "all_clear"):
            last_email_at = when

        print(f"  day {day:>2}  {when:%a %d %b %H:%M}  "
              f"found={len(changes)}  ->  {action}")


if __name__ == "__main__":
    main()
