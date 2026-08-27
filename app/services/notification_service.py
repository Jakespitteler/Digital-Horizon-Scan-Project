"""Persistence and orchestration around the notifier.

The notifier is a pure function of its arguments: it is handed a list of
changes and told when we last emailed, and it remembers nothing. That is
deliberate -- it keeps the wording testable without a database and stops a mail
server outage reaching back up the pipeline. The cost is that *something* has
to do the remembering, and this is it.

Two facts get stored, both per website:

  notification_states       when we last ran, when we last emailed
  pending_notifications     reports whose send failed, waiting on a retry

Nothing here knows how to write an email, and the notifier knows nothing about
any of this.
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import config
from app.db import repository
from app.db.schema import DBNotificationState, DBPendingNotification, DBWebsite
from app.email_sender import notifier

logger: logging.Logger = logging.getLogger(__name__)


def _aware(when: datetime | None) -> datetime | None:
    """Re-attach UTC to a timestamp read back from the database.

    SQLite has no timezone-aware column type, so a datetime written as UTC
    comes back naive. Handing that to the notifier would raise on the
    `now - last_email_at` comparison, which would take the whole run down. All
    stored timestamps are UTC by convention, so this just says so again.
    """
    if when is None:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


class NotificationService:
    def __init__(self, session: Session):
        """
        Args:
            session: The database session.
        """
        self._db = session

    # ------------------------------------------------------------------
    # Notification state
    # ------------------------------------------------------------------

    def get_state(self, website_id: uuid.UUID) -> DBNotificationState:
        """The notification state for a website, created empty if absent.

        Args:
            website_id: The website whose state is wanted.

        Returns:
            The website's notification state.
        """
        existing: Sequence[DBNotificationState] = repository.get_list(
            self._db, table=DBNotificationState, limit=1, attributes={"website_id": website_id}
        )
        if existing:
            return existing[0]

        state = DBNotificationState(website_id=website_id)
        repository.add(self._db, record=state)
        return state

    def last_email_at(self, website_id: uuid.UUID) -> datetime | None:
        """When we last emailed about this website, or None if we never have.

        This is the one value the notifier cannot work out for itself, and
        without it the weekly all-clear never fires.
        """
        return _aware(self.get_state(website_id).last_email_at)

    def has_run_since(self, website_id: uuid.UUID, cutoff: datetime) -> bool:
        """Whether a notification run has already happened since `cutoff`.

        The scheduler uses this so a restart part-way through the day does not
        trigger a second check.
        """
        last_run_at: datetime | None = _aware(self.get_state(website_id).last_run_at)
        return last_run_at is not None and last_run_at >= cutoff

    def record_run(self, website_id: uuid.UUID, now: datetime, action: str) -> DBNotificationState:
        """Write down that a run happened, and whether it emailed.

        `last_email_at` only moves when something actually went out. A run that
        found nothing still counts as a run, but it must not reset the weekly
        all-clear window -- otherwise a quiet site resets its own clock every
        morning and the all-clear never becomes due.

        Args:
            website_id: The website the run was for.
            now: When the run happened.
            action: What notify() returned.

        Returns:
            The updated notification state.
        """
        state: DBNotificationState = self.get_state(website_id)
        updates: dict[str, object] = {"last_run_at": now, "last_action": action}
        if action in ("digest", "all_clear"):
            updates["last_email_at"] = now
        return repository.update(self._db, record=state, updates=updates)

    # ------------------------------------------------------------------
    # Failed sends
    # ------------------------------------------------------------------

    def park(self, website_id: uuid.UUID, changes: list[notifier.Change], now: datetime) -> DBPendingNotification:
        """Store a report whose send failed, so it can be retried.

        Args:
            website_id: The website the report was for.
            changes: The changes that failed to send.
            now: When the send was attempted.

        Returns:
            The parked notification.
        """
        pending = DBPendingNotification(
            website_id=website_id,
            changes=[change.to_dict() for change in changes],
            attempts=1,
            last_attempt_at=now,
            next_attempt_at=now + self._backoff(1),
        )
        repository.add(self._db, record=pending)
        logger.warning(
            "parked %d change(s) for retry after a failed send. %s, next attempt %s",
            len(changes),
            f"{website_id=}",
            pending.next_attempt_at,
        )
        return pending

    def due_pending(self, now: datetime, limit: int = 100) -> list[DBPendingNotification]:
        """Parked reports whose next attempt is due.

        Args:
            now: The current time.
            limit: The maximum number to return.

        Returns:
            The parked reports ready to retry, oldest attempt first.
        """
        candidates: Sequence[DBPendingNotification] = repository.get_list(
            self._db, table=DBPendingNotification, limit=limit
        )
        due = [record for record in candidates if (_aware(record.next_attempt_at) or now) <= now]
        return sorted(due, key=lambda record: _aware(record.next_attempt_at) or now)

    def _backoff(self, attempts: int) -> timedelta:
        """How long to wait before attempt number `attempts` + 1.

        Exponential from retry_base_delay_seconds, capped so the gap between
        tries never grows past retry_max_delay_seconds.
        """
        seconds = config.retry_base_delay_seconds * (2 ** max(attempts - 1, 0))
        return timedelta(seconds=min(seconds, config.retry_max_delay_seconds))

    def record_retry_failure(self, pending: DBPendingNotification, now: datetime) -> bool:
        """Note that a retry failed. Returns whether it will be tried again.

        Gives up after retry_max_attempts and logs an error rather than
        retrying forever in silence -- at that point a person needs to look at
        it, and a queue that never drains is worse than a loud failure.

        Args:
            pending: The parked report whose retry failed.
            now: When the retry was attempted.

        Returns:
            True if it stays queued, False if it was given up on and deleted.
        """
        attempts: int = pending.attempts + 1
        if attempts >= config.retry_max_attempts:
            logger.error(
                "giving up on %d change(s) for website_id=%s after %d failed sends; they will not be reported",
                len(pending.changes),
                pending.website_id,
                attempts,
            )
            repository.delete(self._db, table=DBPendingNotification, id=pending.id)
            return False

        repository.update(
            self._db,
            record=pending,
            updates={
                "attempts": attempts,
                "last_attempt_at": now,
                "next_attempt_at": now + self._backoff(attempts),
            },
        )
        return True

    def discard(self, pending: DBPendingNotification) -> None:
        """Drop a parked report, once it has gone out."""
        repository.delete(self._db, table=DBPendingNotification, id=pending.id)

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    def run_for_website(
        self,
        website: DBWebsite,
        changes: list[notifier.Change],
        now: datetime | None = None,
        dry_run: bool | None = None,
    ) -> str:
        """One notification run for one website: send, then record the outcome.

        Args:
            website: The website being reported on.
            changes: What the diff finder found for it this run.
            now: The run time. Defaults to now.
            dry_run: Overrides the DRY_RUN setting for this call.

        Returns:
            What notify() returned: "digest", "all_clear", "nothing" or "failed".
        """
        now = now or datetime.now(UTC)
        action: str = notifier.notify(
            changes,
            now=now,
            last_email_at=self.last_email_at(website.id),
            dry_run=dry_run,
            site_name=website.url,
        )

        if action == "failed" and changes:
            self.park(website.id, changes, now)
        elif action == "failed":
            # An all-clear that failed to send is not worth parking: it carries
            # no changes, and next week's will say the same thing.
            logger.warning("all-clear send failed for website_id=%s, not parking", website.id)

        self.record_run(website.id, now, action)
        return action

    def retry_pending(self, now: datetime | None = None, dry_run: bool | None = None) -> tuple[int, int]:
        """Try to send everything that is due for a retry.

        Args:
            now: The current time. Defaults to now.
            dry_run: Overrides the DRY_RUN setting for these calls.

        Returns:
            (sent, still_queued).
        """
        now = now or datetime.now(UTC)
        sent = 0
        still_queued = 0

        for pending in self.due_pending(now):
            website: DBWebsite = repository.get(self._db, table=DBWebsite, id=pending.website_id)
            changes: list[notifier.Change] = [notifier.Change.from_dict(item) for item in pending.changes]
            action: str = notifier.notify(
                changes,
                now=now,
                last_email_at=self.last_email_at(website.id),
                dry_run=dry_run,
                site_name=website.url,
            )

            if action == "failed":
                if self.record_retry_failure(pending, now):
                    still_queued += 1
                continue

            self.discard(pending)
            self.record_run(website.id, now, action)
            sent += 1
            logger.info("retry sent %d parked change(s) for website_id=%s", len(changes), website.id)

        return sent, still_queued
