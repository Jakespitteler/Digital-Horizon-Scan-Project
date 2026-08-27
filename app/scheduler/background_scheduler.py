"""Background scheduler: drives the notification run, and the scrape once it exists.

Run it with:

    python -m app.scheduler.background_scheduler

The notification run happens once a day at a wall clock time (DAILY_RUN_HOUR /
DAILY_RUN_MINUTE, in REPORT_TIMEZONE) rather than on a repeating interval. An
interval loop drifts: the wait starts after the task finishes, so the true
period is interval + however long the run took, and at daily intervals that
walks the client's report a little later every day. Scheduling against the
clock has no drift to accumulate.

Because it is time-of-day driven rather than "every 24 hours since boot",
restarts are safe: `last_run_at` is stored per website, so a process that comes
back up at lunchtime does not fire a second report for a day already covered.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import config
from app.db import repository
from app.db.core import SessionLocal
from app.db.schema import DBWebsite
from app.email_sender import notifier
from app.services.notification_service import NotificationService

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Scheduling helpers
# ----------------------------------------------------------------------


def _slot_on(day: datetime, hour: int, minute: int) -> datetime:
    """The scheduled instant on `day`, which must already be in the report tz."""
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def last_slot_at(now: datetime, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    """The most recent scheduled time at or before `now`, in UTC.

    The run guard compares against this: a website whose last run is at or
    after it has already been covered for the current day.
    """
    local: datetime = now.astimezone(tz)
    slot: datetime = _slot_on(local, hour, minute)
    if slot > local:
        slot = _slot_on(local - timedelta(days=1), hour, minute)
    return slot.astimezone(UTC)


def next_slot_at(now: datetime, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    """The next scheduled time strictly after `now`, in UTC.

    Computed from the wall clock every time rather than by adding 24 hours to
    the last run, so the report cannot drift and a daylight saving change moves
    it by an hour exactly once instead of permanently.
    """
    local: datetime = now.astimezone(tz)
    slot: datetime = _slot_on(local, hour, minute)
    if slot <= local:
        slot = _slot_on(local + timedelta(days=1), hour, minute)
    return slot.astimezone(UTC)


async def _run_guarded(task_function: Callable[[], Awaitable[None]]) -> None:
    """Run a task, logging anything it raises rather than letting it escape.

    Without this, one bad morning -- a network blip, a database hiccup -- ends
    monitoring for good and says nothing: the exception unwinds out of the loop
    and nobody restarts it.

    Only Exception is caught, so asyncio.CancelledError still stops the loop
    and Ctrl-C keeps working.
    """
    name: str = getattr(task_function, "__name__", repr(task_function))
    try:
        await task_function()
    except Exception:
        log.exception("scheduled task %s failed; carrying on", name)


async def run_loop(interval_seconds: int, task_function: Callable[[], Awaitable[None]]) -> None:
    """Run task_function forever, one go every interval_seconds.

    The time the task itself took is subtracted from the wait, so the period
    stays put instead of stretching by the task's duration each time. A task
    that overruns its interval simply starts the next go immediately.

    Used for the scrape loop, which just needs to happen regularly. The
    notification run uses run_daily_at() instead, because the client reads a
    timestamp off it.
    """
    while True:
        started: float = asyncio.get_running_loop().time()
        await _run_guarded(task_function)
        elapsed: float = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.0, interval_seconds - elapsed))


async def run_daily_at(
    hour: int,
    minute: int,
    tz: ZoneInfo,
    task_function: Callable[[], Awaitable[None]],
) -> None:
    """Run task_function once a day at a wall clock time.

    Fires once on entry so a process started after the day's slot still covers
    that day; the task's own per-website guard makes that a no-op if the run
    already happened.
    """
    await _run_guarded(task_function)
    while True:
        now: datetime = datetime.now(UTC)
        target: datetime = next_slot_at(now, hour, minute, tz)
        log.info("next notification run at %s", target.astimezone(tz))
        await asyncio.sleep((target - now).total_seconds())
        await _run_guarded(task_function)


# ----------------------------------------------------------------------
# The notification run
# ----------------------------------------------------------------------


def collect_changes(session: Session, website: DBWebsite) -> list[notifier.Change]:
    """What the diff finder found for this website since the last run.

    TODO (diff finder): this is the seam the diff finder plugs into. It holds
    both versions of a page at the moment it decides one changed, so it is the
    one that can fill in Change.old_text / Change.new_text and give the client
    a real before/after. Until it exists there is nothing to report and the run
    stays quiet -- which is also why nothing is emailed on a fresh database.
    """
    return []


def run_notifications(session: Session, now: datetime | None = None) -> dict[str, int]:
    """One notification pass over every website. Blocking; call via to_thread.

    Retries anything parked by a previous failed send first, then runs today's
    report for each website that has not already had one.

    Args:
        session: The database session.
        now: The run time. Defaults to now.

    Returns:
        A count of each action taken, for logging.
    """
    now = now or datetime.now(UTC)
    service = NotificationService(session)
    tally: dict[str, int] = {}

    sent, queued = service.retry_pending(now=now)
    if sent or queued:
        log.info("retried parked notifications: %d sent, %d still queued", sent, queued)

    cutoff: datetime = last_slot_at(now, config.daily_run_hour, config.daily_run_minute, notifier.report_tz())

    for website in repository.get_list(session, table=DBWebsite, limit=1000):
        if service.has_run_since(website.id, cutoff):
            log.debug("website_id=%s already reported since %s, skipping", website.id, cutoff)
            continue

        changes: list[notifier.Change] = collect_changes(session, website)
        action: str = service.run_for_website(website, changes, now=now)
        tally[action] = tally.get(action, 0) + 1

        if action == "failed":
            # Not lost any more -- run_for_website parked the changes and they
            # will go out on a later retry.
            log.error("notification send failed for %s, %d change(s) parked", website.url, len(changes))
        else:
            log.info("notification run for %s: %s (%d change(s))", website.url, action, len(changes))

    return tally


async def check_notifications() -> None:
    """One notification run, off the event loop.

    notify() blocks: smtplib waits up to 30s on an unresponsive server, and the
    database calls are synchronous too. Running that inline would freeze the
    whole loop, including the scrape loop once it is added.

    dry_run is left to the DRY_RUN setting rather than hardcoded, so this
    prints on a fresh checkout and only mails anyone once .env says to.
    """

    def _work() -> dict[str, int]:
        session: Session = SessionLocal()
        try:
            tally: dict[str, int] = run_notifications(session)
            session.commit()
            return tally
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    tally: dict[str, int] = await asyncio.to_thread(_work)
    if tally:
        log.info("notification pass complete: %s", tally)


# TODO (scraper): add the scrape loop here once the web scraper is implemented.
# It goes alongside the notification schedule under asyncio.gather(), which is
# why the blocking send in check_notifications() had to move off the event loop:
#
#     await asyncio.gather(
#         run_daily_at(...),
#         run_loop(config.scrape_interval_seconds, check_site),
#     )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    tz: ZoneInfo = notifier.report_tz()
    log.info(
        "scheduler started; daily notification run at %02d:%02d %s (dry_run=%s)",
        config.daily_run_hour,
        config.daily_run_minute,
        tz,
        notifier.DRY_RUN,
    )

    await run_daily_at(
        config.daily_run_hour,
        config.daily_run_minute,
        tz,
        check_notifications,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("scheduler stopped")
