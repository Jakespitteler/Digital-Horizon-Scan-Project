import asyncio

from pydantic import BaseModel, Field

from email_sender import notifier


class SchedulerConfig(BaseModel):
    scrape_interval_seconds: int = Field(gt=0)
    notification_interval_seconds: int = Field(gt=0)


async def run_loop(interval_seconds: int, task_function):
    while True:
        await task_function()
        await asyncio.sleep(interval_seconds)


#async def fake_task():
    #print("Policy check running...")

async def check_notifications():
    # TODO: wire in the scraper and the diff finder. The diff finder produces
    # the Change list the notifier packages up; until it exists there is
    # nothing to report and this stays quiet.
    changes: list[notifier.Change] = []

    # TODO: last_email_at has to survive between runs for the weekly all-clear
    # to ever fire -- passing None means it never does. The notifier keeps no
    # state by design, so whatever owns persistence has to hand it in here.
    # Same open question as the scheduling note below.
    #
    # dry_run is left to the DRY_RUN setting rather than hardcoded, so this
    # prints on a fresh checkout and only mails anyone once .env says to.
    notifier.notify(changes, last_email_at=None)

# TODO (scheduling): this still loops forever on short test intervals. It should
# run once a day. Group's current thinking is to start the program on machine
# startup and keep a `last_run` timestamp so a restart part-way through the day
# doesn't trigger a second check. Alternative is a one-shot run driven by cron.
# Needs a team decision before implementing.
#
# NOTE: notifier.notify() has no guard of its own -- it emails whatever changes
# it is handed, on the spot, so whatever we pick has to do the once-a-day
# enforcement here.

async def main():
    config = SchedulerConfig(
        scrape_interval_seconds=5,
        notification_interval_seconds=10,
    )

    #TO DO: Add the scraper loop here once the web scraper is implemented.

    await run_loop(
        config.notification_interval_seconds,
        check_notifications,
    )


if __name__ == "__main__":
    asyncio.run(main())