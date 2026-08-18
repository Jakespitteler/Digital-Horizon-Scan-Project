"""Background scheduler: drives the notification run, and the scrape once it exists.

Run it with either of:

    PYTHONPATH=src python -m scheduler.background_scheduler
    python src/scheduler/background_scheduler.py
"""

import asyncio
import logging
import sys
from pathlib import Path

from pydantic import BaseModel, Field

try:
    from email_sender import notifier
except ModuleNotFoundError:
    # Running this file directly puts src/scheduler on sys.path rather than
    # src/, so its sibling packages aren't importable. Add src/ and retry.
    #
    # TODO: the real fix is `packages` in pyproject.toml. It says ["src"],
    # which installs this project as `src.scheduler` and leaves `email_sender`
    # unimportable either way. That file is shared with the scraper and
    # database work, so it needs a team decision rather than a unilateral fix.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from email_sender import notifier

log = logging.getLogger(__name__)


class SchedulerConfig(BaseModel):
    scrape_interval_seconds: int = Field(gt=0)
    notification_interval_seconds: int = Field(gt=0)


async def run_loop(interval_seconds: int, task_function):
    """Run task_function forever, waiting interval_seconds between goes.

    A task that raises is logged and the loop carries on. Without that, one bad
    morning -- a network blip, a database hiccup -- ends monitoring for good and
    says nothing: the exception unwinds out of here and nobody restarts it.

    Only Exception is caught, so asyncio.CancelledError still stops the loop
    and Ctrl-C keeps working.

    TODO (drift): the wait starts after the task finishes, so the true period is
    interval + however long the run took. At daily intervals that walks the
    report a little later every day. Fixing it properly means scheduling to a
    wall clock time, which is part of the once-a-day decision below.
    """
    name = getattr(task_function, "__name__", repr(task_function))
    while True:
        try:
            await task_function()
        except Exception:
            log.exception("scheduled task %s failed; carrying on", name)
        await asyncio.sleep(interval_seconds)


async def check_notifications():
    """One notification run: collect the day's changes and hand them to the notifier."""
    # TODO: wire in the scraper and the diff finder. The diff finder produces
    # the Change list the notifier packages up; until it exists there is
    # nothing to report and this stays quiet.
    changes: list[notifier.Change] = []

    # TODO: last_email_at has to survive between runs for the weekly all clear
    # to ever fire -- None means it never does. The notifier keeps no state by
    # design, so whoever owns the database has to hand it in here.
    last_email_at = None

    # notify() blocks: smtplib waits up to 30s on an unresponsive server, which
    # would freeze the whole event loop -- including the scrape loop once it is
    # added below. to_thread keeps it off the loop.
    #
    # dry_run is left to the DRY_RUN setting rather than hardcoded, so this
    # prints on a fresh checkout and only mails anyone once .env says to.
    action = await asyncio.to_thread(notifier.notify, changes, last_email_at=last_email_at)

    if action == "failed":
        # TODO: those changes are gone at this point -- the diff finder has
        # already moved its snapshot on, so tomorrow's run won't see them.
        # Whoever owns persistence needs to hold them until the send is
        # confirmed, or park them for a retry. See README.
        log.error("notification send failed, %d change(s) dropped", len(changes))
    else:
        log.info("notification run: %s (%d change(s))", action, len(changes))


# TODO (scheduling): this still loops forever on short test intervals, and the
# intervals below are hardcoded rather than read from the environment like the
# notifier's settings are. It should run once a day. Group's current thinking is
# to start the program on machine startup and keep a `last_run` timestamp so a
# restart part-way through the day doesn't trigger a second check. Alternative
# is a one-shot run driven by cron. Needs a team decision before implementing,
# and settles the drift TODO in run_loop at the same time.
#
# NOTE: notifier.notify() has no guard of its own -- it emails whatever changes
# it is handed, on the spot, so whatever we pick has to do the once-a-day
# enforcement here.

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    config = SchedulerConfig(
        scrape_interval_seconds=5,
        notification_interval_seconds=10,
    )

    # TODO: Add the scraper loop here once the web scraper is implemented.
    # It goes alongside this one under asyncio.gather(), which is why the
    # blocking send in check_notifications() had to move off the event loop.

    await run_loop(
        config.notification_interval_seconds,
        check_notifications,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("scheduler stopped")
