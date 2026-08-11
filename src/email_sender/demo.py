"""Simulate three weeks of daily runs: python demo.py

No scraper and no mail server needed emails are printed, not sent.

to test cd into this src directory and run python3 demo.py
"""

import os
from datetime import datetime, timedelta, timezone

import notifier

DB = "demo.db"

# What the scraper "finds" on each day of the simulation.
# Day 1 is the first ever run, so it should establish a baseline silently.
FINDINGS = {
    1: [("PAGE_ADDED", "https://example.edu.au/p1", {"title": "Home"}),
        ("PAGE_ADDED", "https://example.edu.au/enrolment", {"title": "Enrolment"}),
        ("PAGE_ADDED", "https://example.edu.au/fees", {"title": "Fees"})],

    3: [("PAGE_CONTENT_CHANGED", "https://example.edu.au/enrolment",
         {"title": "Enrolment deadlines"}),
        ("FILE_CHANGED", "https://example.edu.au/docs/fees.pdf",
         {"filename": "2026-fee-schedule.pdf"}),
        ("PAGE_ADDED", "https://example.edu.au/news/sem2",
         {"title": "Semester 2 update"})],

    # Days 4-9 quiet. Day 10 should be the all-clear (7 days after day 3).
    12: [("FILE_ADDED", "https://example.edu.au/docs/handbook.pdf",
          {"filename": "handbook-2026.pdf"})],
    # Days 13-18 quiet. Day 19 should be the next all-clear.
}


def main():
    if os.path.exists(DB):
        os.remove(DB)
    conn = notifier.connect(DB)

    start = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)

    for day in range(1, 22):
        # Cron drifts a bit each morning; the heartbeat must cope with that.
        when = start + timedelta(days=day - 1, minutes=(day * 7) % 20)

        for type_, url, detail in FINDINGS.get(day, []):
            notifier.add_event(conn, type_, url, detail, when)

        action = notifier.run_notification_cycle(conn, when)
        verbose = action in ("digest", "all_clear")
        sent = notifier.send_pending(conn, when, dry_run=verbose)

        found = len(FINDINGS.get(day, []))
        print(f"  day {day:>2}  {when:%a %d %b %H:%M}  "
              f"found={found}  ->  {action:<10} emails={sent}")


if __name__ == "__main__":
    main()
