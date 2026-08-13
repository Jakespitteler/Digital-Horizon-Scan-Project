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


async def fake_task():
    print("Policy check running...")

async def check_notifications():
    conn = notifier.connect()

    action = notifier.run_notification_cycle(conn)

    if action in ("digest", "all_clear"):
        notifier.send_pending(conn, dry_run=True)

async def main():
    config = SchedulerConfig(
        scrape_interval_seconds=5,
        notification_interval_seconds=10,
    )

    await asyncio.gather(
        run_loop(config.scrape_interval_seconds, fake_task),
        run_loop(config.notification_interval_seconds, check_notifications),
    )


if __name__ == "__main__":
    asyncio.run(main())